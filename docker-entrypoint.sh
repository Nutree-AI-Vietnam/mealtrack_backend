#!/bin/bash
set -e

echo "🚀 MealTrack Backend - Starting application..."

# Function to log messages
log() {
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] $1"
}

# Schema upgrades on Render are owned by GitHub Actions (migrate.yml).
# Skip auto-migrate on Render (any ENV) and when ENV is production.
if [ "${RENDER:-}" = "true" ]; then
    log "⏭️ Skipping migrations (Render; use GitHub Actions migrate workflow)"
elif [ "${ENV:-}" = "production" ] || [ "${ENVIRONMENT:-}" = "production" ]; then
    log "⏭️ Skipping migrations (production ENV; use GitHub Actions migrate workflow)"
else
    AUTO_MIGRATE="${AUTO_MIGRATE:-true}"
    AUTO_MIGRATE_NORMALIZED="$(printf '%s' "$AUTO_MIGRATE" | tr '[:upper:]' '[:lower:]')"
    case "$AUTO_MIGRATE_NORMALIZED" in
        1|true|yes|on)
            RUN_MIGRATIONS=true
            ;;
        0|false|no|off)
            RUN_MIGRATIONS=false
            ;;
        *)
            log "❌ AUTO_MIGRATE must be one of: true, false, 1, 0, yes, no, on, off"
            exit 1
            ;;
    esac

    if [ "$RUN_MIGRATIONS" = true ]; then
        log "📦 Running database migrations..."
        if python migrations/run.py; then
            log "✅ Migrations completed successfully"
        else
            log "❌ Migrations failed!"
            exit 1
        fi
    else
        log "⏭️ Skipping migrations (AUTO_MIGRATE=${AUTO_MIGRATE})"
    fi
fi

# Render defaults web services to port 10000 if a custom port is not configured.
# Local Docker defaults to 8000.
if [ -z "${PORT:-}" ]; then
    if [ "${RENDER:-}" = "true" ]; then
        PORT="10000"
    else
        PORT="8000"
    fi
fi

# Start the application
log "🚀 Starting FastAPI application on port ${PORT}..."
WORKERS="${UVICORN_WORKERS:-4}"
DB_MODE="${DB_CONNECTION_MODE:-auto}"
log "Uvicorn workers: ${WORKERS}"
log "DB_CONNECTION_MODE: ${DB_MODE} (empty/auto = detect from APP_DATABASE_URL host)"

# Restart each worker after a jittered number of requests. Python keeps its
# memory high-water mark, so a worker's RSS climbs for days between deploys.
# Uvicorn's supervisor starts a replacement at once while the other workers
# keep serving. A single worker is the whole server, so it is never recycled.
# Set WORKER_MAX_REQUESTS=0 to turn recycling off. These names avoid the
# UVICORN_ prefix on purpose: uvicorn reads UVICORN_* variables by itself, so
# UVICORN_LIMIT_MAX_REQUESTS=0 would make every worker exit at once.
MAX_REQUESTS="${WORKER_MAX_REQUESTS:-50000}"
MAX_REQUESTS_JITTER="${WORKER_MAX_REQUESTS_JITTER:-25000}"
for value in "$WORKERS" "$MAX_REQUESTS" "$MAX_REQUESTS_JITTER"; do
    case "$value" in
        ''|*[!0-9]*)
            log "❌ UVICORN_WORKERS, WORKER_MAX_REQUESTS and WORKER_MAX_REQUESTS_JITTER must be whole numbers"
            exit 1
            ;;
    esac
done
RECYCLE_ARGS=()
if [ "$WORKERS" -gt 1 ] && [ "$MAX_REQUESTS" -gt 0 ]; then
    RECYCLE_ARGS=(--limit-max-requests "$MAX_REQUESTS" --limit-max-requests-jitter "$MAX_REQUESTS_JITTER")
    log "Worker recycling: after ${MAX_REQUESTS}-$((MAX_REQUESTS + MAX_REQUESTS_JITTER)) requests"
else
    log "Worker recycling: off"
fi

exec uvicorn src.api.main:app \
    --host 0.0.0.0 \
    --port "$PORT" \
    --workers "$WORKERS" \
    --loop uvloop \
    "${RECYCLE_ARGS[@]}"
