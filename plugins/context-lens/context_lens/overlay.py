"""Attach the panel to Codex message action rows, with no chat-query tools."""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from .cdp import CDP
from .reader import SessionStore
from . import __version__
from .lifecycle import AutoEnable, desktop, plugin_enabled

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = Path.home() / ".codex" / "sessions"
RUNTIME = Path(tempfile.gettempdir()) / ("codex-context-lens-" + str(os.getuid() if hasattr(os, "getuid") else "local"))
STATE_PATH = RUNTIME / "overlay.json"
STOP_PATH = RUNTIME / "stop"
STOPPED = False


def targets(port):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f"http://127.0.0.1:{port}/json/list", timeout=2) as response:
        pages = json.load(response)
    candidates = [t for t in pages if t.get("type") == "page" and str(t.get("url", "")).startswith("app://")
                  and ("codex" in (t.get("title", "") + t.get("url", "")).lower() or "chatgpt" in t.get("title", "").lower() or t.get("url", "").startswith("app://-/index.html"))
                  and "avatar-overlay" not in t.get("url", "") and t.get("webSocketDebuggerUrl")]
    # The un-routed main renderer precedes pop-outs/utilities. Only app:// pages
    # are considered; webpages and external browser targets are excluded.
    candidates.sort(key=lambda t: (t.get("url") not in ("app://-/index.html", "app://codex/index.html"), "initialRoute=" in t.get("url", "")))
    if not candidates:
        raise ConnectionError("本地端口中没有 Codex / ChatGPT 主窗口")
    return candidates


def trim_report(report):
    return {k: report[k] for k in ("session", "context", "latest_usage", "buckets", "notes")}


def watch(root, port):
    global STOPPED
    STOPPED = False
    def signal_stop(*_):
        global STOPPED
        STOPPED = True
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, signal_stop)
    store, clients = SessionStore(root), {}
    script = (PLUGIN_ROOT / "web" / "matching.js").read_text() + "\n" + (PLUGIN_ROOT / "web" / "overlay.js").read_text()
    last_status, refresh_at, discover_at = None, 0, 0
    bootstrap, runtime_status = AutoEnable(port), "starting"
    window_states = {}
    try:
        while not STOPPED and not STOP_PATH.exists():
            now = time.monotonic()
            if now >= discover_at:
                if not PLUGIN_ROOT.exists() or not plugin_enabled():
                    break
                try:
                    discovered = targets(port)
                except (OSError, ValueError, ConnectionError):
                    discovered = []
                ids = {t["id"] for t in discovered}
                for tid in list(clients):
                    if tid not in ids:
                        clients.pop(tid).close()
                        window_states.pop(tid, None)
                for target in discovered:
                    if target["id"] not in clients:
                        try:
                            c = CDP(target["webSocketDebuggerUrl"])
                            c.call("Runtime.enable")
                            c.call("Runtime.addBinding", {"name": "__contextLensRequest"})
                            c.evaluate(script)
                            clients[target["id"]] = c
                        except (OSError, RuntimeError, ValueError, ConnectionError):
                            if "c" in locals():
                                c.close()
                app, pids = desktop() if not discovered else (None, None)
                if STOPPED or STOP_PATH.exists() or not plugin_enabled():
                    break
                runtime_status = bootstrap.tick(bool(discovered), app, pids, now)
                if discovered and not clients:
                    runtime_status = "waiting_for_renderer"
                discover_at = now + 5
            for tid, client in list(clients.items()):
                try:
                    if now >= refresh_at:
                        # Reinstall after navigation/renderer replacement when needed.
                        client.evaluate(script)
                        state = client.evaluate("window.__codexContextLens.status()") or {}
                        window_states[tid] = state
                        sid = state.get("activeThreadId")
                        if sid:
                            try:
                                report = store.report(sid)
                                payload = {"sessionId": report["session"]["id"], "turns": report["turns"]}
                            except (OSError, ValueError):
                                payload = {"sessionId": sid, "turns": []}
                        else:
                            payload = {"sessionId": None, "turns": []}
                        client.evaluate("window.__codexContextLens.update(" + json.dumps(payload, ensure_ascii=False) + ")")
                    incoming = list(client.events)
                    client.events.clear()
                    message = client.receive(.05)
                    if message:
                        incoming.append(message)
                    for event in incoming:
                        if event.get("method") != "Runtime.bindingCalled" or event.get("params", {}).get("name") != "__contextLensRequest":
                            continue
                        request = json.loads(event["params"]["payload"])
                        answer = {"id": request.get("id")}
                        try:
                            if not request.get("session_id") or not request.get("turn_id"):
                                raise ValueError("无法确认这条消息所属的会话和轮次")
                            answer["report"] = trim_report(store.report(request["session_id"], request["turn_id"]))
                        except (OSError, ValueError) as exc:
                            answer["error"] = str(exc)
                        client.evaluate("window.__codexContextLens.receive(" + json.dumps(answer, ensure_ascii=False) + ")")
                except (OSError, RuntimeError, ValueError, ConnectionError, TimeoutError):
                    clients.pop(tid, None)
                    window_states.pop(tid, None)
                    client.close()
            if now >= refresh_at:
                refresh_at = now + 3
            status = "attached" if clients else runtime_status
            try:
                state = json.loads(STATE_PATH.read_text())
                if state.get("pid") == os.getpid() and (state.get("runtime_status") != status or state.get("windows") != window_states):
                    state["runtime_status"] = status
                    state["windows"] = window_states
                    STATE_PATH.write_text(json.dumps(state))
            except (OSError, ValueError):
                pass
            if status != last_status:
                print(status, flush=True)
                last_status = status
            time.sleep(.15 if clients else 1)
    finally:
        for client in clients.values():
            try:
                client.evaluate("window.__codexContextLens?.dispose()")
                client.call("Runtime.removeBinding", {"name": "__contextLensRequest"})
            except Exception:
                pass
            client.close()
        try:
            if json.loads(STATE_PATH.read_text()).get("pid") == os.getpid():
                STATE_PATH.unlink()
        except (OSError, ValueError):
            pass


