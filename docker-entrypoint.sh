#!/bin/bash
set -e

echo "🚀 MealTrack Backend - Starting application..."

# Function to log messages
log() {
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] $1"
}

# Run database migrations unless disabled or handled by the production pre-deploy.
if [ "${ENV:-}" = "production" ] || [ "${ENVIRONMENT:-}" = "production" ]; then
    log "⏭️ Skipping migrations (pre-deploy handles this)"
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
log "Uvicorn workers: ${WORKERS}"
exec uvicorn src.api.main:app \
    --host 0.0.0.0 \
    --port "$PORT" \
    --workers "$WORKERS" \
    --loop uvloop
