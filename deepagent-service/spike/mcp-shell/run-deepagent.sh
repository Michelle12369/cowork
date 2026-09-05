#!/usr/bin/env bash
# THROWAWAY spike -- start the deepagent on :8000 against the main checkout's one-local.properties
# (OpenRouter creds). NEVER print/cat the properties file: it contains secrets.
# Run from deepagent-service/: spike/mcp-shell/run-deepagent.sh
set -euo pipefail

export ONE_PROPERTIES_PATH="${ONE_PROPERTIES_PATH:-/Users/melody/Code/cowork/deepagent-service/one-local.properties}"
export AGENT_API_BEARER_TOKEN="${AGENT_API_BEARER_TOKEN:-spike-token}"
export AGENT_WORKSPACE_ROOT="${AGENT_WORKSPACE_ROOT:-/tmp/erd-spike-workspace}"
mkdir -p "${AGENT_WORKSPACE_ROOT}"

echo "deepagent :8000  properties=${ONE_PROPERTIES_PATH}  workspace=${AGENT_WORKSPACE_ROOT}"
exec uv run uvicorn app.main:app --host 127.0.0.1 --port "${DEEPAGENT_PORT:-8000}"
