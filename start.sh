#!/usr/bin/env bash
# Start the gateway API and the dashboard together; Ctrl+C stops both.
#
#   bash start.sh
#   BACKEND_PORT=7700 FRONTEND_PORT=5200 bash start.sh
#
# Defaults: API on 7632, dashboard on http://localhost:5173. Port 8000 is refused.
set -euo pipefail

BACKEND_PORT="${BACKEND_PORT:-7632}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

for port in "$BACKEND_PORT" "$FRONTEND_PORT"; do
  if [[ "$port" == "8000" ]]; then
    echo "Port 8000 is reserved; pick another BACKEND_PORT / FRONTEND_PORT." >&2
    exit 1
  fi
done
if [[ "$BACKEND_PORT" == "$FRONTEND_PORT" ]]; then
  echo "BACKEND_PORT and FRONTEND_PORT must differ." >&2
  exit 1
fi
for tool in uv npm; do
  command -v "$tool" >/dev/null || { echo "$tool is not installed." >&2; exit 1; }
done

cd "$ROOT"
uv sync --quiet
[[ -d frontend/node_modules ]] || (cd frontend && npm install)

pids=()
cleanup() {
  trap - INT TERM EXIT
  for pid in ${pids[@]+"${pids[@]}"}; do kill "$pid" 2>/dev/null || true; done
  wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

uv run uvicorn ugen_ai_gateway.main:app --host 0.0.0.0 --port "$BACKEND_PORT" &
pids+=($!)
(cd frontend && UGEN_BACKEND_PORT="$BACKEND_PORT" exec npm run dev -- --port "$FRONTEND_PORT" --strictPort) &
pids+=($!)

echo "API:       http://localhost:$BACKEND_PORT"
echo "Dashboard: http://localhost:$FRONTEND_PORT"

# Stop everything as soon as either process exits (no `wait -n`: macOS ships bash 3.2).
while kill -0 "${pids[0]}" 2>/dev/null && kill -0 "${pids[1]}" 2>/dev/null; do
  sleep 1
done
