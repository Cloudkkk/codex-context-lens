import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from context_lens.reader import SessionStore
from context_lens.overlay import view_payload, view_report


def record(kind, payload):
    return json.dumps({'type': kind, 'payload': payload}) + '\n'


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = SessionStore(self.root)

    def session(self, sid, turns, **metadata):
        path = self.root / (sid + '.jsonl')
        path.write_text(record('session_meta', {'id': sid, **metadata}))
        for turn in turns:
            self.append(path, turn)
        return path

    def append(self, path, turn):
        with path.open('a') as stream:
            stream.write(record('event_msg', {'type': 'task_started', 'turn_id': turn, 'model_context_window': 10000}))
            stream.write(record('turn_context', {'turn_id': turn}))
            stream.write(record('response_item', {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'Same wording across different chats.'}]}))
            stream.write(record('token_usage_record', {'usage': {'input_tokens': 100, 'output_tokens': 5}}))
            stream.write(record('event_msg', {'type': 'task_complete', 'turn_id': turn}))

    def resolve(self, hint, turns):
        return self.store.resolve_view(hint, turns)

    def test_draft_alias_is_resolved_from_authoritative_turns(self):
        self.session('chat-a', ['turn-a', 'turn-a2'])
        result = self.resolve('client-new-thread:draft', ['turn-a', 'turn-a2'])
        self.assertEqual(result['session_id'], 'chat-a')
        self.assertEqual(result['reason'], 'turn_ids')

    def test_no_composer_or_sidebar_still_resolves_unique_visible_chat(self):
        self.session('chat-a', ['turn-a'])
        self.assertEqual(self.resolve(None, ['turn-a'])['session_id'], 'chat-a')

    def test_same_draft_alias_is_not_cached_across_two_chats(self):
        self.session('chat-a', ['turn-a'])
        self.session('chat-b', ['turn-b'])
        self.assertEqual(self.resolve('client-new-thread:reused', ['turn-a'])['session_id'], 'chat-a')
        self.assertEqual(self.resolve('client-new-thread:reused', ['turn-b'])['session_id'], 'chat-b')
        self.assertEqual(self.resolve('client-new-thread:reused', ['turn-a'])['session_id'], 'chat-a')

    def test_stale_sidebar_cannot_override_visible_real_turn(self):
        self.session('chat-a', ['turn-a'])
        self.session('chat-b', ['turn-b'])
        self.assertEqual(self.resolve('chat-a', ['turn-b'])['session_id'], 'chat-b')

    def test_empty_prewarmed_window_never_selects_latest(self):
        self.session('chat-a', ['turn-a'])
        self.assertIsNone(self.resolve(None, [])['session_id'])
        self.assertIsNone(self.resolve('client-new-thread:new', [])['session_id'])

    def test_legacy_view_with_exact_session_id_is_supported(self):
        self.session('chat-a', ['turn-a'])
        self.assertEqual(self.resolve('chat-a', [])['session_id'], 'chat-a')
        self.assertIsNone(self.resolve('chat-', [])['session_id'])

    def test_duplicate_turn_in_fork_is_ambiguous_without_exact_session(self):
        self.session('parent', ['common-turn'])
        self.session('fork', ['common-turn'])
        result = self.resolve('client-new-thread:fork', ['common-turn'])
        self.assertIsNone(result['session_id'])
        self.assertEqual(result['reason'], 'ambiguous_turn_session')
        self.assertEqual(self.resolve('fork', ['common-turn'])['session_id'], 'fork')

    def test_multiple_visible_chats_are_not_mixed(self):
        self.session('chat-a', ['turn-a'])
        self.session('chat-b', ['turn-b'])
        result = self.resolve('chat-a', ['turn-a', 'turn-b'])
        self.assertIsNone(result['session_id'])
        self.assertEqual(result['reason'], 'conflicting_turn_sessions')

    def test_pending_log_does_not_reuse_old_session(self):
        self.session('chat-a', ['turn-a'])
        result = self.resolve('chat-a', ['not-logged-yet'])
        self.assertIsNone(result['session_id'])
        self.assertEqual(result['reason'], 'waiting_for_turn_log')

    def test_partially_written_new_turn_is_waited_for_then_resolved(self):
        path = self.session('chat-a', ['turn-a'])
        self.resolve(None, ['turn-a'])
        pending = record('event_msg', {'type': 'task_started', 'turn_id': 'new-turn'})
        with path.open('a') as stream:
            stream.write(pending[:len(pending)//2])
        self.assertIsNone(self.resolve(None, ['new-turn'])['session_id'])
        with path.open('a') as stream:
            stream.write(pending[len(pending)//2:])
        self.assertEqual(self.resolve(None, ['new-turn'])['session_id'], 'chat-a')

    def test_concurrent_completion_in_existing_files_updates_shared_index(self):
        first = self.session('chat-a', ['turn-a'])
        second = self.session('chat-b', ['turn-b'])
        self.resolve(None, ['turn-a'])
        self.append(first, 'new-a')
        self.append(second, 'new-b')
        self.assertEqual(self.resolve('client-new-thread:a', ['new-a'])['session_id'], 'chat-a')
        self.assertEqual(self.resolve('client-new-thread:b', ['new-b'])['session_id'], 'chat-b')

    def test_new_session_is_discovered_after_scan_interval(self):
        with patch('context_lens.reader.time.monotonic', return_value=10):
            self.resolve(None, ['new-turn'])
        self.session('new-chat', ['new-turn'])
        with patch('context_lens.reader.time.monotonic', return_value=14):
            self.assertEqual(self.resolve(None, ['new-turn'])['session_id'], 'new-chat')

    def test_deleted_or_replaced_files_cannot_leave_stale_owners(self):
        path = self.session('chat-a', ['turn-a'])
        self.resolve(None, ['turn-a'])
        path.write_text(record('session_meta', {'id': 'chat-b'}) + record('turn_context', {'turn_id': 'turn-b'}))
        self.assertIsNone(self.resolve(None, ['turn-a'])['session_id'])
        self.assertEqual(self.resolve(None, ['turn-b'])['session_id'], 'chat-b')
        path.unlink()
        self.store._scan_at = None
        self.assertIsNone(self.resolve(None, ['turn-b'])['session_id'])

    def test_subagent_replayed_parent_turn_is_not_a_main_chat_owner(self):
        self.session('chat-a', ['turn-a'])
        self.session('child', ['turn-a'], parent_thread_id='chat-a', source={'subagent': {}})
        self.assertEqual(self.resolve(None, ['turn-a'])['session_id'], 'chat-a')

    def test_same_chat_and_different_chats_in_multiple_windows(self):
        self.session('chat-a', ['turn-a', 'turn-a2'])
        self.session('chat-b', ['turn-b'])
        views = [{'activeThreadId': 'client-new-thread:a', 'visibleTurnIds': ['turn-a'], 'viewToken': 'window-a'},
                 {'activeThreadId': None, 'visibleTurnIds': ['turn-a2'], 'viewToken': 'window-a2'},
                 {'activeThreadId': 'chat-b', 'visibleTurnIds': ['turn-b'], 'viewToken': 'window-b'}]
        payloads = [view_payload(self.store, view) for view in views]
        self.assertEqual([p['sessionId'] for p in payloads], ['chat-a', 'chat-a', 'chat-b'])
        self.assertEqual([p['viewToken'] for p in payloads], ['window-a', 'window-a2', 'window-b'])
        self.assertEqual([t['turn_id'] for t in payloads[2]['turns']], ['turn-b'])

    def test_unchanged_logs_are_not_reopened_for_another_window(self):
        path = self.session('chat-a', ['turn-a'])
        self.resolve(None, ['turn-a'])
        with patch.object(Path, 'open', side_effect=AssertionError('Unchanged files should remain indexed')):
            self.assertEqual(self.resolve('client-new-thread:another', ['turn-a'])['session_id'], 'chat-a')


class ViewRequestTests(unittest.TestCase):
    setUp = RoutingTests.setUp
    session = RoutingTests.session
    append = RoutingTests.append
    def test_hover_request_from_other_window_is_rejected(self):
        self.session('chat-a', ['turn-a'])
        self.session('chat-b', ['turn-b'])
        view = {'activeThreadId': 'chat-b', 'visibleTurnIds': ['turn-b'], 'viewToken': 'window-b'}
        with self.assertRaisesRegex(ValueError, '切换'):
            view_report(self.store, view, {'session_id': 'chat-a', 'turn_id': 'turn-a', 'view_token': 'window-a'})
        with self.assertRaisesRegex(ValueError, '所属'):
            view_report(self.store, view, {'session_id': 'chat-a', 'turn_id': 'turn-a', 'view_token': 'window-b'})

    def test_current_window_hover_uses_resolved_real_session(self):
        self.session('chat-a', ['turn-a'])
        view = {'activeThreadId': 'client-new-thread:draft', 'visibleTurnIds': ['turn-a'], 'viewToken': 'window-a'}
        report = view_report(self.store, view, {'session_id': 'chat-a', 'turn_id': 'turn-a', 'view_token': 'window-a'})
        self.assertEqual(report['session']['id'], 'chat-a')
