# CLAUDE.md

Process memory for Claude Code agents. See `AGENTS.md` and `README.md`.

## Commands

```bash
# Dev
uvicorn src.api.main:app --reload

# DB (Always use CLI to ensure 14-digit format and single head; never hand-create migration files)
./scripts/development/migrate.sh generate "description"
./scripts/development/migrate.sh upgrade
./scripts/development/migrate.sh check-downgrades

# Format / lint
ruff format src/ tests/ && ruff check src/ && mypy src/

# Default CI-aligned tests — do not run bare unscoped `pytest` (import collisions)
pytest tests/unit --cov=src --cov-fail-under=65
```

## MUST-Follow Rules (Non-Inferable)

**DB Migrations = Use CLI generator & include rollback**
- Always generate via `./scripts/development/migrate.sh generate "<msg>"` or `python migrations/cli.py generate "<msg>"`.
- Never hand-craft migration revision IDs or filenames. IDs must strictly follow the 14-digit UTC timestamp pattern (`YYYYMMDDHHmmss`, e.g. `20260830152437`).
- Every migration must implement a valid `downgrade()` method.
- Follow Expand-Migrate-Contract: never delete active columns in the same release.

**Calories = backend is source of truth**
- Clients must not re-derive calories (`src/domain/services/meal_calorie_service.py`).

**Weekly budget `remaining_days` includes today**
- Mon=7, Tue=6, …, Sun=1. First-day check: `remaining_days >= 7`.

**Architecture**
- Domain has no outer I/O. Layer boundaries: `tests/architecture/` and `docs/system-architecture.md`. CQRS conventions: `docs/cqrs-guide.md`.

