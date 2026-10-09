import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from context_lens.initialize import RestartPlan, initialize, queue_restart, task_state
from context_lens.lifecycle import Desktop


class InitializationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.log = self.root / 'session.jsonl'
        self.request = self.root / 'initialize.json'
        self.app = Desktop(Path('/Applications/ChatGPT.app'), Path('/Applications/ChatGPT.app/Contents/MacOS/ChatGPT'))
        self.quit, self.reopen = Mock(return_value=True), Mock()
        self.plan = RestartPlan(self.root, self.request, self.reopen, self.quit)
        self.log.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': 'setup-chat'}}) + '\n')
        self.event('task_started')

    def event(self, kind, path=None, turn='setup-turn'):
        with (path or self.log).open('a') as stream:
            stream.write(json.dumps({'type': 'event_msg', 'payload': {'type': kind, 'turn_id': turn}}) + '\n')

    def queue(self):
        return queue_restart(self.root, self.request, 'setup-chat', self.app, [42], now=100)

    def tick(self, now, pids=None, connected=False):
        return self.plan.tick(connected, self.app, [42] if pids is None else pids, now)

    def test_monitor_without_explicit_initialization_never_quits_or_reopens(self):
        self.assertEqual(self.tick(100), 'initialization_required')
        self.tick(110, [])
        self.quit.assert_not_called()
        self.reopen.assert_not_called()

    def test_waits_for_own_completion_then_gracefully_quits_and_reopens_once(self):
        self.queue()
        self.assertEqual(self.tick(100), 'waiting_for_tasks')
        self.quit.assert_not_called()
        self.event('task_complete')
        self.tick(101)
        self.assertEqual(self.tick(104), 'quitting_app')
        self.quit.assert_called_once_with(self.app, [42])
        self.assertEqual(self.tick(105, []), 'quitting_app')
        self.assertEqual(self.tick(108, []), 'reopening_app')
        self.reopen.assert_called_once_with(self.app, 9333)
        self.assertEqual(self.tick(109, [43], True), 'attached')
        self.tick(150, [])
        self.reopen.assert_called_once()

    def test_other_active_task_delays_restart(self):
        other = self.root / 'other.jsonl'
        self.event('task_started', other, 'other-turn')
        self.queue()
        self.event('task_complete')
        self.tick(100)
        self.tick(110)
        self.quit.assert_not_called()
        self.event('task_complete', other, 'other-turn')
        self.tick(111)
        self.tick(114)
        self.quit.assert_called_once()

    def test_partial_own_log_blocks_restart(self):
        self.queue()
        self.event('task_complete')
        with self.log.open('a') as stream:
            stream.write('{')
        self.tick(100)
        self.tick(110)
        self.quit.assert_not_called()

    def test_refused_quit_is_respected_without_later_reopen(self):
        self.queue()
        self.event('task_complete')
        self.quit.return_value = False
        self.tick(100)
        self.assertEqual(self.tick(104), 'quit_cancelled')
        self.tick(110, [])
        self.reopen.assert_not_called()
        self.assertFalse(self.request.exists())

    def test_cancelled_or_stalled_shutdown_is_not_forced(self):
        self.queue()
        self.event('task_complete')
        self.tick(100)
        self.tick(104)
        self.assertEqual(self.tick(140), 'quit_cancelled')
        self.quit.assert_called_once()
        self.reopen.assert_not_called()

    def test_pid_change_cancels_request(self):
        self.queue()
        self.assertEqual(self.tick(101, [43]), 'initialization_cancelled')
        self.quit.assert_not_called()

    def test_wait_timeout_cancels_request(self):
        self.queue()
        self.assertEqual(self.tick(1001), 'initialization_timed_out')
        self.quit.assert_not_called()

    def test_reopen_failure_is_not_retried(self):
        self.queue()
        self.event('task_complete')
        self.tick(100)
        self.tick(104)
        self.reopen.side_effect = subprocess.CalledProcessError(1, 'open')
        self.assertEqual(self.tick(108, []), 'initialization_cancelled')
        self.tick(120, [])
        self.reopen.assert_called_once()

    def test_no_session_id_never_uses_latest(self):
        with self.assertRaisesRegex(ValueError, '初始化聊天'):
            queue_restart(self.root, self.request, None, self.app, [42])
        self.assertFalse(self.request.exists())

    def test_connected_initialization_does_not_restart(self):
        ensure = Mock(return_value={'pid': 55})
        with patch('context_lens.initialize.sys.platform', 'darwin'), \
                patch('context_lens.initialize.desktop', return_value=(self.app, [42])):
            result = initialize(self.root, 9333, self.request, ensure, lambda port: ['window'])
        self.assertFalse(result['restart'])
        ensure.assert_called_once()
        self.assertFalse(self.request.exists())

    def test_abort_clears_active_turn(self):
        self.event('turn_aborted')
        self.assertEqual(task_state(self.log), set())

    def test_new_turn_supersedes_interrupted_history(self):
        self.event('task_started', turn='later-turn')
        self.event('task_complete', turn='later-turn')
        self.assertEqual(task_state(self.log), set())

    def test_abort_without_turn_id_clears_active_turn(self):
        self.event('turn_aborted', turn=None)
        self.assertEqual(task_state(self.log), set())


if __name__ == '__main__':
    unittest.main()
