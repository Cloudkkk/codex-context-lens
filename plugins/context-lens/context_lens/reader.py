"""Parse local rollouts, respecting compaction and usage-record boundaries.

No provider API, database mutation, tokenizer download or transcript export.
Text attribution is explicitly an estimate; opaque payloads are never tokenized.
"""
from __future__ import annotations

import json
import math
import re
import threading
import time
from pathlib import Path

CATEGORIES = {
    "user": ("用户消息", "#3b82f6"),
    "assistant": ("助手消息", "#6366f1"),
    "tool_results": ("工具返回", "#ef8354"),
    "tool_calls": ("工具调用", "#f2b84b"),
    "skills": ("Skills 指令与目录", "#16a085"),
    "memory": ("AGENTS.md / 记忆指令", "#b799e8"),
    "system": ("基础指令", "#7a879a"),
    "developer": ("环境与运行指令", "#8e9e78"),
    "tools": ("已记录工具定义", "#dc70a1"),
    "summary": ("压缩摘要", "#56a9ac"),
    "unknown": ("日志未覆盖的差额", "#b5bac4"),
}
TAGS = {
    "skills_instructions": "skills", "plugins_instructions": "tools",
    "apps_instructions": "tools", "recommended_plugins": "tools",
    "environment_context": "developer", "permissions instructions": "developer",
    "collaboration_mode": "developer", "multi_agent_role": "developer",
    "multi_agent_mode": "developer", "user_instructions": "memory",
}
TAG_PATTERN = re.compile(r"<(" + "|".join(map(re.escape, TAGS)) + r")>(.*?)</\1>", re.S)


