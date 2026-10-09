"""Plugin-owned desktop bootstrap; never quits an active desktop app."""
from __future__ import annotations

import plistlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

PLUGIN_ID = "context-lens@codex-context-local"


@dataclass(frozen=True)
class Desktop:
    path: Path
    executable: Path


def desktop():
    """Find the actual main executable, excluding helpers and computer-use apps."""
    installed = []
    for path in (Path("/Applications/ChatGPT.app"), Path("/Applications/Codex.app")):
        try:
            with (path / "Contents/Info.plist").open("rb") as stream:
                name = plistlib.load(stream)["CFBundleExecutable"]
            binary = path / "Contents/MacOS" / name
            if binary.is_file():
                installed.append(Desktop(path, binary))
        except (OSError, ValueError, KeyError, plistlib.InvalidFileException):
            continue
    for app in installed:
        pids = running_pids(app)
        if pids:
            return app, pids
    return (installed[0], running_pids(installed[0])) if installed else (None, None)


def running_pids(app):
    """None means inspection failed; never infer that the app exited in that case."""
    try:
        result = subprocess.run(["ps", "-ax", "-o", "pid=,comm="],
                                capture_output=True, text=True, check=True, timeout=3)
        matches = []
        for line in result.stdout.splitlines():
            fields = line.strip().split(None, 1)
            if len(fields) == 2 and fields[1] == str(app.executable):
                matches.append(int(fields[0]))
        return matches
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def reopen(app, port):
    subprocess.run(["open", str(app.path), "--args",
                    "--remote-debugging-address=127.0.0.1",
                    f"--remote-debugging-port={port}"], check=True, timeout=10)


def plugin_enabled(config=None):
    """Read only this plugin's user-level switch, including Python 3.9 hosts."""
    config = config or Path.home() / ".codex/config.toml"
    try:
        text = config.read_text()
    except FileNotFoundError:
        return True
    except OSError:
        return False  # Unknown state must not trigger a desktop relaunch.
    # Restrict the fallback to the exact generated TOML section. Do not scan
    # other plugins or interpret arbitrary config as code.
    selected = False
    for line in text.splitlines():
        header = re.fullmatch(r'\s*\[plugins\.(["\x27])([^"\x27]+)\1\]\s*(?:#.*)?', line)
        if header:
            selected = header.group(2) == PLUGIN_ID
        elif line.lstrip().startswith("["):
            selected = False
        elif selected and re.fullmatch(r'\s*enabled\s*=\s*false\s*(?:#.*)?', line):
            return False
    return True


class AutoEnable:
    """One relaunch after a missing-port app exits; never reopen an enabled app."""
    def __init__(self, port, open_app=reopen):
        self.port, self.open_app = port, open_app
        self.pending = None
        self.exit_seen_at = None
        self.attempted = False
        self.attempted_at = None

    def tick(self, connected, app, pids, now):
        if connected:
            self.pending, self.exit_seen_at = None, None
            self.attempted, self.attempted_at = False, None
            return "attached"
        if self.attempted:
            return "restart_failed" if now - self.attempted_at >= 20 else "reopening_app"
        if app is None or pids is None:
            return "waiting_for_debug_port"
        if pids:
            self.pending, self.exit_seen_at = app, None
            return "waiting_for_app_exit"
        if self.pending is None:
            return "waiting_for_debug_port"  # CLI use must not open the GUI.
        if self.exit_seen_at is None:
            self.exit_seen_at = now
        if now - self.exit_seen_at < 2:
            return "waiting_for_app_exit"
        # Consume the plan before calling open, even if it raises: no restart
        # loops, and no surprise reopen after the user quits a connected app.
        pending, self.pending = self.pending, None
        self.attempted, self.attempted_at = True, now
        try:
            self.open_app(pending, self.port)
        except (OSError, subprocess.SubprocessError):
            self.attempted_at = now - 20
            return "restart_failed"
        return "reopening_app"
