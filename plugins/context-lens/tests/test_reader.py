import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from context_lens.reader import inspect_session, SessionStore


def rec(kind, payload):
    return {"type": kind, "timestamp": "2026-10-08T12:00:00Z", "payload": payload}


def message(role, text, id=None):
    return rec("response_item", {"type": "message", "role": role, "id": id, "content": [{"type": "input_text", "text": text}]})


def usage(input, output=5, cumulative=999999, source="token_usage_record"):
    exact = {"input_tokens": input, "cached_input_tokens": input // 2, "output_tokens": output, "total_tokens": input + output}
    total = {"input_tokens": cumulative, "total_tokens": cumulative + output}
    if source == "event_msg":
        return rec(source, {"type": "token_count", "info": {"last_token_usage": exact, "total_token_usage": total, "model_context_window": 10000}})
    return rec(source, {"usage": exact, "thread_token_usage": total})


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.path = self.root / "rollout-test.jsonl"
        self.start = [rec("session_meta", {"id": "12345678-1234-1234-1234-123456789012", "cwd": "/tmp/project", "base_instructions": {"text": "SYSTEM"}}),
                      rec("event_msg", {"type": "task_started", "turn_id": "turn-one", "model_context_window": 10000}),
                      rec("turn_context", {"turn_id": "turn-one", "model": "test-model"})]

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, records, tail=""):
        self.path.write_text("\n".join(json.dumps(r) for r in records) + "\n" + tail)

    def test_first_scan_at_near_zero_monotonic_clock(self):
        self.write(self.start + [usage(100)])
        with patch('context_lens.reader.time.monotonic', return_value=0.01):
            self.assertEqual(len(SessionStore(self.root).paths()), 1)

    def test_request_input_not_lifetime_or_output(self):
        self.write(self.start + [message("user", "HELLO"), message("assistant", "X" * 1000), usage(100, 50)])
        report = inspect_session(self.path)
        self.assertEqual(report["context"]["used"], 100)
        self.assertEqual(report["context"]["free"], 9900)
        self.assertEqual(sum(b["tokens"] for b in report["buckets"]), 100)
        self.assertEqual(next(b for b in report["buckets"] if b["key"] == "assistant")["tokens"], 0)

    def test_prior_assistant_retained_next_request(self):
        self.write(self.start + [message("user", "hello"), message("assistant", "A" * 100), usage(100), message("user", "next"), message("assistant", "B" * 200), usage(200)])
        report = inspect_session(self.path)
        assistant = next(b for b in report["buckets"] if b["key"] == "assistant")
        self.assertEqual(assistant["chars"], 100)

    def test_compaction_replacement_rebuilds_active_history(self):
        self.write(self.start + [message("user", "old" * 10000), usage(9000), rec("compacted", {"replacement_history": [{"type": "message", "role": "summary", "content": "short summary"}]}), message("user", "NEW"), usage(100)])
        report = inspect_session(self.path)
        self.assertEqual(report["context"]["compactions"], 1)
        self.assertLess(report["context"]["visible_estimated"], 20)
        self.assertEqual(next(b for b in report["buckets"] if b["key"] == "summary")["chars"], 13)

    def test_stale_usage_after_compaction_unknown(self):
        self.write(self.start + [usage(9000), rec("compacted", {"message": "summary"})])
        self.assertIsNone(inspect_session(self.path)["context"]["used"])

    def test_ciphertext_not_treated_as_plaintext(self):
        self.write(self.start + [rec("response_item", {"type": "reasoning", "encrypted_content": "x" * 100000, "summary": []}), usage(100)])
        self.assertEqual(inspect_session(self.path)["context"]["visible_estimated"], 2)

    def test_duplicate_usage_events_only_one_request(self):
        self.write(self.start + [message("user", "HI"), usage(100), usage(100, source="event_msg")])
        self.assertEqual(len(inspect_session(self.path)["requests"]), 1)

    def test_mixed_tags_counted_once_and_cap_conserves_total(self):
        self.write(self.start + [message("developer", "<skills_instructions>" + "s" * 1000 + "</skills_instructions>"), usage(100)])
        report = inspect_session(self.path)
        self.assertTrue(report["context"]["estimate_scaled"])
        self.assertEqual(sum(b["tokens"] for b in report["buckets"]), 100)

    def test_partial_line_does_not_drop_previous_usage(self):
        self.write(self.start + [usage(100)], '{"type":"response_item"')
        report = inspect_session(self.path)
        self.assertEqual(report["context"]["used"], 100)
        self.assertTrue(any("最后一行" in n for n in report["notes"]))

    def test_turn_specific_report_stays_historical(self):
        self.write(self.start + [message("user", "first"), message("assistant", "First reply", "msg-one"), usage(100),
                   rec("event_msg", {"type": "task_complete", "turn_id": "turn-one"}),
                   rec("event_msg", {"type": "task_started", "turn_id": "turn-two", "model_context_window": 10000}),
                   message("user", "second"), message("assistant", "Second reply", "msg-two"), usage(300),
                   rec("event_msg", {"type": "task_complete", "turn_id": "turn-two"})])
        store = SessionStore(self.root)
        sid = self.start[0]["payload"]["id"]
        self.assertEqual(store.report(sid, "turn-one")["context"]["used"], 100)
        self.assertEqual(store.report(sid, "turn-two")["context"]["used"], 300)
        self.assertEqual(store.report(sid)["turns"][0]["message_id"], "msg-one")

    def test_turn_tps_sums_requests_and_deduplicates_usage(self):
        first = usage(100, 40)
        first["payload"]["response_id"] = "response-one"
        second = usage(200, 60)
        second["payload"]["response_id"] = "response-two"
        self.write(self.start + [first, usage(100, 40, source="event_msg"), first,
                                second, usage(200, 60, source="event_msg"),
                                rec("event_msg", {"type": "task_complete", "turn_id": "turn-one",
                                                  "duration_ms": 10000, "time_to_first_token_ms": 2000})])
        performance = inspect_session(self.path)["turn_performance"]
        self.assertEqual(performance["output_tokens"], 100)
        self.assertEqual(performance["duration_ms"], 10000)
        self.assertEqual(performance["average_tps"], 10)

    def test_historical_tps_uses_its_own_completion_after_usage(self):
        self.write(self.start + [usage(100, 40), usage(200, 60),
                   rec("event_msg", {"type": "task_complete", "turn_id": "turn-one", "duration_ms": 10000}),
                   rec("event_msg", {"type": "task_started", "turn_id": "turn-two"}), usage(300, 200),
                   rec("event_msg", {"type": "task_complete", "turn_id": "turn-two", "duration_ms": 2000})])
        store = SessionStore(self.root)
        sid = self.start[0]["payload"]["id"]
        first, second = store.report(sid, "turn-one"), store.report(sid, "turn-two")
        self.assertEqual(first["turn_performance"]["average_tps"], 10)
        self.assertEqual(first["turn_performance"]["output_tokens"], 100)
        self.assertEqual(first["context"]["used"], 200)
        self.assertEqual(second["turn_performance"]["average_tps"], 100)
        from context_lens.overlay import trim_report
        self.assertEqual(trim_report(first)["turn_performance"], first["turn_performance"])

    def test_turn_tps_for_legacy_token_count_and_equal_usage_in_new_turn(self):
        self.write(self.start + [usage(100, 40),
                   rec("event_msg", {"type": "task_complete", "turn_id": "turn-one", "duration_ms": 2000}),
                   rec("event_msg", {"type": "task_started", "turn_id": "turn-two"}),
                   usage(100, 40, source="event_msg"), usage(200, 60, source="event_msg"),
                   rec("event_msg", {"type": "task_complete", "turn_id": "turn-two", "duration_ms": 10000})])
        self.assertEqual(inspect_session(self.path)["turn_performance"]["average_tps"], 10)

    def test_turn_tps_missing_invalid_duration_or_incomplete_stays_unknown(self):
        for duration in (None, 0, -1, True, "1000", float("inf"), float("nan")):
            with self.subTest(duration=duration):
                self.write(self.start + [usage(100, 40), rec("event_msg", {
                    "type": "task_complete", "turn_id": "turn-one", "duration_ms": duration})])
                self.assertIsNone(inspect_session(self.path)["turn_performance"]["average_tps"])
        self.write(self.start + [usage(100, 40)])
        self.assertIsNone(inspect_session(self.path)["turn_performance"]["average_tps"])

    def test_turn_tps_zero_output_is_zero_and_cache_refreshes_on_completion(self):
        records = self.start + [usage(100, 0)]
        self.write(records)
        store = SessionStore(self.root)
        sid = self.start[0]["payload"]["id"]
        self.assertIsNone(store.report(sid, "turn-one")["turn_performance"]["average_tps"])
        self.write(records + [rec("event_msg", {"type": "task_complete", "turn_id": "turn-one", "duration_ms": 1000})])
        self.assertEqual(store.report(sid, "turn-one")["turn_performance"]["average_tps"], 0)

    def test_turn_tps_missing_output_is_unknown_rather_than_zero(self):
        incomplete = rec("token_usage_record", {"usage": {"input_tokens": 100}})
        self.write(self.start + [usage(100, 40), incomplete, usage(200, 60), rec("event_msg", {
            "type": "task_complete", "turn_id": "turn-one", "duration_ms": 10000})])
        self.assertIsNone(inspect_session(self.path)["turn_performance"]["average_tps"])

    def test_matching_prefix_keeps_markdown_lines_and_complete_link(self):
        reply = '回复里包含[本地插件](/Users/' + 'folder/' * 40 + 'plugin.zip)。\n\n- 第一项分类\n- 第二项分类'
        self.write(self.start + [message('assistant', reply), usage(100),
                                rec('event_msg', {'type': 'task_complete', 'turn_id': 'turn-one'})])
        self.assertEqual(inspect_session(self.path)['turns'][0]['text_prefix'], reply)

    def test_arbitrary_file_path_not_a_session_selector(self):
        self.write(self.start + [usage(100)])
        with self.assertRaises(ValueError):
            SessionStore(self.root).report("/etc/passwd")

    def test_symlink_outside_sessions_not_read(self):
        self.write(self.start + [usage(100)])
        (self.root / "outside.jsonl").symlink_to("/etc/passwd")
        self.assertEqual(len(SessionStore(self.root).paths()), 1)

    def test_replayed_parent_metadata_does_not_replace_child_identity(self):
        child = rec("session_meta", {"id": "child-thread", "parent_thread_id": "parent-thread", "source": {"subagent": {}}})
        parent = rec("session_meta", {"id": "parent-thread", "cwd": "/tmp/parent"})
        self.write([child, parent] + self.start[1:] + [message("user", "hello"), usage(100)])
        store = SessionStore(self.root)
        self.assertEqual(store.header(self.path)["id"], "child-thread")
        self.assertTrue(store.header(self.path)["is_subagent"])
        self.assertEqual(store.report("child-thread")["session"]["id"], "child-thread")


if __name__ == "__main__":
    unittest.main()
