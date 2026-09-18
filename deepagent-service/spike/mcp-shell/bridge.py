"""THROWAWAY spike -- shell host FastAPI app, 127.0.0.1:8766.

Run: ``uv run python spike/mcp-shell/bridge.py`` (from ``deepagent-service/``).

Serves ``shell.html`` and forwards the iframe's ``mcp(connector, tool, args, handler)`` calls
(brokered by ``shell.html``'s host bridge) to deepagent's real ``POST /tool-call`` endpoint --
the actual hop (4) transport, not a local mirror of ``mcp_adapter.py``. ``mcp()`` itself is no
longer defined here: it is deepagent's injected ``erd-mcp-runtime`` prelude
(``app/engine/results.py``), already present in any dashboard generated after Task 7.
"""

import logging
import os
import re
import time
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.config import get_settings

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("bridge")

_HOST = "127.0.0.1"
_PORT = 8766
_SPIKE_ROOT = Path(__file__).parent
_SHELL_HTML_PATH = _SPIKE_ROOT / "shell.html"
_DEFAULT_DASHBOARD_PATH = _SPIKE_ROOT / "out" / "dashboard.html"

# hop (4) stand-in: the deepagent endpoint that actually calls the MCP server.
_DEEPAGENT_URL = os.environ.get("DEEPAGENT_URL", "http://127.0.0.1:8000")
_TOOL_CALL_TIMEOUT_SECONDS = 65.0
_MOCK_MCP_URL = os.environ.get("MOCK_MCP_URL", "http://127.0.0.1:8765/mcp")
# The spike serves exactly one connector; the dashboard's mcp() call names it but the bridge
# always forwards this fixed spec, same as run-deepagent.sh/generate.sh's "sales" connector.
_CONNECTOR_SPEC: dict[str, str] = {"id": "sales", "name": "sales-mock", "url": _MOCK_MCP_URL}

# Fail loudly at import time, like generate.sh's preflight -- a wrong or missing token here
# would otherwise surface only as a mystifying AUTH card once a dashboard calls mcp().
if not os.environ.get("AGENT_API_BEARER_TOKEN"):
    raise RuntimeError(
        "AGENT_API_BEARER_TOKEN is not set. It must equal the value run-deepagent.sh started "
        "with (default there: spike-token)."
    )
_AGENT_API_BEARER_TOKEN = os.environ["AGENT_API_BEARER_TOKEN"]
_DEV_SSO_TOKEN = os.environ.get("DEV_SSO_TOKEN", "spike")
_DEV_SSO_URL = os.environ.get("DEV_SSO_URL", "http://spike.invalid")

# Non-200 folding the product bridge will also do: /tool-call itself always answers 200 once
# past bearer auth, so these only fire for the bridge's own auth mistakes or deepagent being down.
_AUTH_STATUSES = frozenset({401, 403, 404})
_INVALID_CALL_STATUSES = frozenset({400, 422})


def _fold_status_code(status_code: int) -> str:
    if status_code in _AUTH_STATUSES:
        return "AUTH"
    if status_code in _INVALID_CALL_STATUSES:
        return "INVALID_CALL"
    return "RETRYABLE"


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


# No Java backend in the spike, so the bridge also plays ArtifactAssembler: render the repo's
# head-inject.vm (error relay, Inter @font-face, 'erd' ECharts theme) and insert it after <head>.
# Velocity is not available here; _render_head_inject understands only the two constructs that
# template uses (`#if($flag)`/`#end` and `#[[ ... ]]#` unparsed blocks) and fails loudly on
# anything else, so a template change that needs more shows up as a crash, not a silent skip.
_REPO_ROOT = _SPIKE_ROOT.parents[2]
_HEAD_INJECT_TEMPLATE = _REPO_ROOT / "backend/src/main/resources/templates/artifact/head-inject.vm"
_FONTS_DIR = _REPO_ROOT / "frontend" / "public" / "fonts"
_ECHARTS_MARKER = "echarts"
_DATA_MARKER = "__ERD_DATA__"
_VELOCITY_IF = re.compile(r"#if\(\$(\w+)\)")
_VELOCITY_UNPARSED = re.compile(r"#\[\[(.*)\]\]#", re.DOTALL)
_HEAD_OPEN_TAG = re.compile(r"<head(?=[\s>/])[^>]*>", re.IGNORECASE)


