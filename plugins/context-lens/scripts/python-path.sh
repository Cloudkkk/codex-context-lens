# Shared Python discovery for the package installer and plugin hook.
find_context_lens_python() {
    task_python_home="$1"
    for task_python_candidate in \
        "$task_python_home/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3" \
        "$task_python_home/.local/bin/python3" \
        "/opt/homebrew/bin/python3" "/usr/local/bin/python3" "/usr/bin/python3"; do
        if [ -x "$task_python_candidate" ] && \
            "$task_python_candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
            printf '%s\n' "$task_python_candidate"
            return 0
        fi
    done
    printf '%s\n' 'Context Lens: Python 3.9+ not found. Open Codex to initialize its bundled runtime, or install Python 3.9+.' >&2
    return 1
}
