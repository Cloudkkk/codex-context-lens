#!/bin/sh
set -eu
task_stage="$1"
. "$task_stage/marketplace/plugins/context-lens/scripts/python-path.sh"
task_python=$(find_context_lens_python "$HOME")
exec "$task_python" "$task_stage/setup.py" --payload "$task_stage/marketplace"
