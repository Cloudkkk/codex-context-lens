import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("setup", ROOT / "installer/setup.py")
setup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup)


class FakeCLI:
    def __init__(self, previous=None, old_plugin=None, fail_add=False):
        self.previous, self.old_plugin = previous, old_plugin
        self.calls, self.add_count = [], 0
        self.fail_add = fail_add

    def __call__(self, binary, arguments):
        self.calls.append(arguments)
        if arguments[:3] == ["plugin", "marketplace", "list"]:
            return {"marketplaces": ([self.previous] if self.previous else [])}
        if arguments[:2] == ["plugin", "list"]:
            if self.add_count:
                return {"installed": [{"pluginId": setup.PLUGIN_ID, "enabled": True, "version": "0.3.3"}]}
            return {"installed": ([self.old_plugin] if self.old_plugin else [])}
        if arguments[:2] == ["plugin", "add"]:
            self.add_count += 1
            if self.fail_add and self.add_count == 1:
                raise RuntimeError("Simulated plugin installation failure")
        return {}


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.payload = self.root / "payload"
        shutil.copytree(ROOT / "plugins", self.payload / "plugins")
        shutil.copytree(ROOT / ".agents", self.payload / ".agents")
        self.destination = self.root / "Application Support/Context Lens/marketplace"
        self.previous = {"name": setup.MARKETPLACE, "root": str(self.destination)}
        self.old_plugin = {"pluginId": setup.PLUGIN_ID, "enabled": True, "version": "0.2.12"}

    def old_destination(self):
        self.destination.mkdir(parents=True)
        (self.destination / "old-version.txt").write_text("previous installation")

    def test_fresh_install_registers_enables_and_verifies_without_trusting(self):
        cli = FakeCLI()
        verified = []
        result = setup.install(self.payload, self.destination, "codex", call=cli,
                               verify=lambda binary, catalog: verified.append(catalog))
        self.assertEqual(result["version"], "0.3.3")
        self.assertTrue((self.destination / "plugins/context-lens/.codex-plugin/plugin.json").is_file())
        self.assertEqual(verified, [self.destination / ".agents/plugins/marketplace.json"])
        self.assertIn(["plugin", "marketplace", "add", str(self.destination), "--json"], cli.calls)
        self.assertIn(["plugin", "add", setup.PLUGIN_ID, "--json"], cli.calls)
        self.assertFalse(any("trust" in json.dumps(call) or "config" in call for call in cli.calls))

    def test_update_replaces_complete_directory_and_removes_obsolete_manifest(self):
        self.old_destination()
        (self.destination / "obsolete.json").write_text("old")
        setup.install(self.payload, self.destination, "codex", call=FakeCLI(self.previous, self.old_plugin),
                      verify=lambda *args: None)
        self.assertFalse((self.destination / "obsolete.json").exists())
        self.assertTrue((self.destination / "plugins/context-lens/hooks/hooks.json").exists())

    def test_existing_other_source_is_removed_before_new_source_registration(self):
        self.previous["root"] = str(self.root / "old-source")
        cli = FakeCLI(self.previous, self.old_plugin)
        setup.install(self.payload, self.destination, "codex", call=cli, verify=lambda *args: None)
        remove = ["plugin", "marketplace", "remove", setup.MARKETPLACE, "--json"]
        add = ["plugin", "marketplace", "add", str(self.destination), "--json"]
        self.assertLess(cli.calls.index(remove), cli.calls.index(add))

    def test_failed_update_restores_old_files_and_source(self):
        self.old_destination()
        cli = FakeCLI(self.previous, self.old_plugin, fail_add=True)
        with self.assertRaisesRegex(RuntimeError, "Simulated"):
            setup.install(self.payload, self.destination, "codex", call=cli, verify=lambda *args: None)
        self.assertEqual((self.destination / "old-version.txt").read_text(), "previous installation")
        self.assertEqual(cli.add_count, 2)

    def test_failed_fresh_install_removes_new_source_and_files(self):
        cli = FakeCLI(fail_add=True)
        with self.assertRaises(RuntimeError):
            setup.install(self.payload, self.destination, "codex", call=cli, verify=lambda *args: None)
        self.assertFalse(self.destination.exists())
        self.assertIn(["plugin", "remove", setup.PLUGIN_ID, "--json"], cli.calls)
        self.assertIn(["plugin", "marketplace", "remove", setup.MARKETPLACE, "--json"], cli.calls)

    def test_failed_migration_removes_new_registration_before_restoring_old(self):
        self.previous['root'] = str(self.root / 'old-source')
        cli = FakeCLI(self.previous, self.old_plugin)
        def fail(*args):
            raise RuntimeError('Verification failed')
        with self.assertRaisesRegex(RuntimeError, 'Verification failed'):
            setup.install(self.payload, self.destination, 'codex', call=cli, verify=fail)
        old_add = ['plugin', 'marketplace', 'add', self.previous['root'], '--json']
        index = cli.calls.index(old_add)
        self.assertEqual(cli.calls[index - 1], ['plugin', 'marketplace', 'remove', setup.MARKETPLACE, '--json'])

    def test_failed_verification_restores_disabled_state(self):
        self.old_destination()
        self.old_plugin["enabled"] = False
        disabled = []
        def fail_verification(*args):
            raise RuntimeError("Hook discovery failed")
        with self.assertRaisesRegex(RuntimeError, "Hook discovery"):
            setup.install(self.payload, self.destination, "codex", call=FakeCLI(self.previous, self.old_plugin),
                          verify=fail_verification, disable=lambda binary: disabled.append(binary))
        self.assertEqual(disabled, ["codex"])
        self.assertTrue((self.destination / "old-version.txt").exists())

    def test_root_user_is_rejected_before_any_codex_call(self):
        cli = FakeCLI()
        with patch.object(setup.os, "geteuid", return_value=0), self.assertRaisesRegex(RuntimeError, "root"):
            setup.install(self.payload, self.destination, "codex", call=cli)
        self.assertEqual(cli.calls, [])

    def test_old_portable_manifest_is_rejected_before_registration(self):
        (self.payload / "plugins/context-lens/plugin.json").write_text("{}")
        cli = FakeCLI()
        with self.assertRaisesRegex(ValueError, "旧清单"):
            setup.install(self.payload, self.destination, "codex", call=cli)
        self.assertEqual(cli.calls, [])

    def test_payload_symlink_is_rejected(self):
        (self.payload / "outside").symlink_to(self.root)
        with self.assertRaisesRegex(ValueError, "符号链接"):
            setup.validate_payload(self.payload)

    def stale_cli(self):
        original = FakeCLI(self.previous)
        def call(binary, arguments):
            if arguments[:3] == ['plugin', 'marketplace', 'list']:
                if not (self.destination / '.agents/plugins/marketplace.json').is_file():
                    raise RuntimeError('Error: failed to load marketplace(s):\n- `codex-context-local`: marketplace root does not contain a supported manifest')
            if arguments[:3] == ['plugin', 'marketplace', 'add']:
                if not (self.destination / '.agents/plugins/marketplace.json').is_file():
                    raise RuntimeError('Source files must be restored before registration')
            return original(binary, arguments)
        self.previous.update({'unavailable': True, 'config': {'source_type': 'local', 'source': str(self.destination)}})
        return original, call

    def test_deleted_registered_directory_can_be_reinstalled(self):
        cli, call = self.stale_cli()
        recovered = []
        def recover(binary):
            recovered.append(binary)
            return self.previous, None
        result = setup.install(self.payload, self.destination, 'codex', call=call,
                               recover=recover, verify=lambda *args: None)
        self.assertTrue(result['installed'])
        self.assertEqual(recovered, ['codex'])
        self.assertTrue((self.destination / '.agents/plugins/marketplace.json').is_file())
        self.assertEqual(sum(args[:2] == ['plugin', 'list'] for args in cli.calls), 1)

    def test_failed_stale_repair_restores_exact_original_registration(self):
        cli, call = self.stale_cli()
        restored = []
        def fail(*args):
            raise RuntimeError('Verification failed')
        with self.assertRaisesRegex(RuntimeError, 'Verification failed'):
            setup.install(self.payload, self.destination, 'codex', call=call,
                          recover=lambda binary: (self.previous, None), verify=fail,
                          restore=lambda binary, previous: restored.append(previous['config']))
        self.assertFalse(self.destination.exists())
        self.assertEqual(restored, [self.previous['config']])

    def test_unrelated_marketplace_error_is_not_treated_as_our_stale_source(self):
        def fail(*args):
            raise RuntimeError('Error: failed to load marketplace(s):\n- `another-source`: missing manifest')
        def must_not_recover(*args):
            raise AssertionError('Unrelated sources must not be modified')
        with self.assertRaisesRegex(RuntimeError, 'another-source'):
            setup.install(self.payload, self.destination, 'codex', call=fail, recover=must_not_recover)
        self.assertFalse(self.destination.exists())

    def test_missing_source_recovery_reads_only_own_config(self):
        config = {'marketplaces': {setup.MARKETPLACE: {'source_type': 'local', 'source': str(self.destination)},
                                   'other': {'source_type': 'local', 'source': '/other'}},
                  'plugins': {setup.PLUGIN_ID: {'enabled': False}}}
        with patch.object(setup, 'appserver_request', return_value={'config': config}):
            previous, old = setup.recover_unavailable_source('codex')
        self.assertEqual(previous['config'], config['marketplaces'][setup.MARKETPLACE])
        self.assertFalse(old['enabled'])


if __name__ == "__main__":
    unittest.main()
