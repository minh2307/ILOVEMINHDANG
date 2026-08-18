#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$PROJECT_ROOT/.venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
    echo "Virtual environment not found: $PYTHON" >&2
    exit 1
fi

exec "$PYTHON" "$PROJECT_ROOT/main.py" scheduler "$@"
