#!/bin/sh
set -eu
task_plugin_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
. "$task_plugin_root/scripts/python-path.sh"
task_python=$(find_context_lens_python "$HOME")
exec "$task_python" "$task_plugin_root/cli.py" "$@"
