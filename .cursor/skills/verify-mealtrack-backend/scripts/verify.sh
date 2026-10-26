#!/usr/bin/env bash
# Launch, check, or stop one local MealTrack API for HTTP verification.
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$SCRIPT_DIR/../../../.." && pwd)
STATE=/tmp/mealtrack-verify
PORT="${VERIFY_PORT:-8000}"
BASE="http://127.0.0.1:${PORT}"
COMPOSE=(docker compose -f "$ROOT/docker-compose.yml" -f "$STATE/compose.override.yml")

die() { echo "verify: $*" >&2; exit 1; }

port_open() {
  python3 -c 'import socket,sys; s=socket.socket(); s.settimeout(0.3); sys.exit(0 if s.connect_ex(("127.0.0.1", int(sys.argv[1])))==0 else 1)' "$1"
}

container_label() {
  docker inspect -f '{{index .Config.Labels "mealtrack.verify"}}' "$1" 2>/dev/null || true
}

container_exists() {
  docker container inspect "$1" >/dev/null 2>&1
}

require_tools() {
  command -v docker >/dev/null 2>&1 || die "docker is not installed"
  docker info >/dev/null 2>&1 || die "docker is installed but the daemon is not reachable"
  docker compose version >/dev/null 2>&1 || die "docker compose is not available"
  command -v uv >/dev/null 2>&1 || die "uv is not on PATH"
  command -v python3 >/dev/null 2>&1 || die "python3 is not on PATH"
  command -v curl >/dev/null 2>&1 || die "curl is not on PATH"
  command -v openssl >/dev/null 2>&1 || die "openssl is not on PATH"
}

