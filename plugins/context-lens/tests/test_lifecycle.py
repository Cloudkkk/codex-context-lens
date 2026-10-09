import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from context_lens import overlay
from context_lens import __version__
from context_lens.lifecycle import AutoEnable, Desktop, plugin_enabled, running_pids


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.app = Desktop(Path('/Applications/ChatGPT.app'),
                           Path('/Applications/ChatGPT.app/Contents/MacOS/ChatGPT'))
        self.open_app = Mock()
        self.plan = AutoEnable(9333, self.open_app)

    def test_missing_port_reopens_once_after_exit(self):
        self.assertEqual(self.plan.tick(False, self.app, [42], 0), 'waiting_for_app_exit')
        self.assertEqual(self.plan.tick(False, self.app, [], 5), 'waiting_for_app_exit')
        self.plan.tick(False, self.app, [], 7)
        self.open_app.assert_called_once_with(self.app, 9333)
        self.assertEqual(self.plan.tick(False, self.app, [], 28), 'restart_failed')
        self.open_app.assert_called_once()

    def test_connected_app_quit_is_respected(self):
        self.plan.tick(False, self.app, [42], 0)
        self.plan.tick(True, None, None, 1)
        for now in (5, 10, 50):
            self.plan.tick(False, self.app, [], now)
        self.open_app.assert_not_called()

    def test_another_instance_delays_reopen(self):
        self.plan.tick(False, self.app, [42], 0)
        self.plan.tick(False, self.app, [], 5)
        self.plan.tick(False, self.app, [43], 7)
        self.plan.tick(False, self.app, [43], 10)
        self.open_app.assert_not_called()
        self.plan.tick(False, self.app, [], 15)
        self.plan.tick(False, self.app, [], 17)
        self.open_app.assert_called_once()

    def test_failed_process_inspection_does_not_launch(self):
        self.plan.tick(False, self.app, [42], 0)
        for now in (5, 10, 50):
            self.plan.tick(False, self.app, None, now)
        self.open_app.assert_not_called()

    def test_cli_without_desktop_does_not_launch(self):
        for now in (0, 5, 50):
            self.plan.tick(False, self.app, [], now)
        self.open_app.assert_not_called()

    def test_reopen_failure_does_not_loop(self):
        self.open_app.side_effect = subprocess.CalledProcessError(1, 'open')
        self.plan.tick(False, self.app, [42], 0)
        self.plan.tick(False, self.app, [], 5)
        self.assertEqual(self.plan.tick(False, self.app, [], 7), 'restart_failed')
        self.plan.tick(False, self.app, [], 50)
        self.open_app.assert_called_once()

    def test_exact_main_process_excludes_helpers(self):
        output = ('42 /Applications/ChatGPT.app/Contents/MacOS/ChatGPT\n'
                  '43 /Applications/ChatGPT.app/Contents/MacOS/ChatGPT Helper\n'
                  '44 /Users/a/Codex Computer Use.app/Contents/MacOS/SkyComputerUseService\n')
        with patch('context_lens.lifecycle.subprocess.run', return_value=SimpleNamespace(stdout=output)):
            self.assertEqual(running_pids(self.app), [42])

    def test_disable_only_own_plugin(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / 'config.toml'
            config.write_text('[plugins."other@local"]\nenabled = false\n'
                              '[plugins."context-lens@codex-context-local"]\nenabled = true\n')
            self.assertTrue(plugin_enabled(config))
            config.write_text('[plugins."context-lens@codex-context-local"]\nenabled = false # off\n')
            self.assertFalse(plugin_enabled(config))


class SupervisorUpgradeTests(unittest.TestCase):
    def test_upgrade_stops_old_supervisor_before_starting_new(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            old = {'pid': 42, 'version': '0.1.0'}
            with patch.multiple(overlay, RUNTIME=runtime, STATE_PATH=runtime / 'overlay.json',
                                STOP_PATH=runtime / 'stop'), \
                    patch.object(overlay, 'active_process', side_effect=[old, None]), \
                    patch.object(overlay.subprocess, 'Popen', return_value=SimpleNamespace(pid=43)) as spawn:
                result = overlay.ensure(runtime / 'sessions', 9333)
                self.assertEqual(result['version'], __version__)
                self.assertEqual(result['pid'], 43)
                self.assertFalse((runtime / 'stop').exists())
                spawn.assert_called_once()

    def test_repeated_hook_reuses_supervisor(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            state = {'pid': 42, 'version': __version__, 'port': 9333,
                     'sessions_root': str((runtime / 'sessions').resolve())}
            with patch.object(overlay, 'RUNTIME', runtime), \
                    patch.object(overlay, 'active_process', return_value=state), \
                    patch.object(overlay.subprocess, 'Popen') as spawn:
                self.assertEqual(overlay.ensure(runtime / 'sessions', 9333), state)
                spawn.assert_not_called()


if __name__ == '__main__':
    unittest.main()
