# GitHub Copilot Instructions — MealTrack Backend

Process memory for Copilot. See `AGENTS.md` and `README.md`.

## Database Migration Rules (Strict)

- **Always use the CLI generator**:
  - `./scripts/development/migrate.sh generate "<msg>"`
  - Or `python migrations/cli.py generate "<msg>"`
  - (Use `--empty` flag if creating a template without an active database connection).
- **Never hand-craft migration files or revision IDs**:
  - Revision IDs must strictly follow the 14-digit UTC timestamp format `YYYYMMDDHHmmss` (e.g. `20260830152437`).
- **Every migration must implement `downgrade()`**:
  - All migrations must be fully reversible.
  - Merge migrations must use `pass`.
- **Single Head Requirement**:
  - Migration branches must be merged so the graph always resolves to a single head.
- **Verification**:
  - Run `python migrations/cli.py check-downgrades` to validate migration files.

## Architectural and Testing Rules

- **Calories source of truth**: Backend is source of truth (`src/domain/services/meal_calorie_service.py`).
- **Weekly budget**: `remaining_days` includes today (Mon=7, Tue=6, ..., Sun=1).
- **Testing**: Run `pytest tests/unit --cov=src --cov-fail-under=65`. Do not run bare unscoped `pytest`.
- **Linting & Formatting**: Run `ruff format src/ tests/ && ruff check src/ && mypy src/`.
