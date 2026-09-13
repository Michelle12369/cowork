#!/usr/bin/env bash
# THROWAWAY spike -- start the deepagent from deepagent-service/ against one-local.properties.
# NEVER print/cat the properties file: it contains secrets.
#
# Everything comes from the properties file (env vars still override official Settings keys):
#   port            from DEV_DEEPAGENT_URL, default 8000 (same default as dev_chat.py/bridge.py)
#   workspace root  AGENT_WORKSPACE_ROOT if set, else /tmp/erd-spike-workspace
#   bearer token, AGENT_MODEL, AGENT_PROVIDER_REQUIRE_PARAMETERS ...  the normal Settings keys
# `scripts/dev_config.py --shell-exports` resolves the first two with app.config's parser and
# prints only those two KEY=value lines.
set -euo pipefail

SHELL_EXPORTS="$(uv run python scripts/dev_config.py --shell-exports)"
DEEPAGENT_PORT="$(printf '%s\n' "${SHELL_EXPORTS}" | sed -n 's/^DEEPAGENT_PORT=//p')"
AGENT_WORKSPACE_ROOT="$(printf '%s\n' "${SHELL_EXPORTS}" | sed -n 's/^AGENT_WORKSPACE_ROOT=//p')"
export AGENT_WORKSPACE_ROOT
mkdir -p "${AGENT_WORKSPACE_ROOT}"

echo "deepagent :${DEEPAGENT_PORT}  properties=${ONE_PROPERTIES_PATH:-one-local.properties (cwd default)}  workspace=${AGENT_WORKSPACE_ROOT}"
# --reload: the agent code is under active edit during the spike; restart on save so
# scripts/dev_chat.py always hits the current tree. Reload watches app/ only (skills/spike
# changes need no restart).
exec uv run uvicorn app.main:app --host 127.0.0.1 --port "${DEEPAGENT_PORT}" --reload --reload-dir app
