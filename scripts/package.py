"""Build one local marketplace distribution ZIP from tracked files."""
import argparse
import json
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = PurePosixPath("plugins/context-lens")


def validate_plugin(archive):
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        if not names or len(names) != len(set(names)):
            raise ValueError("Empty archive or duplicate entries")
        for name in names:
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or "\\" in name:
                raise ValueError("Unsafe archive path: " + name)
        roots = {PurePosixPath(name).parts[0] for name in names}
        if len(roots) != 1:
            raise ValueError("Expected exactly one plugin directory")
        root = roots.pop()
        manifest = json.loads(bundle.read(root + "/plugin.json"))
        if root != manifest["name"] or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", root):
            raise ValueError("Directory must match the plugin name")
        if len(root) > 64:
            raise ValueError("Plugin directory name is too long")
        extension = manifest["extensions"]["com.openai"]
        if len(extension["interface"]["shortDescription"]) > 30:
            raise ValueError("Listing subtitle exceeds 30 characters")
        hook_path = extension["hooks"]
        if not hook_path.startswith("./") or ".." in PurePosixPath(hook_path).parts:
            raise ValueError("Hook path must stay inside the plugin")
        hooks = json.loads(bundle.read(root + "/" + hook_path[2:]))
        if not hooks["hooks"].get("SessionStart"):
            raise ValueError("SessionStart hook missing")
        for required in ("cli.py", "LICENSE", "context_lens/__init__.py",
                         "context_lens/overlay.py", "context_lens/cdp.py",
                         "context_lens/reader.py", "context_lens/lifecycle.py",
                         "skills/context-lens/SKILL.md", "web/matching.js", "web/overlay.js"):
            if root + "/" + required not in names:
                raise ValueError("Missing runtime file: " + required)
        forbidden = {".git", "__pycache__", "tests", ".env"}
        if any(forbidden.intersection(PurePosixPath(name).parts) or name.endswith(".jsonl")
               for name in names):
            raise ValueError("Archive contains development files or session data")
        if sum(name.endswith("/plugin.json") for name in names) != 1:
            raise ValueError("Archive contains multiple plugin manifests")
        if bundle.testzip() is not None:
            raise ValueError("Archive CRC check failed")
        return manifest["version"], len(names)


def write_zip(destination, entries):
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for source, name in entries:
            if source.is_symlink() or not source.is_file():
                raise ValueError("Expected a tracked regular file: " + str(source))
            info = zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            bundle.writestr(info, source.read_bytes())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-directory", type=Path, default=ROOT.parent)
    args = parser.parse_args()
    output = args.output_directory.resolve()
    output.mkdir(parents=True, exist_ok=True)
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    files = sorted(PurePosixPath(name) for name in tracked if name)
    plugin_files = []
    for path in files:
        if PLUGIN not in path.parents:
            continue
        relative = path.relative_to(PLUGIN)
        if "tests" in relative.parts or relative == PurePosixPath("web/demo.js"):
            continue
        plugin_files.append((ROOT / str(path), "context-lens/" + str(relative)))
    # The account-upload archive does not enable local hooks. Validate a
    # temporary plugin bundle, but distribute only the local marketplace.
    with tempfile.TemporaryDirectory() as temporary:
        plugin_zip = Path(temporary) / "plugin.zip"
        write_zip(plugin_zip, plugin_files)
        version, count = validate_plugin(plugin_zip)
    source_zip = output / "codex-context-local.zip"
    write_zip(source_zip, [(ROOT / str(path), "codex-context-local/" + str(path)) for path in files])
    with zipfile.ZipFile(source_zip) as bundle:
        prefix = "codex-context-local/"
        catalog = json.loads(bundle.read(prefix + ".agents/plugins/marketplace.json"))
        entry = next(item for item in catalog["plugins"] if item["name"] == "context-lens")
        path = entry["source"]["path"]
        if not path.startswith("./") or ".." in PurePosixPath(path).parts:
            raise ValueError("Marketplace plugin path must stay inside the distribution")
        if prefix + path[2:] + "/plugin.json" not in bundle.namelist():
            raise ValueError("Marketplace points at a missing plugin")
        bundle.read(prefix + "INSTALL.md")
        if bundle.testzip() is not None:
            raise ValueError("Distribution CRC check failed")
    print(f"Local marketplace {version}: {source_zip} ({len(files)} files)")
    print(f"Validated {count} plugin runtime files and the marketplace reference")


if __name__ == "__main__":
    main()
