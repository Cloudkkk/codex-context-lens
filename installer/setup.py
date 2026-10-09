"""One-time user installation invoked by the macOS package, never a launcher."""
import argparse
import json
import os
import selectors
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PLUGIN_ID = "context-lens@codex-context-local"
MARKETPLACE = "codex-context-local"


def find_codex():
    candidates = [
        "/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex",
        "/Applications/Codex.app/Contents/Resources/codex",
        "/Applications/Codex.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex",
        shutil.which("codex"),
    ]
    for candidate in candidates:
        if candidate and os.access(candidate, os.X_OK):
            return candidate
    raise RuntimeError("请先安装 Codex 或集成 Codex 的 ChatGPT 桌面端。")


def cli_json(binary, arguments):
    result = subprocess.run([str(binary), *arguments], capture_output=True,
                            text=True, timeout=120)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip() or "Codex command failed")
    return json.loads(result.stdout)


def appserver_request(binary, method, params):
    process = subprocess.Popen([str(binary), "app-server", "--stdio"],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, text=True, bufsize=1)
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)

    def request(ident, method, params):
        process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": ident,
                                        "method": method, "params": params}) + "\n")
        process.stdin.flush()
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if not selector.select(timeout=1):
                continue
            line = process.stdout.readline()
            if not line:
                raise RuntimeError("Codex 安装验证进程退出。")
            message = json.loads(line)
            if message.get("id") != ident:
                continue
            if "error" in message:
                raise RuntimeError(message["error"].get("message", "Codex verification failed"))
            return message.get("result", {})
        raise RuntimeError("Codex 插件验证超时。")

    try:
        request(1, "initialize", {
            "clientInfo": {"name": "context_lens_installer", "title": "Context Lens installer", "version": "1.0"},
            "capabilities": {"experimentalApi": True, "requestAttestation": False}})
        process.stdin.write('{"jsonrpc":"2.0","method":"initialized"}\n')
        process.stdin.flush()
        return request(2, method, params)
    finally:
        selector.close()
        process.terminate()
        try:
            process.wait(timeout=4)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def verify_hook(binary, catalog):
    detail = appserver_request(binary, "plugin/read", {
        "marketplacePath": str(catalog), "pluginName": "context-lens"}).get("plugin", {})
    if not any(item.get("eventName") == "sessionStart" for item in detail.get("hooks", [])):
        raise RuntimeError("当前 Codex 版本未识别插件 hook，请更新桌面端。")
    if not detail.get("onboardingSkill"):
        raise RuntimeError("当前 Codex 版本未识别初始化入口，请更新桌面端。")


def restore_disabled(binary):
    appserver_request(binary, "config/batchWrite", {
        "edits": [{"keyPath": "plugins." + PLUGIN_ID + ".enabled", "value": False, "mergeStrategy": "upsert"}],
        "filePath": None, "expectedVersion": None, "reloadUserConfig": True})


def recover_unavailable_source(binary):
    """Read a stale registration without requiring its deleted catalog.

    config/read does not load marketplaces. Never repair another source or
    blindly treat every marketplace/list failure as an empty installation.
    """
    config = appserver_request(binary, "config/read", {"includeLayers": False}).get("config", {})
    source = config.get("marketplaces", {}).get(MARKETPLACE)
    if not isinstance(source, dict) or not source.get("source"):
        raise RuntimeError("无法确认损坏来源的原始登记，未修改配置。")
    previous = {"name": MARKETPLACE, "root": source["source"],
                "marketplaceSource": {"source": source["source"], "ref": source.get("ref")},
                "config": source, "unavailable": True}
    settings = config.get("plugins", {}).get(PLUGIN_ID)
    old_plugin = ({"pluginId": PLUGIN_ID, "enabled": settings.get("enabled", True)}
                  if isinstance(settings, dict) else None)
    return previous, old_plugin


def restore_unavailable_source(binary, previous):
    # Restoring an originally broken registration with marketplace/add would
    # validate its missing directory and fail. Restore exactly this key instead.
    appserver_request(binary, "config/batchWrite", {
        "edits": [{"keyPath": "marketplaces." + MARKETPLACE, "value": previous["config"], "mergeStrategy": "replace"}],
        "filePath": None, "expectedVersion": None, "reloadUserConfig": True})


