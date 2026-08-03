#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$PROJECT_ROOT/.venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
    echo "Virtual environment not found: $PYTHON" >&2
    exit 1
fi
if ! "$PYTHON" -c 'import playwright' >/dev/null 2>&1; then
    echo "Playwright is not installed in $PYTHON" >&2
    echo "Run: $PYTHON -m pip install -r $PROJECT_ROOT/requirements.txt" >&2
    exit 1
fi
cd "$PROJECT_ROOT"
if [[ $# -eq 1 && "$1" == http* ]]; then
    exec "$PYTHON" "$PROJECT_ROOT/main.py" create-job --url "$1"
fi
exec "$PYTHON" "$PROJECT_ROOT/main.py" orchestrator "$@"
