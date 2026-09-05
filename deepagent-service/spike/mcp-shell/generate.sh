#!/usr/bin/env bash
# THROWAWAY spike -- POST /chat in connector mode, stream SSE to out/chat-<ts>.log, extract the
# DASHBOARD_HTML event's `html` field into out/dashboard.html.
#
# Run from deepagent-service/: spike/mcp-shell/generate.sh
set -euo pipefail

SPIKE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="${SPIKE_ROOT}/out"
mkdir -p "${OUT_DIR}"

DEEPAGENT_URL="${DEEPAGENT_URL:-http://127.0.0.1:8000}"
AGENT_API_BEARER_TOKEN="${AGENT_API_BEARER_TOKEN:?set AGENT_API_BEARER_TOKEN (must match run-deepagent.sh)}"
PROMPT="${PROMPT:-Build a sales dashboard from the sales connector. Let the viewer choose the region(s) and the time window (7/30/90 days) with controls; show revenue trend, top products, region comparison, and a defect breakdown.}"
# Assumption: matches run-deepagent.sh's AGENT_WORKSPACE_ROOT default, used only as a fallback
# copy path -- the DASHBOARD_HTML SSE event carries the html inline, which is the primary source.
WORKSPACE_ROOT="${WORKSPACE_ROOT:-/tmp/erd-spike-workspace}"
# Assumption: app/agent/connectors/mcp_adapter.py's _build_headers() calls require_sso_token()/
# require_sso_url() unconditionally whenever any connector is configured, so both SSO headers
# must carry a non-empty dummy value even though the mock server does not check them.
SSO_TOKEN_HEADER_NAME="X-SSO-Token"
SSO_URL_HEADER_NAME="X-SSO-Url"
SSO_TOKEN_DUMMY_VALUE="spike-sso-token"
SSO_URL_DUMMY_VALUE="https://sso.invalid/spike"

TIMESTAMP="$(date +%s)"
SESSION_ID="spike-${TIMESTAMP}"
USER_ID="spike"
CHAT_LOG="${OUT_DIR}/chat-${TIMESTAMP}.log"
DASHBOARD_OUT="${OUT_DIR}/dashboard.html"

REQUEST_BODY="$(
  jq -n \
    --arg sessionId "${SESSION_ID}" \
    --arg userId "${USER_ID}" \
    --arg message "${PROMPT}" \
    '{
      sessionId: $sessionId,
      userId: $userId,
      message: $message,
      history: [],
      sources: [],
      connectors: [
        {id: "sales", name: "Sales", url: "http://127.0.0.1:8765/mcp", bearerTokenKey: null}
      ]
    }'
)"

echo "POST ${DEEPAGENT_URL}/chat  sessionId=${SESSION_ID}"
echo "streaming SSE to ${CHAT_LOG}"

curl -N -sS "${DEEPAGENT_URL}/chat" \
  -H "authorization: Bearer ${AGENT_API_BEARER_TOKEN}" \
  -H "${SSO_TOKEN_HEADER_NAME}: ${SSO_TOKEN_DUMMY_VALUE}" \
  -H "${SSO_URL_HEADER_NAME}: ${SSO_URL_DUMMY_VALUE}" \
  -H "content-type: application/json" \
  -d "${REQUEST_BODY}" \
  | tee "${CHAT_LOG}"

echo
echo "extracting DASHBOARD_HTML event from ${CHAT_LOG}"

EXTRACTED_HTML="$(
  grep -o '^data: .*' "${CHAT_LOG}" \
    | sed 's/^data: //' \
    | jq -rs '[.[] | select(.type == "DASHBOARD_HTML")] | last | .html // empty'
)"

if [ -n "${EXTRACTED_HTML}" ] && [ "${EXTRACTED_HTML}" != "null" ]; then
  printf '%s' "${EXTRACTED_HTML}" > "${DASHBOARD_OUT}"
  echo "wrote ${DASHBOARD_OUT} (from SSE DASHBOARD_HTML event)"
else
  echo "no DASHBOARD_HTML event found in SSE stream -- falling back to workspace file copy"
  # Assumption: SessionWorkspace root is <WORKSPACE_ROOT>/<userId>/sessions/<sessionId>/, per
  # app/engine/workspace.py prepare_local_layout() (NOT <WORKSPACE_ROOT>/<sessionId>/ as
  # initially assumed).
  WORKSPACE_DASHBOARD="${WORKSPACE_ROOT}/${USER_ID}/sessions/${SESSION_ID}/dashboard.html"
  if [ -f "${WORKSPACE_DASHBOARD}" ]; then
    cp "${WORKSPACE_DASHBOARD}" "${DASHBOARD_OUT}"
    echo "wrote ${DASHBOARD_OUT} (copied from ${WORKSPACE_DASHBOARD})"
  else
    echo "ERROR: no dashboard found in SSE stream nor at ${WORKSPACE_DASHBOARD}" >&2
    exit 1
  fi
fi
