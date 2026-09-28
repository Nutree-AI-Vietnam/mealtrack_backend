#!/bin/bash
set -Eeuo pipefail

echo "Starting MealTrack locally (local PostgreSQL + Redis via Docker)..."

# ── 1. Virtual environment ────────────────────────────────────────────────────
HOST_PYTHON="${PYTHON_BIN:-}"
if [ -z "$HOST_PYTHON" ]; then
    for candidate in python3.13 python3 python; do
        if command -v "$candidate" >/dev/null 2>&1; then
            HOST_PYTHON="$candidate"
            break
        fi
    done
fi

if [ -z "$HOST_PYTHON" ] || ! command -v "$HOST_PYTHON" >/dev/null 2>&1; then
    echo "ERROR: Python 3.13.x is required. Install it or run with PYTHON_BIN=/path/to/python3.13." >&2
    exit 1
fi

HOST_PYTHON="$(command -v "$HOST_PYTHON")"
VENV_PYTHON=".venv/bin/python"
HOST_VERSION="$("$HOST_PYTHON" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')"

if ! "$HOST_PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 13) else 1)' >/dev/null 2>&1; then
    echo "ERROR: Python 3.13.x is required. Found Python $HOST_VERSION at $HOST_PYTHON." >&2
    echo "Set PYTHON_BIN=/path/to/python3.13 if your Python 3.13 binary has a custom name." >&2
    exit 1
fi

echo "Using Python $HOST_VERSION from $HOST_PYTHON"

if [ ! -x "$VENV_PYTHON" ]; then
    echo "Creating virtual environment..."
    rm -rf .venv
    "$HOST_PYTHON" -m venv .venv
fi

if ! "$VENV_PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 13) else 1)' >/dev/null 2>&1; then
    echo "Virtual environment is broken or not Python 3.13. Recreating..."
    rm -rf .venv
    "$HOST_PYTHON" -m venv .venv
fi

echo "Activating virtual environment..."
source .venv/bin/activate

if ! "$VENV_PYTHON" -m pip --version >/dev/null 2>&1; then
    echo "pip missing from virtual environment. Bootstrapping pip..."
    if ! "$VENV_PYTHON" -m ensurepip --upgrade; then
        echo "ERROR: pip is missing and ensurepip is unavailable. Reinstall Python 3.13 with venv/ensurepip support or set PYTHON_BIN to a complete Python install." >&2
        exit 1
    fi
fi

echo "Installing dependencies (dev/test)..."
"$VENV_PYTHON" -m pip install -r requirements-test.txt -q

# ── 2. PostgreSQL (local Docker) ──────────────────────────────────────────────
PG_CONTAINER="mealtrack_postgres"
PG_USER="nutree"
PG_PASSWORD="nutree"
PG_DB="nutree"
PG_PORT="5432"

if ! docker ps | grep -q "$PG_CONTAINER"; then
    echo "Starting PostgreSQL..."
    docker run -d --name "$PG_CONTAINER" \
        -e POSTGRES_USER="$PG_USER" \
        -e POSTGRES_PASSWORD="$PG_PASSWORD" \
        -e POSTGRES_DB="$PG_DB" \
        -p "$PG_PORT:5432" \
        pgvector/pgvector:pg16 2>/dev/null || docker start "$PG_CONTAINER"

    echo "Waiting for PostgreSQL to be ready..."
    sleep 4
fi

LOCAL_DATABASE_URL="postgresql://$PG_USER:$PG_PASSWORD@localhost:$PG_PORT/$PG_DB"

# .env can contain production or remote Neon URLs. Export every app and
# migration URL explicitly so the documented local workflow cannot fall back
# to a remote database through a higher-priority variable.
export APP_DATABASE_URL="$LOCAL_DATABASE_URL"
export DATABASE_URL="$LOCAL_DATABASE_URL"
export DATABASE_URL_DIRECT="$LOCAL_DATABASE_URL"
export MIGRATION_DATABASE_URL="$LOCAL_DATABASE_URL"
export DB_CONNECTION_MODE="direct_pool"
export ENVIRONMENT="development"

# ── 3. Redis ──────────────────────────────────────────────────────────────────
if ! docker ps | grep -q mealtrack_redis; then
    echo "Starting Redis..."
    docker run -d --name mealtrack_redis \
        -p 6379:6379 \
        redis:7-alpine 2>/dev/null || docker start mealtrack_redis

    echo "Waiting for Redis..."
    sleep 2
fi

# ── 4. Database initialisation ────────────────────────────────────────────────
echo "Running database setup..."
"$VENV_PYTHON" scripts/init_postgres_db.py

# ── 5. Migrations ─────────────────────────────────────────────────────────────
echo "Running Alembic migrations..."
"$VENV_PYTHON" -m alembic upgrade head

# ── 6. Start app ──────────────────────────────────────────────────────────────
echo ""
echo "Ready! Starting at http://localhost:8000"
echo "Docs at http://localhost:8000/docs"
echo ""
"$VENV_PYTHON" -m uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload
