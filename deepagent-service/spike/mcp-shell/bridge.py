"""THROWAWAY spike -- shell host FastAPI app, 127.0.0.1:8766.

Run: ``uv run python spike/mcp-shell/bridge.py`` (from ``deepagent-service/``).

Serves ``shell.html`` and brokers the iframe's ``mcp(connector, tool, args, handler)`` calls to
real MCP servers via ``fastmcp.Client`` -- mirrors ``app/agent/connectors/mcp_adapter.py``'s
``_call``/``_extract_tool_payload`` (same ``Client`` + ``StreamableHttpTransport`` shape, same
"no unwrap of FastMCP's ``{'result': ...}`` envelope for list-returning tools" behaviour, since
that is what the agent itself sees when it explores tools -- see README assumption notes).
"""

import logging
import os
import re
import time
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from pydantic import BaseModel

from app.config import get_settings

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("bridge")

_HOST = "127.0.0.1"
_PORT = 8766
_SPIKE_ROOT = Path(__file__).parent
_SHELL_HTML_PATH = _SPIKE_ROOT / "shell.html"
_DEFAULT_DASHBOARD_PATH = _SPIKE_ROOT / "out" / "dashboard.html"
_REQUEST_TIMEOUT_SECONDS = 30.0

_CONNECTORS: dict[str, str] = {"sales": "http://127.0.0.1:8765/mcp"}

# Internal runtime (AGENT_RUNTIME=internal in one-local.properties, or the env var): the network
# blocks the public CDNs, so do what the Java serve path does -- rewrite the known CDN URLs to
# /vendor/... and serve the repo's vendored copies. Same two rules as
# backend/src/main/resources/application.properties (erd.artifact.rewrite.profiles.tw3-ec5).
_INTERNAL_RUNTIME_NAME = "internal"
_VENDOR_DIR = _SPIKE_ROOT.parents[2] / "frontend" / "public" / "vendor"
_CDN_REWRITE_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"""https://cdn\.tailwindcss\.com[^"']*"""), "/vendor/tailwind-play-v3.js"),
    (
        re.compile(r"""https://cdn\.jsdelivr\.net/npm/echarts@5[^"']*"""),
        "/vendor/echarts-v5.min.js",
    ),
)


def _vendor_assets_enabled() -> bool:
    return get_settings().AGENT_RUNTIME == _INTERNAL_RUNTIME_NAME


def _rewrite_cdn_urls(html: str) -> str:
    for pattern, replacement in _CDN_REWRITE_RULES:
        html = pattern.sub(replacement, html)
    return html


def _serve_html(html: str) -> HTMLResponse:
    return HTMLResponse(content=_rewrite_cdn_urls(html) if _VENDOR_ASSETS else html)


_VENDOR_ASSETS = _vendor_assets_enabled()

app = FastAPI(title="mcp-shell bridge (spike, throwaway)")
if _VENDOR_ASSETS:
    if not _VENDOR_DIR.is_dir():
        raise RuntimeError(
            f"AGENT_RUNTIME={_INTERNAL_RUNTIME_NAME} but vendored assets not found at {_VENDOR_DIR}"
        )
    app.mount("/vendor", StaticFiles(directory=_VENDOR_DIR), name="vendor")
logger.info(
    "vendor assets %s (AGENT_RUNTIME=%s, properties=%s)",
    "ON: CDN URLs rewritten to /vendor/" if _VENDOR_ASSETS else "off: CDN URLs served as-is",
    get_settings().AGENT_RUNTIME,
    os.environ.get("ONE_PROPERTIES_PATH", "one-local.properties"),
)


class McpCallRequest(BaseModel):
    connector: str
    tool: str
    args: dict[str, Any] = {}


def _dashboard_path() -> Path:
    override = os.environ.get("DASHBOARD_HTML")
    return Path(override) if override else _DEFAULT_DASHBOARD_PATH


@app.get("/")
def serve_shell() -> HTMLResponse:
    return _serve_html(_SHELL_HTML_PATH.read_text(encoding="utf-8"))


@app.get("/api/dashboard")
def serve_dashboard() -> HTMLResponse:
    dashboard_path = _dashboard_path()
    if not dashboard_path.exists():
        return HTMLResponse(
            content=f"<p>dashboard not found at {dashboard_path}</p>", status_code=404
        )
    return _serve_html(dashboard_path.read_text(encoding="utf-8"))


@app.post("/api/mcp/call")
async def call_mcp_tool(call_request: McpCallRequest) -> JSONResponse:
    start_time = time.monotonic()
    base_url = _CONNECTORS.get(call_request.connector)
    if base_url is None:
        elapsed_ms = round((time.monotonic() - start_time) * 1000, 1)
        error_message = f"unknown connector '{call_request.connector}'"
        logger.info(
            "mcp_call connector=%s tool=%s args=%s ms=%s ok=False error=%s",
            call_request.connector,
            call_request.tool,
            call_request.args,
            elapsed_ms,
            error_message,
        )
        return JSONResponse(content={"error": {"message": error_message}})

    try:
        transport = StreamableHttpTransport(base_url)
        async with Client(transport, timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            result = await client.call_tool(
                call_request.tool, call_request.args, raise_on_error=False
            )
    except Exception as call_error:  # noqa: BLE001 -- spike: forward any failure as {error:...}
        elapsed_ms = round((time.monotonic() - start_time) * 1000, 1)
        logger.info(
            "mcp_call connector=%s tool=%s args=%s ms=%s ok=False error=%s",
            call_request.connector,
            call_request.tool,
            call_request.args,
            elapsed_ms,
            call_error,
        )
        return JSONResponse(content={"error": {"message": str(call_error)}})

    elapsed_ms = round((time.monotonic() - start_time) * 1000, 1)

    if result.is_error:
        error_message = (
            "\n".join(block.text for block in result.content if hasattr(block, "text"))
            or f"tool '{call_request.tool}' failed with no message"
        )
        logger.info(
            "mcp_call connector=%s tool=%s args=%s ms=%s ok=False error=%s",
            call_request.connector,
            call_request.tool,
            call_request.args,
            elapsed_ms,
            error_message,
        )
        return JSONResponse(content={"error": {"message": error_message}})

    if result.structured_content is None:
        error_message = f"tool '{call_request.tool}' response has no structuredContent"
        logger.info(
            "mcp_call connector=%s tool=%s args=%s ms=%s ok=False error=%s",
            call_request.connector,
            call_request.tool,
            call_request.args,
            elapsed_ms,
            error_message,
        )
        return JSONResponse(content={"error": {"message": error_message}})

    # NEVER unwrap FastMCP's `{"result": [...]}` envelope here -- app/agent/connectors/
    # mcp_adapter.py's `_extract_tool_payload` returns `result.structured_content` as-is, so
    # this is the exact shape the agent saw during analysis when it wrote the mcp() call.
    payload = result.structured_content
    if isinstance(payload, list):
        row_count = len(payload)
    elif isinstance(payload, dict) and isinstance(payload.get("result"), list):
        row_count = len(payload["result"])
    elif isinstance(payload, dict) and isinstance(payload.get("data"), list):
        row_count = len(payload["data"])
    else:
        row_count = "n/a"
    logger.info(
        "mcp_call connector=%s tool=%s args=%s ms=%s ok=True rows=%s",
        call_request.connector,
        call_request.tool,
        call_request.args,
        elapsed_ms,
        row_count,
    )
    return JSONResponse(content={"data": payload})


if __name__ == "__main__":
    uvicorn.run(app, host=_HOST, port=_PORT)
