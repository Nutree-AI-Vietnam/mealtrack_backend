#!/bin/bash
#
# Migration CLI wrapper
#
# Usage:
#   ./scripts/development/migrate.sh generate "Add user preferences"
#   ./scripts/development/migrate.sh upgrade
#   ./scripts/development/migrate.sh downgrade
#   ./scripts/development/migrate.sh rollback 20260829000001
#   ./scripts/development/migrate.sh test
#   ./scripts/development/migrate.sh status
#   ./scripts/development/migrate.sh check-downgrades

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

cd "$PROJECT_ROOT"

if [ $# -eq 0 ]; then
    echo "Usage: $0 <command> [args]"
    echo ""
    echo "Commands:"
    echo "  generate <message>       Generate new migration with 14-digit YYYYMMDDHHmmss ID"
    echo "  upgrade [--target <rev>] Apply pending migrations"
    echo "  downgrade [--steps <n>]  Rollback migrations by step count or target"
    echo "  rollback <target_rev>    Rollback to a specific target revision"
    echo "  test                     Test upgrade/downgrade cycle"
    echo "  status                   Show migration status and orphan diagnostics"
    echo "  check-downgrades         Validate naming, single head, and downgrade support"
    exit 1
fi

python migrations/cli.py "$@"