def _render_head_inject(flags: dict[str, bool]) -> str:
    active: list[bool] = [True]
    rendered: list[str] = []
    for line_number, raw_line in enumerate(
        _HEAD_INJECT_TEMPLATE.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line:
            continue
        if_match = _VELOCITY_IF.fullmatch(line)
        if if_match:
            active.append(active[-1] and flags[if_match.group(1)])
            continue
        if line.endswith("#end"):
            if active[-1] and line != "#end":
                raise RuntimeError(f"head-inject.vm line {line_number}: unsupported inline #end")
            active.pop()
            continue
        if not active[-1]:
            continue
        unparsed_match = _VELOCITY_UNPARSED.fullmatch(line)
        if unparsed_match is None:
            raise RuntimeError(
                f"head-inject.vm line {line_number}: unsupported directive {line[:40]!r}"
            )
        rendered.append(unparsed_match.group(1))
    if len(active) != 1:
        raise RuntimeError("head-inject.vm: unbalanced #if/#end")
    return "\n".join(rendered)


def _inject_head(html: str) -> str:
    if _DATA_MARKER in html:
        logger.warning("dashboard references %s; connector mode never embeds data", _DATA_MARKER)
    inject_block = _render_head_inject(
        {"hasEcharts": _ECHARTS_MARKER in html.lower(), "includeData": False}
    )
    head_match = _HEAD_OPEN_TAG.search(html)
    if head_match is None:
        return inject_block + html
    insert_at = head_match.end()
    return html[:insert_at] + "\n" + inject_block + html[insert_at:]


def _serve_html(html: str, *, as_artifact: bool) -> HTMLResponse:
    if _VENDOR_ASSETS:
        html = _rewrite_cdn_urls(html)
    if as_artifact:
        html = _inject_head(html)
    return HTMLResponse(content=html)


_VENDOR_ASSETS = _vendor_assets_enabled()

app = FastAPI(title="mcp-shell bridge (spike, throwaway)")
if _VENDOR_ASSETS:
    if not _VENDOR_DIR.is_dir():
        raise RuntimeError(
            f"AGENT_RUNTIME={_INTERNAL_RUNTIME_NAME} but vendored assets not found at {_VENDOR_DIR}"
        )
    app.mount("/vendor", StaticFiles(directory=_VENDOR_DIR), name="vendor")
if _FONTS_DIR.is_dir():
    app.mount("/fonts", StaticFiles(directory=_FONTS_DIR), name="fonts")
if not _HEAD_INJECT_TEMPLATE.is_file():
    raise RuntimeError(f"head-inject.vm not found at {_HEAD_INJECT_TEMPLATE}")
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
    return _serve_html(_SHELL_HTML_PATH.read_text(encoding="utf-8"), as_artifact=False)


@app.get("/api/dashboard")
def serve_dashboard() -> HTMLResponse:
    dashboard_path = _dashboard_path()
    if not dashboard_path.exists():
        return HTMLResponse(
            content=f"<p>dashboard not found at {dashboard_path}</p>", status_code=404
        )
    return _serve_html(dashboard_path.read_text(encoding="utf-8"), as_artifact=True)


def _log_mcp_call(call_request: McpCallRequest, elapsed_ms: float, outcome: str) -> None:
    # Argument keys only, never values -- same rule as the product bridge and the prelude's log.
    arg_keys = sorted(call_request.args)
    logger.info(
        "mcp_call tool=%s arg_keys=%s ms=%s %s",
        call_request.tool,
        arg_keys,
        elapsed_ms,
        outcome,
    )


@app.post("/api/mcp/call")
async def call_mcp_tool(call_request: McpCallRequest) -> JSONResponse:
    start_time = time.monotonic()
    request_body = {
        "connector": _CONNECTOR_SPEC,
        "tool": call_request.tool,
        "args": call_request.args,
    }
    request_headers = {
        "Authorization": f"Bearer {_AGENT_API_BEARER_TOKEN}",
        get_settings().SSO_TOKEN_HEADER: _DEV_SSO_TOKEN,
        get_settings().SSO_URL_HEADER: _DEV_SSO_URL,
    }

    try:
        async with httpx.AsyncClient(timeout=_TOOL_CALL_TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{_DEEPAGENT_URL}/tool-call", json=request_body, headers=request_headers
            )
    except httpx.HTTPError as request_error:
        elapsed_ms = round((time.monotonic() - start_time) * 1000, 1)
        _log_mcp_call(call_request, elapsed_ms, "ok=False code=RETRYABLE")
        error_message = (
            f"could not reach deepagent at {_DEEPAGENT_URL} ({type(request_error).__name__})"
        )
        return JSONResponse(content={"error": {"code": "RETRYABLE", "message": error_message}})

    elapsed_ms = round((time.monotonic() - start_time) * 1000, 1)

    if response.status_code == 200:
        body = response.json()
        _log_mcp_call(call_request, elapsed_ms, "ok=" + str("error" not in body))
        return JSONResponse(content=body)

    folded_code = _fold_status_code(response.status_code)
    _log_mcp_call(call_request, elapsed_ms, f"ok=False code={folded_code}")
    error_message = f"deepagent /tool-call returned HTTP {response.status_code}"
    return JSONResponse(content={"error": {"code": folded_code, "message": error_message}})


if __name__ == "__main__":
    uvicorn.run(app, host=_HOST, port=_PORT)