def validate_payload(payload):
    manifest_path = payload / "plugins/context-lens/.codex-plugin/plugin.json"
    manifest = json.loads(manifest_path.read_text())
    catalog = json.loads((payload / ".agents/plugins/marketplace.json").read_text())
    if manifest["name"] != "context-lens" or catalog["name"] != MARKETPLACE:
        raise ValueError("安装包身份不正确。")
    entry = next((item for item in catalog.get("plugins", []) if item.get("name") == "context-lens"), {})
    if entry.get("source") != {"source": "local", "path": "./plugins/context-lens"}:
        raise ValueError("安装包来源路径不正确。")
    if (payload / "plugins/context-lens/plugin.json").exists():
        raise ValueError("安装包含有会遮蔽原生 hook 的旧清单。")
    if manifest.get("hooks") != "./hooks/hooks.json":
        raise ValueError("安装包缺少原生 hook 声明。")
    if manifest.get("extensions", {}).get("com.openai", {}).get("onboardingSkill") != "./skills/setup/SKILL.md":
        raise ValueError("安装包缺少初始化入口。")
    for relative in ("hooks/hooks.json", "cli.py", "scripts/run-python.sh", "scripts/python-path.sh", "skills/setup/SKILL.md", "context_lens/initialize.py", "context_lens/routing.py"):
        if not (payload / "plugins/context-lens" / relative).is_file():
            raise ValueError("安装包缺少文件：" + relative)
    if any(path.is_symlink() for path in payload.rglob("*")):
        raise ValueError("安装包不能包含符号链接。")
    return manifest["version"]


def install(payload, destination, binary, call=cli_json, verify=verify_hook, disable=restore_disabled,
            recover=recover_unavailable_source, restore=restore_unavailable_source):
    if os.geteuid() == 0:
        raise RuntimeError("用户安装步骤不能以 root 执行。")
    version = validate_payload(payload)
    try:
        before = call(binary, ["plugin", "marketplace", "list", "--json"])
    except RuntimeError as error:
        if "failed to load marketplace" not in str(error) or "`" + MARKETPLACE + "`" not in str(error):
            raise
        previous, old_plugin = recover(binary)
    else:
        previous = next((item for item in before.get("marketplaces", []) if item["name"] == MARKETPLACE), None)
        installed_before = call(binary, ["plugin", "list", "--marketplace", MARKETPLACE, "--json"])
        old_plugin = next((item for item in installed_before.get("installed", []) if item.get("pluginId") == PLUGIN_ID), None)
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".context-lens-", dir=destination.parent))
    prepared, backup = stage / "new", stage / "previous"
    source_changed = False
    had_destination = destination.exists()
    if destination.is_symlink():
        shutil.rmtree(stage)
        raise ValueError("安装目标不能是符号链接。")
    try:
        shutil.copytree(payload, prepared)
        if had_destination:
            destination.rename(backup)
        prepared.rename(destination)
        source_changed = True
        if previous and Path(previous["root"]).resolve() != destination.resolve():
            call(binary, ["plugin", "marketplace", "remove", MARKETPLACE, "--json"])
        call(binary, ["plugin", "marketplace", "add", str(destination), "--json"])
        # This command installs/enables the local plugin; it never trusts hooks.
        call(binary, ["plugin", "add", PLUGIN_ID, "--json"])
        listing = call(binary, ["plugin", "list", "--marketplace", MARKETPLACE, "--json"])
        current = next((item for item in listing.get("installed", []) if item.get("pluginId") == PLUGIN_ID), {})
        if not current.get("enabled") or current.get("version") != version:
            raise RuntimeError("插件安装或版本验证失败。")
        verify(binary, destination / ".agents/plugins/marketplace.json")
    except Exception as error:
        rollback_errors = []
        if source_changed:
            shutil.rmtree(destination)
        if backup.exists():
            backup.rename(destination)
        # Attempt every rollback operation even if one step fails.
        operations = []
        if not old_plugin:
            operations.append(lambda: call(binary, ["plugin", "remove", PLUGIN_ID, "--json"]))
        if previous and previous.get("unavailable"):
            operations.append(lambda: restore(binary, previous))
        elif previous:
            operations.append(lambda: call(binary, ["plugin", "marketplace", "remove", MARKETPLACE, "--json"]))
            source = previous.get("marketplaceSource", {}).get("source") or previous["root"]
            arguments = ["plugin", "marketplace", "add", source, "--json"]
            ref = previous.get("marketplaceSource", {}).get("ref")
            if ref:
                arguments += ["--ref", ref]
            operations.append(lambda: call(binary, arguments))
        else:
            operations.append(lambda: call(binary, ["plugin", "marketplace", "remove", MARKETPLACE, "--json"]))
        if old_plugin:
            if not (previous and previous.get("unavailable")):
                operations.append(lambda: call(binary, ["plugin", "add", PLUGIN_ID, "--json"]))
            if not old_plugin.get("enabled", True):
                operations.append(lambda: disable(binary))
        for operation in operations:
            try:
                operation()
            except Exception as failure:
                rollback_errors.append(str(failure))
        if rollback_errors:
            raise RuntimeError(str(error) + "；恢复旧来源失败：" + "; ".join(rollback_errors)) from error
        raise
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return {"installed": True, "version": version, "source": str(destination),
            "nextStep": "打开 Codex 插件详情，运行 Setup 初始化，并按宿主提示授权本地执行权限。无需先信任 hook 或手动退出。"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", type=Path, required=True)
    args = parser.parse_args()
    destination = Path.home() / "Library/Application Support/Codex Context Lens/marketplace"
    print(json.dumps(install(args.payload.resolve(), destination, find_codex()), ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("Context Lens 安装失败：" + str(error), file=sys.stderr)
        sys.exit(1)
