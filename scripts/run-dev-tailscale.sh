#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND_DIR="$ROOT_DIR/frontend"
TAILSCALE_HOME="${TAILSCALE_HOME:-$HOME/.local/opt/tailscale}"
TAILSCALE_STATE_DIR="${TAILSCALE_STATE_DIR:-$HOME/.local/state/tailscale}"
TAILSCALE_BIN="$TAILSCALE_HOME/tailscale"
TAILSCALED_BIN="$TAILSCALE_HOME/tailscaled"
TAILSCALE_SOCKET="$TAILSCALE_STATE_DIR/tailscaled.sock"
RUNTIME_DIR="${XDG_RUNTIME_DIR:-/tmp}/trading-webapp-${UID}"
TAILSCALED_LOG="$RUNTIME_DIR/tailscaled.log"

BACKEND_PID=""
FRONTEND_PID=""
TAILSCALED_PID=""
TAILSCALED_OWNED=0

log() {
  printf '[trading-webapp] %s\n' "$*"
}

fail() {
  printf '[trading-webapp] ERROR: %s\n' "$*" >&2
  exit 1
}

stop_group() {
  local pid="$1"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
  fi
}

cleanup() {
  local exit_code=$?
  trap - EXIT INT TERM
  log "Stopping services..."
  stop_group "$FRONTEND_PID"
  stop_group "$BACKEND_PID"
  if [[ "$TAILSCALED_OWNED" -eq 1 ]]; then
    stop_group "$TAILSCALED_PID"
  fi
  wait 2>/dev/null || true
  exit "$exit_code"
}
trap cleanup EXIT INT TERM

wait_for_command() {
  local description="$1"
  shift
  for _ in {1..100}; do
    if "$@" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.1
  done
  fail "$description did not become ready"
}

for command in uv npm curl python3 setsid; do
  command -v "$command" >/dev/null 2>&1 || fail "Required command not found: $command"
done

[[ -x "$TAILSCALE_BIN" ]] || fail "Tailscale CLI not found at $TAILSCALE_BIN"
[[ -x "$TAILSCALED_BIN" ]] || fail "Tailscale daemon not found at $TAILSCALED_BIN"
[[ -x "$ROOT_DIR/.venv/bin/uvicorn" ]] || fail "Backend dependencies missing; run: cd '$ROOT_DIR' && uv sync --extra dev"
[[ -x "$FRONTEND_DIR/node_modules/.bin/vite" ]] || fail "Frontend dependencies missing; run: cd '$FRONTEND_DIR' && npm ci"

for port in 8000 5173; do
  if ss -ltn "sport = :$port" 2>/dev/null | grep -q LISTEN; then
    fail "Port $port is already in use. Stop the existing app process first."
  fi
done

mkdir -p "$TAILSCALE_STATE_DIR" "$RUNTIME_DIR"

if "$TAILSCALE_BIN" --socket="$TAILSCALE_SOCKET" status >/dev/null 2>&1; then
  log "Reusing the running rootless Tailscale daemon."
else
  rm -f "$TAILSCALE_SOCKET"
  log "Starting the rootless Tailscale daemon..."
  setsid "$TAILSCALED_BIN" \
    --tun=userspace-networking \
    --state="$TAILSCALE_STATE_DIR/tailscaled.state" \
    --socket="$TAILSCALE_SOCKET" \
    >"$TAILSCALED_LOG" 2>&1 &
  TAILSCALED_PID=$!
  TAILSCALED_OWNED=1
  wait_for_command "Tailscale daemon" "$TAILSCALE_BIN" --socket="$TAILSCALE_SOCKET" status
fi

TAILSCALE_STATE="$($TAILSCALE_BIN --socket="$TAILSCALE_SOCKET" status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["BackendState"])')"
if [[ "$TAILSCALE_STATE" == "NeedsLogin" ]]; then
  log "Tailscale authentication is required; follow the URL printed below."
  "$TAILSCALE_BIN" --socket="$TAILSCALE_SOCKET" up --hostname=trading-webapp
elif [[ "$TAILSCALE_STATE" != "Running" ]]; then
  log "Waiting for Tailscale to become ready..."
  for _ in {1..100}; do
    TAILSCALE_STATE="$($TAILSCALE_BIN --socket="$TAILSCALE_SOCKET" status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["BackendState"])')"
    [[ "$TAILSCALE_STATE" == "Running" ]] && break
    sleep 0.1
  done
  [[ "$TAILSCALE_STATE" == "Running" ]] || fail "Tailscale did not reach the Running state"
fi

log "Starting FastAPI on http://127.0.0.1:8000 ..."
(
  cd "$ROOT_DIR"
  exec setsid uv run uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
) &
BACKEND_PID=$!

log "Starting Vite on http://127.0.0.1:5173 ..."
(
  cd "$FRONTEND_DIR"
  exec setsid npm run dev -- --host 127.0.0.1 --port 5173
) &
FRONTEND_PID=$!

wait_for_command "FastAPI" curl -fsS http://127.0.0.1:8000/openapi.json
wait_for_command "Vite" curl -fsS http://127.0.0.1:5173/

"$TAILSCALE_BIN" --socket="$TAILSCALE_SOCKET" serve --bg http://127.0.0.1:5173 >/dev/null
TAILNET_URL="$($TAILSCALE_BIN --socket="$TAILSCALE_SOCKET" status --json | python3 -c 'import json,sys; print("https://" + json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')"

log "Ready."
log "Local URL:   http://127.0.0.1:5173"
log "Tailnet URL: $TAILNET_URL"
log "Press Ctrl+C to stop all services."

if [[ "$TAILSCALED_OWNED" -eq 1 ]]; then
  wait -n "$BACKEND_PID" "$FRONTEND_PID" "$TAILSCALED_PID"
else
  wait -n "$BACKEND_PID" "$FRONTEND_PID"
fi
fail "A service exited unexpectedly"
