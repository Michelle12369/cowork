#!/usr/bin/env bash
# THROWAWAY spike -- start the deepagent on :8000 against the current checkout's
# one-local.properties (the service's own default resolves it relative to cwd, so this MUST be
# run from deepagent-service/). NEVER print/cat the properties file: it contains secrets.
# AGENT_API_BEARER_TOKEN now lives in one-local.properties too, so deepagent, dev_chat.py and
# bridge.py all read the same value from one place; an env var still overrides it here if set.
# Run from deepagent-service/: spike/mcp-shell/run-deepagent.sh
set -euo pipefail

export AGENT_WORKSPACE_ROOT="${AGENT_WORKSPACE_ROOT:-/tmp/erd-spike-workspace}"
mkdir -p "${AGENT_WORKSPACE_ROOT}"

echo "deepagent :8000  properties=${ONE_PROPERTIES_PATH:-one-local.properties (cwd default)}  workspace=${AGENT_WORKSPACE_ROOT}"
# --reload: the agent code is under active edit during the spike; restart on save so
# scripts/dev_chat.py always hits the current tree. Reload watches app/ only (skills/spike
# changes need no restart).
exec uv run uvicorn app.main:app --host 127.0.0.1 --port "${DEEPAGENT_PORT:-8000}" --reload --reload-dir app
