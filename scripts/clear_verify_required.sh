#!/usr/bin/env sh
# Container-friendly wrapper for scripts/clear_verify_required.py.
# Prefer this over invoking python directly so PYTHONPATH and a working
# interpreter are resolved consistently inside the runtime image.
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

PYTHON_BIN=${PYTHON:-}
if [ -z "$PYTHON_BIN" ]; then
    if command -v python3 >/dev/null 2>&1; then
        PYTHON_BIN=python3
    else
        PYTHON_BIN=python
    fi
fi

if [ -d "$REPO_ROOT/src" ]; then
    PYTHONPATH="$REPO_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
    export PYTHONPATH
fi

exec "$PYTHON_BIN" "$SCRIPT_DIR/clear_verify_required.py" "$@"
