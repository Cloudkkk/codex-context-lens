"""Build the macOS GUI installer. No runtime download or separate launcher."""
import argparse
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "com.cloudkkk.codex.context-lens"


def build(output, sign=None):
    manifest = json.loads((ROOT / "plugins/context-lens/.codex-plugin/plugin.json").read_text())
    version = manifest["version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Expected a semantic version")
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    output.mkdir(parents=True, exist_ok=True)
    destination = output / ("Context-Lens-" + version + "-macOS.pkg")
    with tempfile.TemporaryDirectory(prefix="context-lens-pkg-") as temporary:
        stage = Path(temporary)
        scripts = stage / "scripts"
        payload = scripts / "payload"
        payload.mkdir(parents=True)
        for name in tracked:
            if not name:
                continue
            relative = Path(name)
            keep = (name == ".agents/plugins/marketplace.json" or
                    name in ("README.md", "INSTALL.md", "LICENSE") or name.startswith("docs/") or
                    name.startswith("plugins/context-lens/"))
            if not keep or "tests" in relative.parts or name.endswith("web/demo.js"):
                continue
            source = ROOT / relative
            if source.is_symlink() or not source.is_file():
                raise ValueError("Expected tracked regular file: " + name)
            target = payload / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        for name in ("postinstall", "install-user.sh", "setup.py"):
            shutil.copy2(ROOT / "installer" / name, scripts / name)
        (scripts / "postinstall").chmod(0o755)
        subprocess.run(["/usr/bin/pkgbuild", "--nopayload", "--scripts", str(scripts),
                        "--identifier", IDENTIFIER, "--version", version,
                        str(stage / "component.pkg")], check=True)
        distribution = stage / "distribution.xml"
        distribution.write_text(f'''<?xml version="1.0" encoding="utf-8"?>
<installer-gui-script minSpecVersion="2">
  <title>Context Lens {version}</title>
  <options customize="never" require-scripts="true" hostArchitectures="arm64,x86_64"/>
  <domains enable_localSystem="true" enable_currentUserHome="false" enable_anywhere="false"/>
  <welcome file="welcome.html"/>
  <conclusion file="conclusion.html"/>
  <choices-outline><line choice="context-lens"/></choices-outline>
  <choice id="context-lens" visible="false" title="Context Lens"><pkg-ref id="{IDENTIFIER}"/></choice>
  <pkg-ref id="{IDENTIFIER}" version="{version}" onConclusion="none">component.pkg</pkg-ref>
</installer-gui-script>
''')
        command = ["/usr/bin/productbuild", "--distribution", str(distribution),
                   "--resources", str(ROOT / "installer/resources"), "--package-path", str(stage)]
        if sign:
            command += ["--sign", sign]
        subprocess.run([*command, str(destination)], check=True)
    print(destination)
    print("Signed" if sign else "Unsigned: macOS may require allowing this package in Privacy & Security.")
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-directory", type=Path, default=ROOT.parent)
    parser.add_argument("--sign", help="Existing Developer ID Installer identity")
    args = parser.parse_args()
    build(args.output_directory.resolve(), args.sign)
