#!/usr/bin/env bash
# THROWAWAY spike -- drive the deepagent in connector mode through the stateful dev client
# (scripts/dev_chat.py). First run opens a session against the mock `sales` connector; later runs
# continue the same session (history + previousDashboardHtml carried automatically), so you can
# iterate on the dashboard turn by turn. Writes out/dashboard.html for bridge.py to serve.
#
# Run from deepagent-service/:
#   spike/mcp-shell/generate.sh                      # first turn with the default PROMPT
#   spike/mcp-shell/generate.sh "make the trend a bar chart"   # follow-up turn, same session
#   NEW=1 spike/mcp-shell/generate.sh                # discard the session and start over
#
# Env knobs: DEEPAGENT_URL (default http://127.0.0.1:8000), AGENT_API_BEARER_TOKEN (required,
# must match run-deepagent.sh), MOCK_MCP_URL (default http://127.0.0.1:8765/mcp), PROMPT,
# NEW=1, DEV_SSO_TOKEN / DEV_SSO_URL (optional; dummy values are sent otherwise).
set -euo pipefail

SPIKE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_ROOT="$(cd "${SPIKE_ROOT}/../.." && pwd)"
OUT_DIR="${SPIKE_ROOT}/out"
STATE_DIR="${OUT_DIR}/.dev-session"
DASHBOARD_OUT="${OUT_DIR}/dashboard.html"
DEV_CHAT="${SERVICE_ROOT}/scripts/dev_chat.py"
mkdir -p "${OUT_DIR}"

DEEPAGENT_URL="${DEEPAGENT_URL:-http://127.0.0.1:8000}"
MOCK_MCP_URL="${MOCK_MCP_URL:-http://127.0.0.1:8765/mcp}"
DEFAULT_PROMPT="Build a sales dashboard from the sales connector. Let the viewer choose the region(s) and the time window (7/30/90 days) with controls; show revenue trend, top products, region comparison, and a defect breakdown."
MESSAGE="${1:-${PROMPT:-${DEFAULT_PROMPT}}}"

log()  { printf '[generate] %s\n' "$*"; }
fail() { printf '[generate] ERROR: %s\n' "$*" >&2; exit 1; }

# ---- preflight -------------------------------------------------------------------------------
log "service root: ${SERVICE_ROOT}"
log "state dir:    ${STATE_DIR}"
log "dashboard:    ${DASHBOARD_OUT}"

[ -f "${DEV_CHAT}" ] || fail "dev client missing: ${DEV_CHAT}"
command -v uv >/dev/null 2>&1 || fail "uv not on PATH (install: https://docs.astral.sh/uv/)"
command -v curl >/dev/null 2>&1 || fail "curl not on PATH"

if [ -z "${AGENT_API_BEARER_TOKEN:-}" ]; then
  fail "AGENT_API_BEARER_TOKEN is not set. It must equal the value run-deepagent.sh started with (default there: spike-token)."
fi
log "bearer token: set (${#AGENT_API_BEARER_TOKEN} chars; value not shown)"

log "checking deepagent at ${DEEPAGENT_URL}/health ..."
HEALTH_STATUS="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 "${DEEPAGENT_URL}/health" || true)"
HEALTH_STATUS="${HEALTH_STATUS:-000}"
if [ "${HEALTH_STATUS}" != "200" ]; then
  fail "deepagent not healthy at ${DEEPAGENT_URL} (HTTP ${HEALTH_STATUS}). Start it: spike/mcp-shell/run-deepagent.sh (port via DEEPAGENT_PORT, then set DEEPAGENT_URL here to match)."
fi
log "deepagent: ok"

# The mock MCP server answers on the streamable-HTTP endpoint; any HTTP status (even 4xx for a
# bare GET) proves the process is up. Connection refused is the failure we want to catch.
log "checking mock MCP server at ${MOCK_MCP_URL} ..."
MOCK_STATUS="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 "${MOCK_MCP_URL}" || true)"
MOCK_STATUS="${MOCK_STATUS:-000}"
if [ "${MOCK_STATUS}" = "000" ]; then
  fail "mock MCP server unreachable at ${MOCK_MCP_URL}. Start it: uv run python spike/mcp-shell/mock_server.py"
fi
log "mock MCP server: up (HTTP ${MOCK_STATUS} on bare GET is expected)"

