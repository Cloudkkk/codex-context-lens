"""Resolve a visible chat from exact session or authoritative turn IDs.

One shared, incremental index serves every renderer. It does not retain UI
aliases or use newest sessions/text to guess a chat's owner.
"""
from __future__ import annotations

import json


class TurnSessions:
    def __init__(self, store):
        self.store = store
        self.files = {}
        self.owners = {}

    def remove(self, path):
        old = self.files.pop(path, None)
        if old:
            for turn in old['turns']:
                entries = self.owners.get(turn, {})
                entries.pop(path, None)
                if not entries:
                    self.owners.pop(turn, None)

    def sync(self):
        paths = set(self.store.paths())
        for path in set(self.files) - paths:
            self.remove(path)
        for path in paths:
            try:
                stat = path.stat()
                stamp = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
                old = self.files.get(path)
                if old and old['stamp'] == stamp:
                    continue
                header = self.store.header(path)
                if header['is_subagent']:
                    self.remove(path)
                    continue
                sid = header['id']
                append = (old and old['sid'] == sid and old['stamp'][0] == stat.st_ino
                          and stat.st_size > old['stamp'][1])
                if not append:
                    self.remove(path)
                    old = {'sid': sid, 'turns': set(), 'offset': 0}
                self.files[path] = old
                with path.open('rb') as stream:
                    stream.seek(old['offset'])
                    while True:
                        offset = stream.tell()
                        raw = stream.readline()
                        if not raw or not raw.endswith(b'\n'):
                            old['offset'] = offset
                            break
                        if b'"turn_id"' not in raw:
                            continue
                        try:
                            record = json.loads(raw)
                        except (ValueError, UnicodeDecodeError):
                            continue
                        if not isinstance(record, dict) or record.get('type') not in ('event_msg', 'turn_context'):
                            continue
                        payload = record.get('payload', {})
                        turn = payload.get('turn_id') if isinstance(payload, dict) else None
                        if isinstance(turn, str) and turn:
                            old['turns'].add(turn)
                            self.owners.setdefault(turn, {})[path] = sid
                old['stamp'] = stamp
                self.files[path] = old
            except (OSError, ValueError):
                self.remove(path)

    def resolve(self, view_id, turn_ids):
        turns = set(t for t in (turn_ids or []) if isinstance(t, str) and t)
        # Never map a draft alias to a previous chat. Each request revalidates
        # current visible IDs, including a reused alias in another window.
        self.sync()
        direct = {entry['sid'] for entry in self.files.values() if entry['sid'] == view_id}
        known = [set(self.owners[turn].values()) for turn in turns if turn in self.owners]
        result = {'session_id': None, 'reason': 'waiting_for_turn_log' if turns else 'no_visible_chat',
                  'matched_turns': len(known), 'unresolved_turns': len(turns) - len(known)}
        if known:
            candidates = set.intersection(*known)
            if not candidates:
                result['reason'] = 'conflicting_turn_sessions'
            elif len(candidates) == 1:
                result.update(session_id=next(iter(candidates)), reason='turn_ids')
            elif len(direct & candidates) == 1:
                result.update(session_id=next(iter(direct & candidates)), reason='session_id')
            else:
                result['reason'] = 'ambiguous_turn_session'
        elif not turns and len(direct) == 1:
            result.update(session_id=next(iter(direct)), reason='session_id')
        return result
