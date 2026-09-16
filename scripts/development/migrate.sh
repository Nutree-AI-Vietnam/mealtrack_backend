#!/bin/bash
#
# Migration CLI wrapper
#
# Usage:
#   ./scripts/development/migrate.sh generate "Add user preferences"
#   ./scripts/development/migrate.sh upgrade
#   ./scripts/development/migrate.sh downgrade
#   ./scripts/development/migrate.sh test
#   ./scripts/development/migrate.sh status

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

cd "$PROJECT_ROOT"

if [ $# -eq 0 ]; then
    echo "Usage: $0 <command> [args]"
    echo ""
    echo "Commands:"
    echo "  generate <message>  Generate new migration"
    echo "  upgrade             Apply pending migrations"
    echo "  downgrade           Rollback last migration"
    echo "  test                Test upgrade/downgrade cycle"
    echo "  status              Show migration status"
    exit 1
fi

if [ -f "$PROJECT_ROOT/.venv/bin/python" ]; then
    PYTHON_CMD="$PROJECT_ROOT/.venv/bin/python"
elif command -v python &> /dev/null; then
    PYTHON_CMD="python"
else
    PYTHON_CMD="python3"
fi

"$PYTHON_CMD" migrations/cli.py "$@"