# ---- session mode ----------------------------------------------------------------------------
EXTRA_ARGS=()
if [ "${NEW:-0}" = "1" ] || [ ! -f "${STATE_DIR}/state.json" ]; then
  log "mode: NEW session (connector sales -> ${MOCK_MCP_URL})"
  EXTRA_ARGS+=(--new --connector sales "${MOCK_MCP_URL}" Sales)
else
  SESSION_ID="$(sed -n 's/.*"sessionId": *"\([^"]*\)".*/\1/p' "${STATE_DIR}/state.json" | head -1)"
  TURN_COUNT="$(grep -c '"role": "user"' "${STATE_DIR}/state.json" || true)"
  log "mode: CONTINUE session ${SESSION_ID:-?} (turns so far: ${TURN_COUNT:-0}; NEW=1 to reset)"
  if [ -f "${STATE_DIR}/dashboard.html" ]; then
    log "previous dashboard will be sent as previousDashboardHtml ($(wc -c < "${STATE_DIR}/dashboard.html") bytes)"
  else
    log "no previous dashboard in state dir -- this turn starts from scratch"
  fi
fi

# out/dashboard.html is a tracked snapshot and may pre-exist; a marker touched now tells a fresh
# write from a stale file afterwards (portable across GNU/BSD stat).
TURN_MARKER="${STATE_DIR}/.turn-started"
mkdir -p "${STATE_DIR}"
touch "${TURN_MARKER}"

log "message: ${MESSAGE}"
log "running dev_chat.py (raw SSE log lands in ${STATE_DIR}/chat-<ts>.log) ..."
echo "----------------------------------------------------------------------------------------"

set +e
(
  cd "${SERVICE_ROOT}" &&
  uv run scripts/dev_chat.py \
    --base-url "${DEEPAGENT_URL}" \
    --state-dir "${STATE_DIR}" \
    --user-id spike \
    --dashboard-out "${DASHBOARD_OUT}" \
    "${MESSAGE}" \
    "${EXTRA_ARGS[@]}"   # message first: --connector takes a variable-length list
)
DEV_CHAT_EXIT=$?
set -e

echo "----------------------------------------------------------------------------------------"

# ---- diagnostics -----------------------------------------------------------------------------
LATEST_LOG="$(find "${STATE_DIR}" -maxdepth 1 -name 'chat-*.log' -newer "${TURN_MARKER}" 2>/dev/null | head -1 || true)"

if [ "${DEV_CHAT_EXIT}" -ne 0 ]; then
  log "dev_chat.py exited with ${DEV_CHAT_EXIT}"
  if [ -n "${LATEST_LOG}" ]; then
    ERROR_EVENTS="$(grep '^data:' "${LATEST_LOG}" | sed 's/^data: //' | grep '"type": *"ERROR"' || true)"
    if [ -n "${ERROR_EVENTS}" ]; then
      log "ERROR events in ${LATEST_LOG}:"
      printf '%s\n' "${ERROR_EVENTS}" | sed 's/^/    /'
    fi
    STEP_ERRORS="$(grep '^data:' "${LATEST_LOG}" | sed 's/^data: //' | grep '"type": *"STEP"' | grep '"status": *"ERROR"' || true)"
    if [ -n "${STEP_ERRORS}" ]; then
      log "STEP events that failed:"
      printf '%s\n' "${STEP_ERRORS}" | sed 's/^/    /'
    fi
    log "last 5 raw SSE lines of ${LATEST_LOG}:"
    tail -n 5 "${LATEST_LOG}" | cut -c1-300 | sed 's/^/    /'
  else
    log "no raw SSE log was written -- the request never reached the streaming phase (auth/connect failure above)."
  fi
  fail "turn failed; see messages above"
fi

if [ -z "$(find "${DASHBOARD_OUT}" -newer "${TURN_MARKER}" 2>/dev/null)" ]; then
  log "turn completed but no DASHBOARD_HTML event arrived -- the model answered without emitting a dashboard."
  if [ -f "${DASHBOARD_OUT}" ]; then
    log "${DASHBOARD_OUT} is unchanged from before this turn (stale)."
  fi
  log "check the ANSWER text above (it may have asked a question); reply with another turn."
  exit 2
fi

log "wrote ${DASHBOARD_OUT} ($(wc -c < "${DASHBOARD_OUT}") bytes)"
log "next: open http://127.0.0.1:8766 and click 'Load /api/dashboard' (bridge.py must be running)"
