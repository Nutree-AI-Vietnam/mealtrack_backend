# GitHub Copilot Instructions — MealTrack Backend

Process memory for Copilot. See `AGENTS.md` and `README.md`.

## Database Migration Rules (Strict)

- **Always use the CLI generator**:
  - `./scripts/development/migrate.sh generate "<msg>"`
  - Or `python migrations/cli.py generate "<msg>"`
- **Never hand-craft** migration files, revision IDs, or filenames.
- **Every migration must implement `downgrade()`**.

## Other Core Rules

- See `AGENTS.md` for calories, weekly budget, architecture, and test commands.
