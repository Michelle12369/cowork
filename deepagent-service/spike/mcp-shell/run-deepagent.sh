#!/usr/bin/env bash
# THROWAWAY spike -- start the deepagent against the current checkout's one-local.properties
# (the service's own default resolves it relative to cwd, so this MUST be run from
# deepagent-service/). NEVER print/cat the properties file: it contains secrets.
#
# Port and workspace root come from one-local.properties, not env vars: the port is derived from
# DEV_DEEPAGENT_URL (default 8000 when the key is absent -- the same default scripts/dev_chat.py
# and spike/mcp-shell/bridge.py fall back to), and AGENT_WORKSPACE_ROOT is the file's own value
# if it sets one, else /tmp/erd-spike-workspace. Both are resolved by
# `scripts/dev_config.py --shell-exports`, which prints only these two KEY=value lines, so the
# parsing matches app.config exactly instead of a second, drifting bash parser.
# AGENT_API_BEARER_TOKEN and model/provider knobs (AGENT_MODEL, AGENT_PROVIDER_REQUIRE_PARAMETERS,
# ...) belong in one-local.properties too, read the normal Settings way (env still overrides
# those official keys if set) -- deepagent, dev_chat.py and bridge.py all read the same file.
# Run from deepagent-service/: spike/mcp-shell/run-deepagent.sh
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