pid_alive() {
  local pid=$1
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

kill_tree() {
  local pid=$1 child
  [[ -n "$pid" ]] || return 0
  for child in $(pgrep -P "$pid" 2>/dev/null || true); do
    kill_tree "$child"
  done
  kill "$pid" 2>/dev/null || true
}

load_state() {
  [[ -f "$STATE/state.env" ]] || die "no verification instance (missing $STATE/state.env)"
  # shellcheck disable=SC1091
  source "$STATE/state.env"
}

write_firebase_scaffolding() {
  openssl genrsa -out "$STATE/key.pem" 2048 2>/dev/null
  python3 - "$STATE" <<'PY'
import json, pathlib, sys
state = pathlib.Path(sys.argv[1])
pem = (state / "key.pem").read_text()
doc = {
    "type": "service_account",
    "project_id": "mealtrack-verify",
    "private_key_id": "mealtrack-verify",
    "private_key": pem,
    "client_email": "mealtrack-verify@mealtrack-verify.iam.gserviceaccount.com",
    "client_id": "0",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
    "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
    "client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/mealtrack-verify",
}
(state / "firebase.json").write_text(json.dumps(doc))
PY
  rm -f "$STATE/key.pem"
  chmod 600 "$STATE/firebase.json"
}

write_env_file() {
  local token=$1
  cat >"$STATE/env.sh" <<EOF
export ENVIRONMENT=development
export ENABLE_DEV_AUTH_BYPASS=1
export APP_DATABASE_URL=postgresql://nutree:nutree@127.0.0.1:5432/nutree
export DATABASE_URL=postgresql://nutree:nutree@127.0.0.1:5432/nutree
export DATABASE_URL_DIRECT=postgresql://nutree:nutree@127.0.0.1:5432/nutree
export MIGRATION_DATABASE_URL=postgresql://nutree:nutree@127.0.0.1:5432/nutree
export DB_CONNECTION_MODE=direct_pool
export DB_USER=nutree
export DB_PASSWORD=nutree
export DB_HOST=127.0.0.1
export DB_PORT=5432
export DB_NAME=nutree
export REDIS_URL=redis://127.0.0.1:6379/0
export REDIS_HOST=127.0.0.1
export REDIS_PORT=6379
export CACHE_ENABLED=true
export FAIL_ON_CACHE_ERROR=false
export FAIL_ON_MIGRATION_ERROR=false
export AUTO_MIGRATE=false
export UVICORN_WORKERS=1
export AI_JOBS_ENABLED=false
export AI_JOBS_WORKER_ENABLED=false
export IMAGE_STORE_PROVIDER=local
export MONITORING_API_TOKEN=${token}
export FIREBASE_CREDENTIALS=$STATE/firebase.json
export GOOGLE_CLOUD_PROJECT=mealtrack-verify
export PORT=${PORT}
EOF
  chmod 600 "$STATE/env.sh"
}

refuse_foreign_stack() {
  local name label
  for name in mealtrack_postgres mealtrack_redis; do
    if container_exists "$name"; then
      label=$(container_label "$name")
      [[ "$label" == "1" ]] || die "$name already exists and was not started by this skill; refusing to touch it"
    fi
  done
  if port_open 5432 && ! container_exists mealtrack_postgres; then
    die "127.0.0.1:5432 is in use and mealtrack_postgres is not running"
  fi
  if port_open 6379 && ! container_exists mealtrack_redis; then
    die "127.0.0.1:6379 is in use and mealtrack_redis is not running"
  fi
  if port_open "$PORT"; then
    die "127.0.0.1:${PORT} is already in use"
  fi
}

cmd_cleanup() {
  local pid=""
  if [[ -f "$STATE/state.env" ]]; then
    # shellcheck disable=SC1091
    source "$STATE/state.env"
    pid="${PID:-}"
  fi
  if pid_alive "$pid"; then
    kill_tree "$pid"
    local i
    for i in 1 2 3 4 5 6 7 8 9 10; do
      pid_alive "$pid" || break
      sleep 0.3
    done
    if pid_alive "$pid"; then
      kill -9 "$pid" 2>/dev/null || true
    fi
  fi
  if [[ -f "$STATE/compose.override.yml" ]]; then
    local name label owned=1
    for name in mealtrack_postgres mealtrack_redis; do
      if container_exists "$name"; then
        label=$(container_label "$name")
        [[ "$label" == "1" ]] || owned=0
      fi
    done
    if [[ "$owned" == "1" ]]; then
      "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
    fi
  fi
  rm -rf "$STATE"
  echo "verify: cleaned up instance; evidence under .cursor/skills/verify-mealtrack-backend/artifacts/ was left in place"
}

cmd_doctor() {
  require_tools
  load_state
  pid_alive "${PID:-}" || die "recorded pid ${PID:-} is not running"
  local listener
  listener=$(python3 - "$PORT" "${PID}" <<'PY'
import os, sys
port, root = int(sys.argv[1]), int(sys.argv[2])
wanted = {root}
changed = True
while changed:
    changed = False
    for pid in list(wanted):
        try:
            raw = os.popen(f"pgrep -P {pid}").read().split()
        except Exception:
            raw = []
        for child in raw:
            child = int(child)
            if child not in wanted:
                wanted.add(child)
                changed = True
# /proc/net/tcp is local address in hex, port in hex.
hexport = f"{port:04X}"
owners = set()
for name in ("/proc/net/tcp", "/proc/net/tcp6"):
    try:
        lines = open(name).read().splitlines()[1:]
    except FileNotFoundError:
        continue
    for line in lines:
        parts = line.split()
        local, state, inode = parts[1], parts[3], parts[9]
        if state != "0A":
            continue
        if not local.endswith(":" + hexport):
            continue
        for pid in wanted:
            fd_dir = f"/proc/{pid}/fd"
            try:
                for fd in os.listdir(fd_dir):
                    try:
                        target = os.readlink(f"{fd_dir}/{fd}")
                    except OSError:
                        continue
                    if target == f"socket:[{inode}]":
                        owners.add(pid)
            except OSError:
                continue
if not owners:
    sys.exit(1)
print(" ".join(str(p) for p in sorted(owners)))
PY
  ) || die "port ${PORT} is not owned by pid ${PID}"
  local health root_body db_body
  health=$(curl -fsS "$BASE/health") || die "GET /health failed"
  python3 -c 'import json,sys; body=json.loads(sys.argv[1]); assert body.get("status")=="healthy"' "$health" \
    || die "GET /health status is not healthy: $health"
  root_body=$(curl -fsS "$BASE/") || die "GET / failed"
  python3 -c 'import json,sys; body=json.loads(sys.argv[1]); assert body.get("name")=="MealTrack API"' "$root_body" \
    || die "GET / name is not MealTrack API: $root_body"
  db_body=$(curl -fsS -H "X-Monitoring-Token: ${MONITORING_API_TOKEN}" "$BASE/v1/health/db-connections") \
    || die "GET /v1/health/db-connections failed"
  python3 -c 'import json,sys; body=json.loads(sys.argv[1]); assert body.get("status")=="healthy"' "$db_body" \
    || die "database connections are not healthy: $db_body"
  local name label health_state
  for name in mealtrack_postgres mealtrack_redis; do
    container_exists "$name" || die "$name is not running"
    label=$(container_label "$name")
    [[ "$label" == "1" ]] || die "$name is missing the mealtrack.verify=1 label"
    health_state=$(docker inspect -f '{{.State.Health.Status}}' "$name")
    [[ "$health_state" == "healthy" ]] || die "$name health is $health_state"
  done
  # shellcheck disable=SC1091
  source "$STATE/env.sh"
  [[ "${ENVIRONMENT}" == "development" && "${ENABLE_DEV_AUTH_BYPASS}" == "1" ]] \
    || die "instance env is not the local auth bypass"
  echo "doctor: ok"
  echo "base: $BASE"
  echo "evidence: ${EVIDENCE}"
  echo "pid: ${PID}"
  echo "port_owner: ${listener}"
  echo "db: healthy"
}

cmd_launch() {
  require_tools
  if [[ -f "$STATE/state.env" ]]; then
    # shellcheck disable=SC1091
    source "$STATE/state.env"
    if pid_alive "${PID:-}"; then
      die "an instance is already running as pid ${PID}; run cleanup before launching another"
    fi
  fi
  refuse_foreign_stack
  if container_exists mealtrack_postgres || container_exists mealtrack_redis; then
    die "a previous verification stack is still up; run cleanup first"
  fi

  mkdir -p "$STATE"
  cat >"$STATE/compose.override.yml" <<'EOF'
services:
  postgres:
    labels:
      mealtrack.verify: "1"
  redis:
    labels:
      mealtrack.verify: "1"
EOF
  local run_id evidence token
  run_id=$(date -u +%Y%m%dT%H%M%SZ)
  evidence="$ROOT/.cursor/skills/verify-mealtrack-backend/artifacts/${run_id}"
  mkdir -p "$evidence"
  token=$(openssl rand -hex 24)
  write_firebase_scaffolding
  write_env_file "$token"

  trap 'echo "verify: launch failed; cleaning up" >&2; cmd_cleanup >/dev/null 2>&1 || true' ERR
  echo "verify: starting postgres and redis"
  "${COMPOSE[@]}" up -d --wait --wait-timeout 180

  # shellcheck disable=SC1091
  source "$STATE/env.sh"
  cd "$ROOT"
  echo "verify: syncing python 3.13.2"
  uv python install 3.13.2
  uv sync --frozen
  echo "verify: initializing schema"
  uv run --offline --python 3.13.2 python scripts/init_postgres_db.py
  uv run --offline --python 3.13.2 python -m alembic upgrade head

  echo "verify: starting uvicorn"
  # nohup + disown so the server survives this script exiting.
  nohup uv run --offline --python 3.13.2 python -m uvicorn src.api.main:app \
    --host 127.0.0.1 --port "$PORT" >"$STATE/uvicorn.log" 2>&1 &
  local pid=$!
  disown "$pid" 2>/dev/null || true
  cat >"$STATE/state.env" <<EOF
PID=${pid}
PORT=${PORT}
BASE=${BASE}
EVIDENCE=${evidence}
MONITORING_API_TOKEN=${token}
EOF
  chmod 600 "$STATE/state.env"

  local i ready=0
  for i in $(seq 1 90); do
    if grep -F "MealTrack API started successfully!" "$STATE/uvicorn.log" >/dev/null 2>&1; then
      ready=1
      break
    fi
    if ! pid_alive "$pid"; then
      echo "verify: uvicorn exited" >&2
      tail -n 80 "$STATE/uvicorn.log" >&2 || true
      cmd_cleanup >/dev/null || true
      exit 1
    fi
    sleep 1
  done
  if [[ "$ready" != "1" ]]; then
    echo "verify: timed out waiting for startup" >&2
    tail -n 80 "$STATE/uvicorn.log" >&2 || true
    cmd_cleanup >/dev/null || true
    exit 1
  fi
  # Subshell so a failing doctor exits the subshell, not this launch.
  if ! ( cmd_doctor >/dev/null ); then
    echo "verify: process answered but doctor failed" >&2
    ( cmd_doctor >&2 ) || true
    tail -n 40 "$STATE/uvicorn.log" >&2 || true
    cmd_cleanup >/dev/null || true
    exit 1
  fi
  {
    echo "base=${BASE}"
    grep -F "MealTrack API started successfully!" "$STATE/uvicorn.log" | tail -n 1
    echo "health=$(curl -fsS "$BASE/health")"
    echo "db=$(curl -fsS -H "X-Monitoring-Token: ${token}" "$BASE/v1/health/db-connections")"
  } >"$evidence/launch.txt"
  trap - ERR
  echo "verify: ready"
  echo "base: $BASE"
  echo "evidence: $evidence"
  echo "pid: $pid"
}

usage() {
  echo "usage: verify.sh launch|doctor|cleanup" >&2
  exit 2
}

main() {
  local cmd=${1:-}
  case "$cmd" in
    launch) cmd_launch ;;
    doctor) cmd_doctor ;;
    cleanup) cmd_cleanup ;;
    *) usage ;;
  esac
}

main "$@"
