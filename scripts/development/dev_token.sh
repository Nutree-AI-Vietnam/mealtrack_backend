#!/bin/bash
# Shortcut to get Firebase token for Alex, Tony, Dev, or custom email
# Usage:
#   ./scripts/development/dev_token.sh alex
#   ./scripts/development/dev_token.sh tony
#   ./scripts/development/dev_token.sh dev
#   ./scripts/development/dev_token.sh custom@example.com

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

TARGET="${1:-alex}"

if [ -f "$BACKEND_DIR/.venv/bin/python" ]; then
    PYTHON_BIN="$BACKEND_DIR/.venv/bin/python"
else
    PYTHON_BIN="python3"
fi

if [[ "$TARGET" =~ ^(alex|tony|dev)$ ]]; then
    "$PYTHON_BIN" "$SCRIPT_DIR/get_firebase_token.py" --user "$TARGET" "${@:2}"
else
    "$PYTHON_BIN" "$SCRIPT_DIR/get_firebase_token.py" --email "$TARGET" "${@:2}"
fi