def text_of(value):
    """Only plaintext fields; exclude base64 images and encrypted reasoning."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(filter(None, (text_of(v) for v in value)))
    if isinstance(value, dict):
        kind = value.get("type", "")
        if kind in ("input_image", "image", "image_url", "compaction"):
            return ""
        for key in ("text", "content", "summary", "message", "output"):
            if key in value:
                return text_of(value[key])
    return ""


def estimate(text):
    # Approximation for mixed English/Chinese text, not a model-specific tokenizer.
    ascii_count = sum(ord(c) < 128 for c in text)
    cjk = sum("\u3400" <= c <= "\u9fff" for c in text)
    return math.ceil(ascii_count / 4 + cjk + (len(text) - ascii_count - cjk) / 2)


def number(value):
    return max(0, int(value)) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def usage_of(value):
    value = value if isinstance(value, dict) else {}
    result = {k: number(value.get(k)) for k in (
        "input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens", "total_tokens")}
    if not result["cached_input_tokens"]:
        result["cached_input_tokens"] = number((value.get("input_tokens_details") or {}).get("cached_tokens"))
    if not result["reasoning_output_tokens"]:
        result["reasoning_output_tokens"] = number((value.get("output_tokens_details") or {}).get("reasoning_tokens"))
    if not result["total_tokens"]:
        result["total_tokens"] = result["input_tokens"] + result["output_tokens"]
    return result


def iter_records(path):
    with path.open("rb") as handle:
        for line_no, raw in enumerate(handle, 1):
            try:
                record = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                yield line_no, None, raw.endswith(b"\n")
                continue
            yield line_no, record if isinstance(record, dict) else None, raw.endswith(b"\n")


def inspect_session(path: Path, through_line=None):
    meta, context, history, latest, total, points, pending = {}, {}, [], None, None, [], []
    current_window, compact_count, compact_line = None, 0, None
    invalid, partial, opaque, has_replacement = 0, False, False, True
    calls = {}
    title = ""
    usage_source, usage_timestamp, usage_line = None, None, None
    base_snapshot, context_snapshot, history_snapshot = None, None, None
    response_ids, turns, current_turn, last_assistant = set(), {}, None, None
    capacity_snapshot = None
    for line, rec, terminated in iter_records(path):
        if through_line is not None and line > through_line:
            break
        if rec is None:
            invalid += int(terminated)
            partial |= not terminated
            continue
        typ, p = rec.get("type"), rec.get("payload")
        if not isinstance(p, dict):
            continue
        if typ == "session_meta":
            if not meta:
                meta = p  # Spawned rollouts can replay their parent's session_meta.
        elif typ == "turn_context":
            context = p
            current_turn = p.get("turn_id", current_turn)
        elif typ == "compacted":
            compact_count += 1
            compact_line = line
            replacement = p.get("replacement_history")
            history = [(item, line, "压缩后保留") for item in replacement if isinstance(item, dict)] if isinstance(replacement, list) else []
            has_replacement = isinstance(replacement, list)
            if not has_replacement and p.get("message"):
                history.append(({"type": "message", "role": "summary", "content": p["message"]}, line, "压缩摘要"))
            opaque |= any(item.get("type") == "compaction" for item, _, _ in history)
            calls = {item.get("call_id"): item.get("name", "tool") for item, _, _ in history if item.get("type") in ("function_call", "custom_tool_call")}
            # A pre-compaction usage snapshot no longer describes the rebuilt input.
            pending = []
            current_window = number(p.get("model_context_window")) or current_window
        elif typ == "response_item":
            if p.get("type") == "message" and p.get("role") == "assistant":
                # Keep line structure and enough source to complete Markdown links;
                # UI matching strips formatting before choosing a short signature.
                last_assistant = {"message_id": p.get("id"), "text_prefix": text_of(p.get("content", "")).strip()[:2000]}
            if p.get("type") == "message" and p.get("role") == "user" and not title:
                candidate = text_of(p.get("content", ""))
                if not candidate.lstrip().startswith(("<environment_context>", "<user_instructions>", "<external_codex_apps")):
                    title = re.sub(r"\s+", " ", candidate).strip()[:72]
            history.append((p, line, "日志"))
            pending.append(len(history) - 1)
            if p.get("type") in ("function_call", "custom_tool_call"):
                calls[p.get("call_id")] = p.get("name", "tool")
            if p.get("type") in ("compaction", "reasoning") and p.get("encrypted_content"):
                opaque = True
        elif typ == "event_msg" and p.get("type") == "task_started":
            current_window = number(p.get("model_context_window")) or current_window
            current_turn = p.get("turn_id", current_turn)
        elif typ == "event_msg" and p.get("type") == "task_complete" and p.get("turn_id") in turns:
            turns[p["turn_id"]]["completed"] = True
            if last_assistant:
                turns[p["turn_id"]].update(last_assistant)
        if typ == "token_usage_record" or (typ == "event_msg" and p.get("type") == "token_count"):
            info = p.get("info") if typ == "event_msg" else None
            raw = p.get("usage") if typ == "token_usage_record" else (info or {}).get("last_token_usage")
            aggregate = p.get("thread_token_usage") if typ == "token_usage_record" else (info or {}).get("total_token_usage")
            if info:
                current_window = number(info.get("model_context_window")) or current_window
            if not isinstance(raw, dict):
                continue
            # token_count commonly echoes the preceding token_usage_record; don't
            # replace its more current counters or consume a request boundary twice.
            echo = typ == "event_msg" and usage_source == "token_usage_record" and usage_of(raw) == latest
            if echo:
                continue
            rid = p.get("response_id")
            if rid and rid in response_ids:
                continue
            if rid:
                response_ids.add(rid)
            latest, total = usage_of(raw), usage_of(aggregate) if isinstance(aggregate, dict) else None
            usage_source, usage_timestamp, usage_line = typ, rec.get("timestamp"), line
            # Outputs from this response appear before its usage event but are not
            # part of that response's input. Keep user/developer messages in that batch.
            generated = set(i for i in pending if history[i][0].get("role") == "assistant" or history[i][0].get("type") not in ("message", "function_call_output", "custom_tool_call_output"))
            history_snapshot = [entry for i, entry in enumerate(history) if i not in generated]
            base_snapshot, context_snapshot = meta, context
            capacity_snapshot = current_window
            pending = []
            points.append({"timestamp": usage_timestamp, "input": latest["input_tokens"],
                           "output": latest["output_tokens"], "capacity": current_window, "line": line})
            if current_turn:
                previous = turns.get(current_turn, {})
                turns[current_turn] = {**previous, "turn_id": current_turn, "usage_line": line,
                                       "used": latest["input_tokens"], "capacity": current_window,
                                       "timestamp": usage_timestamp, "completed": previous.get("completed", False)}
                if last_assistant:
                    turns[current_turn].update(last_assistant)
    # If the only usage event predates compaction, its total is stale, not current.
    stale_compaction = compact_line is not None and (usage_line or 0) < compact_line
    if stale_compaction:
        latest = None
    elif latest is not None:
        current_window = capacity_snapshot
    entries = []

    def add(bucket, text, label, line=None, origin="日志"):
        if not text:
            return
        entries.append({"category": bucket, "label": label[:140], "line": line, "origin": origin,
                        "chars": len(text), "bytes": len(text.encode("utf-8")), "weight": estimate(text)})

    def instruction(text, default, label, line=None, origin="日志"):
        last = 0
        for match in TAG_PATTERN.finditer(text):
            add(default, text[last:match.start()], label, line, origin)
            add(TAGS[match.group(1)], match.group(0), match.group(1), line, origin)
            last = match.end()
        remainder = text[last:]
        # System tools can be observable in older CLI rollouts; do not synthesize
        # unrecorded definitions from today's config or enabled plugin directory.
        if "# Tools" in remainder and default == "system":
            before, _, tools = remainder.partition("# Tools")
            add(default, before, label, line, origin)
            add("tools", tools, "已记录的工具定义", line, origin)
        else:
            add(default, remainder, label, line, origin)

    chosen = history_snapshot if latest is not None and history_snapshot is not None else history
    chosen_meta, chosen_context = (base_snapshot or meta), (context_snapshot or context)
    base = chosen_meta.get("base_instructions") or {}
    instruction(text_of(base), "system", "session_meta.base_instructions", 1)
    for key, bucket in (("user_instructions", "memory"), ("developer_instructions", "developer")):
        instruction(text_of(chosen_context.get(key, "")), bucket, "turn_context." + key)
    for item, line, origin in chosen:
        kind = item.get("type")
        if kind == "message":
            role = item.get("role", "assistant")
            content = text_of(item.get("content", ""))
            if role == "developer":
                instruction(content, "developer", "developer 消息", line, origin)
            elif role == "system":
                instruction(content, "system", "system 消息", line, origin)
            elif role == "user" and content.startswith("# AGENTS.md instructions"):
                add("memory", content, "AGENTS.md", line, origin)
            else:
                add({"user": "user", "assistant": "assistant", "summary": "summary"}.get(role, "assistant"), content, role + " 消息", line, origin)
        elif kind in ("function_call", "custom_tool_call"):
            arguments = item.get("arguments", item.get("input", ""))
            arguments = arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False)
            add("tool_calls", item.get("name", "tool") + "\n" + arguments, item.get("name", "tool"), line, origin)
        elif kind in ("function_call_output", "custom_tool_call_output"):
            output = item.get("output", "")
            if not isinstance(output, str):
                output = text_of(output)
            label = calls.get(item.get("call_id"), "工具返回")
            add("tool_results", output, label, line, origin)
        elif kind == "reasoning":
            add("assistant", text_of(item.get("summary", "")), "已记录的思考摘要", line, origin)
        elif kind == "agent_message":
            add("assistant", text_of(item.get("content", "")), "子代理消息", line, origin)
    raw_estimate = sum(e["weight"] for e in entries)
    used = latest["input_tokens"] if latest is not None else None
    scale = min(1, used / raw_estimate) if used is not None and raw_estimate else 1
    # Largest-remainder allocation preserves totals exactly after capping estimates.
    exact_estimates = [e["weight"] * scale for e in entries]
    allocated = [math.floor(v) for v in exact_estimates]
    target = min(raw_estimate, used) if used is not None else raw_estimate
    for i in sorted(range(len(entries)), key=lambda i: exact_estimates[i] - allocated[i], reverse=True)[:max(0, target - sum(allocated))]:
        allocated[i] += 1
    for entry, tokens in zip(entries, allocated):
        entry["estimated_tokens"] = tokens
        del entry["weight"]
    buckets = []
    for key, (label, color) in CATEGORIES.items():
        members = [e for e in entries if e["category"] == key]
        tokens = (used - sum(allocated)) if key == "unknown" and used is not None else sum(e["estimated_tokens"] for e in members)
        buckets.append({"key": key, "label": label, "color": color, "tokens": tokens,
                        "percent": round(tokens / current_window * 100, 3) if current_window else None,
                        "chars": sum(e["chars"] for e in members), "items": len(members),
                        "kind": "residual" if key == "unknown" else "estimate",
                        "details": sorted(members, key=lambda e: e["estimated_tokens"], reverse=True)[:30]})
    notes = ["上下文用量为最近一次请求的 input_tokens；累计消耗包含历史请求，不代表当前窗口占用。",
             "分类按日志可见文本估算（ASCII 约 4 字符/token、中文约 1 字符/token），不是模型 tokenizer 的精确分项。",
             "差额可能包含未记录的工具定义、图片、加密内容及估算误差；不会将它冒充为某类工具用量。",
             "缓存输入属于输入 token，推理输出属于输出 token，均不重复累加。"]
    if scale < 1:
        notes.append("可见文本估算超过实际输入量，已按比例封顶；日志重建和实际请求可能不完全一致。")
    if opaque:
        notes.append("日志含加密思考或不透明压缩块；这些内容不按密文长度估算。")
    if compact_count and not has_replacement:
        notes.append("最近压缩未提供 replacement_history；仅分析可恢复的摘要和后续消息。")
    if invalid or partial:
        notes.append(f"跳过 {invalid} 条损坏记录" + ("；写入中的最后一行等待下次刷新。" if partial else "。"))
    if latest is None:
        notes.append("尚无可用的当前请求 token 统计；窗口用量显示为未知。" if not stale_compaction else "刚发生压缩，等待下一次请求统计；旧用量不会显示为当前用量。")
    return {"schema_version": 1, "session": {"id": meta.get("id", meta.get("session_id", path.stem)),
            "title": title or Path(meta.get("cwd", "session")).name, "cwd": meta.get("cwd", ""),
            "model": chosen_context.get("model", "未知"), "path": str(path), "source": meta.get("source"),
            "is_subagent": bool(meta.get("parent_thread_id")) or isinstance(meta.get("source"), dict)},
            "context": {"used": used, "capacity": current_window, "free": max(0, current_window - used) if current_window and used is not None else None,
                        "percent": round(used / current_window * 100, 2) if current_window and used is not None else None,
                        "usage_timestamp": usage_timestamp, "usage_source": usage_source, "usage_line": usage_line,
                        "visible_estimated": sum(allocated), "coverage_percent": round(sum(allocated) / used * 100, 1) if used else None,
                        "compactions": compact_count, "latest_compaction_line": compact_line,
                        "pending_items": len(pending), "estimate_scaled": scale < 1},
            "latest_usage": latest, "total_usage": total, "buckets": buckets,
            "requests": points[-120:], "turns": list(turns.values()), "notes": notes, "read_at": time.time()}


class SessionStore:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        self._headers, self._reports = {}, {}
        self._paths, self._scan_at = [], None
        self.lock = threading.RLock()
        from .routing import TurnSessions
        self.turn_sessions = TurnSessions(self)

    def paths(self):
        if self._scan_at is None or time.monotonic() - self._scan_at > 3:
            self._paths = sorted((p for p in self.root.rglob("*.jsonl") if p.resolve().is_relative_to(self.root)), key=lambda p: p.stat().st_mtime, reverse=True)
            self._scan_at = time.monotonic()
        return self._paths

    def header(self, path):
        stat = path.stat()
        fingerprint = (stat.st_size, stat.st_mtime_ns)
        old = self._headers.get(path)
        if old and old[0] == fingerprint:
            return old[1]
        metadata, title = {}, ""
        with path.open("rb") as handle:
            # Bounded header scan; no full parse of hundreds of sessions on refresh.
            consumed = 0
            for _ in range(35):
                raw = handle.readline(512_000)
                consumed += len(raw)
                if not raw or consumed > 512_000:
                    break
                try:
                    rec = json.loads(raw)
                except (ValueError, UnicodeDecodeError):
                    continue
                p = rec.get("payload", {})
                if not isinstance(p, dict):
                    continue
                if rec.get("type") == "session_meta":
                    if not metadata:
                        metadata = p
                if rec.get("type") == "response_item" and p.get("role") == "user":
                    candidate = text_of(p.get("content", ""))
                    if not candidate.lstrip().startswith(("<environment_context>", "<user_instructions>", "# AGENTS.md", "<external_codex_apps")):
                        title = re.sub(r"\s+", " ", candidate).strip()[:72]
                        break
        result = {"id": metadata.get("id", metadata.get("session_id", path.stem)), "title": title or Path(metadata.get("cwd", "session")).name,
                  "cwd": metadata.get("cwd", ""), "modified_at": stat.st_mtime,
                  "size": stat.st_size, "is_subagent": bool(metadata.get("parent_thread_id")) or isinstance(metadata.get("source"), dict)}
        self._headers[path] = (fingerprint, result)
        return result

    def list(self, include_subagents=False, limit=120):
        with self.lock:
            result = []
            for path in self.paths():
                try:
                    h = self.header(path)
                except OSError:
                    continue
                if not include_subagents and h["is_subagent"]:
                    continue
                result.append(h)
                if len(result) >= limit:
                    break
            return result

    def resolve(self, selector=None):
        paths = self.paths()
        if not selector or selector == "latest":
            for path in paths:
                if not self.header(path)["is_subagent"]:
                    return path
            raise FileNotFoundError("未找到主聊天日志")
        matches = [p for p in paths if selector == self.header(p)["id"] or selector == p.name]
        if not matches:
            matches = [p for p in paths if len(selector) >= 8 and self.header(p)["id"].startswith(selector)]
        if len(matches) != 1:
            raise ValueError("会话 ID 未找到或不唯一；请从列表选择完整 ID")
        return matches[0]

    def resolve_view(self, view_id, turn_ids):
        with self.lock:
            return self.turn_sessions.resolve(view_id, turn_ids)

    def report(self, selector=None, turn_id=None):
        with self.lock:
            path = self.resolve(selector)
            stat = path.stat()
            stamp = (stat.st_mtime_ns, stat.st_size)
            key = (path, turn_id)
            old = self._reports.get(key)
            if old and old[0] == stamp:
                return old[1]
            if turn_id:
                current = self.report(selector)
                turn = next((t for t in current["turns"] if t["turn_id"] == turn_id), None)
                if turn is None:
                    raise ValueError("找不到这条消息对应的轮次")
                report = inspect_session(path, turn["usage_line"])
                report["selected_turn"] = turn
            else:
                report = inspect_session(path)
            self._reports[key] = (stamp, report)
            if len(self._reports) > 12:
                self._reports.pop(next(iter(self._reports)))
            return report