def active_process():
    try:
        state = json.loads(STATE_PATH.read_text())
        pid = int(state["pid"])
        os.kill(pid, 0)
        command = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, check=True).stdout
        return state if "cli.py" in command and " watch " in command and "context-lens" in command else None
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return None


def ensure(root, port):
    RUNTIME.mkdir(parents=True, mode=0o700, exist_ok=True)
    with (RUNTIME / "start.lock").open("a") as lock:
        if os.name == "posix":
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX)
        previous = active_process()
        if previous:
            if (previous.get("version") == __version__ and previous.get("port") == port
                    and previous.get("sessions_root") == str(Path(root).expanduser().resolve())):
                return previous
            # Replace the old supervisor, without restarting the desktop app.
            STOP_PATH.touch()
            for _ in range(80):
                if not active_process():
                    break
                time.sleep(.1)
            else:
                raise RuntimeError("旧版面板仍在退出，请稍后重试启用")
        STOP_PATH.unlink(missing_ok=True)
        with (RUNTIME / "overlay.log").open("ab") as log:
            proc = subprocess.Popen([sys.executable, str(PLUGIN_ROOT / "cli.py"), "--sessions-root", str(Path(root).expanduser().resolve()), "watch", "--port", str(port)],
                                    stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True, cwd=PLUGIN_ROOT)
        state = {"pid": proc.pid, "port": port, "sessions_root": str(Path(root).expanduser().resolve()),
                 "plugin_root": str(PLUGIN_ROOT), "version": __version__, "runtime_status": "starting"}
        STATE_PATH.write_text(json.dumps(state))
        STATE_PATH.chmod(0o600)
        return state


def main():
    parser = argparse.ArgumentParser(description="Codex Context Lens — message-action hover panel")
    parser.add_argument("--sessions-root", type=Path, default=DEFAULT_ROOT)
    subs = parser.add_subparsers(dest="command", required=True)
    for name in ("watch", "start", "enable", "hook"):
        subs.add_parser(name).add_argument("--port", type=int, default=9333)
    for name in ("stop", "status"):
        subs.add_parser(name)
    inspect = subs.add_parser("inspect")
    inspect.add_argument("--session")
    inspect.add_argument("--turn")
    args = parser.parse_args()
    if args.command == "watch":
        return watch(args.sessions_root, args.port)
    if args.command == "hook":
        try:
            ensure(args.sessions_root, args.port)
        except Exception:
            pass  # Optional UI must not fail a user task or add context content.
        return
    if args.command == "stop":
        if active_process():
            STOP_PATH.touch()
        result = {"stopping": True}
    elif args.command == "status":
        result = active_process() or {"running": False}
    elif args.command == "inspect":
        result = SessionStore(args.sessions_root).report(args.session, args.turn)
    else:
        result = ensure(args.sessions_root, args.port)
    print(json.dumps(result, ensure_ascii=False, indent=2))
