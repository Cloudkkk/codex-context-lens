"""Explicit onboarding restart, independent of hook trust.

Only a user-invoked initialize command arms termination. SessionStart never
requests termination. Observe completion in logs before normal macOS quit.
"""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from .lifecycle import desktop, running_pids
from .reader import SessionStore


def task_state(path):
    """Read lifecycle events only. Partial writes/unknown state block restart."""
    active = set()
    with Path(path).open("rb") as stream:
        for raw in stream:
            if not raw.endswith(b"\n"):
                return None
            try:
                record = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                return None
            if not isinstance(record, dict):
                return None
            if record.get("type") != "event_msg":
                continue
            payload = record.get("payload", {})
            if not isinstance(payload, dict):
                return None
            kind, turn = payload.get("type"), payload.get("turn_id")
            if kind == "task_started" and turn:
                # A chat executes one turn at a time. A later turn supersedes
                # interrupted historical turns whose completion was omitted.
                active = {turn}
            elif kind in ("task_complete", "turn_aborted") and turn:
                active.discard(turn)
            elif kind == "turn_aborted":
                active.clear()
    return active


def quit_normally(app, pids):
    """Use NSRunningApplication.terminate; never SIGKILL or app modification.

    This is runtime functionality invoked by explicit onboarding, not a hook
    side effect. A cancelled/refused termination is respected by RestartPlan.
    """
    if sys.platform != "darwin" or not pids or running_pids(app) != pids:
        return False
    ctypes.CDLL("/System/Library/Frameworks/AppKit.framework/AppKit")
    objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
    objc.objc_getClass.argtypes = [ctypes.c_char_p]
    objc.objc_getClass.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    objc.sel_registerName.restype = ctypes.c_void_p
    address = ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value
    application = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int)(address)
    terminate = ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)(address)
    cls = objc.objc_getClass(b"NSRunningApplication")
    lookup = objc.sel_registerName(b"runningApplicationWithProcessIdentifier:")
    quit_selector = objc.sel_registerName(b"terminate")
    accepted = True
    for pid in pids:
        target = application(cls, lookup, pid)
        accepted = bool(target and terminate(target, quit_selector)) and accepted
    return accepted


def queue_restart(root, request_path, session, app, pids, port=9333, now=None):
    if not session:
        raise ValueError("无法确定初始化聊天。请从插件详情中的初始化入口运行。")
    store = SessionStore(root)
    path = store.resolve(session)
    turns = task_state(path)
    if turns is None or len(turns) != 1:
        raise ValueError("无法确认当前初始化轮次；未安排重启，请稍后重新初始化。")
    now = time.time() if now is None else now
    request = {"session_path": str(path), "turn": next(iter(turns)),
               "app": str(app.path), "pids": pids, "created_at": now,
               "deadline": now + 900, "port": port}
    # Private runtime directory and atomic replacement; duplicate clicks replace
    # a pending request rather than creating parallel restart processes.
    temporary = request_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(request))
    temporary.chmod(0o600)
    temporary.replace(request_path)
    return request


class RestartPlan:
    def __init__(self, root, request_path, open_app, quit_app=quit_normally):
        self.root, self.request_path = Path(root), request_path
        self.open_app, self.quit_app = open_app, quit_app
        self.request, self.phase, self.since = None, None, None
        self.idle_since = None
        self.app, self.last_error = None, None
        self._fingerprints = {}

    def cancel(self, status):
        self.request_path.unlink(missing_ok=True)
        self.request, self.phase, self.idle_since = None, None, None
        self.app = None
        self.last_error = None if status == "attached" else status
        return status

    def tasks_finished(self, now):
        own = Path(self.request["session_path"])
        candidates = {own}
        candidates.update(p for p in self.root.rglob("*.jsonl") if p.stat().st_mtime >= now - 86400)
        for path in candidates:
            if not path.resolve().is_relative_to(self.root.resolve()):
                return False
            stat = path.stat()
            stamp = (stat.st_size, stat.st_mtime_ns)
            cached = self._fingerprints.get(path)
            if cached is None or cached[0] != stamp:
                cached = (stamp, task_state(path))
                self._fingerprints[path] = cached
            active = cached[1]
            if active is None or active:
                return False
        return True

    def tick(self, connected, app, pids, now):
        if self.request is None:
            if not self.request_path.exists():
                return "attached" if connected else (self.last_error or "initialization_required")
            try:
                self.request = json.loads(self.request_path.read_text())
                self.phase = "waiting"
                self.last_error = None
                self.app = app
                if (not isinstance(self.request.get("pids"), list) or
                        not self.request.get("pids") or not self.request.get("turn") or
                        not Path(self.request["session_path"]).resolve().is_relative_to(self.root.resolve())):
                    return self.cancel("initialization_cancelled")
            except (OSError, ValueError, KeyError, TypeError):
                return self.cancel("initialization_cancelled")
        if connected:
            return self.cancel("attached")
        try:
            if now > self.request["deadline"]:
                return self.cancel("initialization_timed_out")
            if app is None or str(app.path) != self.request["app"] or pids is None:
                return self.cancel("initialization_cancelled")
            if self.phase == "reopening":
                if now - self.since > 30:
                    return self.cancel("restart_failed")
                return "reopening_app"
            if self.phase == "quitting":
                if pids:
                    if now - self.since > 30:
                        return self.cancel("quit_cancelled")
                    return "quitting_app"
                # Wait for graceful shutdown before starting a new instance.
                if now - self.since < 3:
                    return "quitting_app"
                self.phase, self.since = "reopening", now
                self.open_app(app, self.request["port"])
                return "reopening_app"
            if pids != self.request["pids"]:
                return self.cancel("initialization_cancelled")
            if not self.tasks_finished(now):
                self.idle_since = None
                return "waiting_for_tasks"
            if self.idle_since is None:
                self.idle_since = now
            if now - self.idle_since < 3:
                return "waiting_for_tasks"
            self.phase, self.since = "quitting", now
            if not self.quit_app(app, pids):
                return self.cancel("quit_cancelled")
            return "quitting_app"
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
            return self.cancel("initialization_cancelled")


def initialize(root, port, request_path, ensure, connected, session=None):
    if sys.platform != "darwin":
        raise ValueError("桌面初始化目前仅支持 macOS。")
    try:
        ready = bool(connected(port))
    except (OSError, ValueError, ConnectionError):
        ready = False
    app, pids = desktop()
    if app is None:
        raise ValueError("未找到支持的桌面安装位置：/Applications/ChatGPT.app 或 /Applications/Codex.app。")
    if pids is None:
        raise RuntimeError("执行权限不足，无法检查桌面进程。请由 Setup 使用沙箱外执行权限重新运行初始化；尚未安排重启。")
    if ready:
        return {"status": "ready", "monitor": ensure(root, port), "restart": False}
    if not pids:
        raise ValueError("未检测到正在运行的 Codex / ChatGPT 主进程；请在桌面端运行 Setup。")
    # Validate the initiating turn before starting a monitor. No latest-session
    # guessing: completion must correspond to the user's explicit setup action.
    state = ensure(root, port)
    queue_restart(root, request_path, session or os.environ.get("CODEX_THREAD_ID"), app, pids, port)
    return {"status": "waiting_for_tasks", "monitor": state, "restart": True,
            "message": "已安排初始化回复结束后自动退出并重开；无需手动退出。"}
