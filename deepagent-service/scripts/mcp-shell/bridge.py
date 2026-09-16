"""THROWAWAY spike -- shell host FastAPI app, 127.0.0.1:8766.

Run: ``uv run python scripts/mcp-shell/bridge.py`` (from ``deepagent-service/``).

Serves ``shell.html`` and forwards the iframe's ``mcp(connector, tool, args, handler)`` calls
(brokered by ``shell.html``'s host bridge) to deepagent's real ``POST /tool-call`` endpoint --
the actual hop (4) transport, not a local mirror of ``mcp_adapter.py``. ``mcp()`` itself is no
longer defined here: it is deepagent's injected ``erd-mcp-runtime`` prelude
(``app/engine/results.py``), already present in any dashboard generated after Task 7.

Dev-only settings (deepagent URL, placeholder SSO values, and the ``DEV_CONNECTORS`` catalog
this bridge is allowed to forward) come from ``scripts.dev_config.resolve()`` -- the same
``DEV_`` keys ``scripts/dev_chat.py`` reads, by the same rule: env > ``one-local.properties`` >
built-in default (this bridge has no CLI flags). The official ``AGENT_API_BEARER_TOKEN`` and
``SSO_*_HEADER`` keys come through the same call, with values from ``app.config.get_settings()``.
The file is meant to be authoritative during a dev run, so a key the env shadows in the file is
logged as a warning at startup. A call naming a connector id outside ``DEV_CONNECTORS`` gets back
an ``INVALID_CALL`` body, the same wording the product's Java hop would give for a connector not
in the session's catalog.
"""

import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

_SPIKE_ROOT = Path(__file__).parent
_SERVICE_ROOT = _SPIKE_ROOT.parents[1]
# 以腳本方式執行時 sys.path[0] 是 scripts/mcp-shell/ 而不是 service root, 要自己把 service root
# 加進去才 import 得到 app/scripts(同 scripts/env_to_properties.py 的招數), 這樣就不用再靠
# PYTHONPATH=. 才能跑.
sys.path.insert(0, str(_SERVICE_ROOT))

from app.config import get_settings
from scripts.dev_config import connectors_needing_real_sso, env_shadow_warning_lines, resolve

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("bridge")

_HOST = "127.0.0.1"
_PORT = 8766
_SHELL_HTML_PATH = _SPIKE_ROOT / "shell.html"
_DEFAULT_DASHBOARD_PATH = _SPIKE_ROOT / "out" / "dashboard.html"

try:
    _DEV_CONFIG = resolve()
except ValueError as config_error:
    # 轉成 RuntimeError 讓訊息是第一行(也是唯一一行)顯示出來, 而不是被裸 ValueError 的
    # traceback 蓋掉——DEV_CONNECTORS 壞掉時常見, 值本身(url/bearerTokenKey)可能藏 token,
    # 訊息只點出欄位名, 不重覆印一次帶原始值的例外鏈.
    raise RuntimeError(str(config_error)) from None
for _warning_line in env_shadow_warning_lines(_DEV_CONFIG):
    logger.warning(_warning_line)

# hop (4) stand-in: the deepagent endpoint that actually calls the MCP server.
_DEEPAGENT_URL = _DEV_CONFIG.deepagent_url
_TOOL_CALL_TIMEOUT_SECONDS = 65.0

# Which connectors this bridge is allowed to forward -- read from DEV_CONNECTORS (same
# one-local.properties the service reads); a dashboard's mcp() call names one by id and the
# bridge looks up its full spec here, instead of forwarding one fixed hard-coded connector.
_CONNECTORS_BY_ID: dict[str, dict[str, str | None]] = {
    str(connector["id"]): connector for connector in _DEV_CONFIG.connectors
}
if not _CONNECTORS_BY_ID:
    raise RuntimeError(
        "DEV_CONNECTORS is empty. Set it in one-local.properties to a JSON list of "
        "{id, url, name?, bearerTokenKey?} entries."
    )

# Fail loudly at import time, like scripts/dev_chat.py's preflight -- a wrong or missing token
# here would otherwise surface only as a mystifying AUTH card once a dashboard calls mcp().
_AGENT_API_BEARER_TOKEN = _DEV_CONFIG.bearer_token
if not _AGENT_API_BEARER_TOKEN:
    raise RuntimeError(
        "AGENT_API_BEARER_TOKEN is not set. Set it in one-local.properties (it must equal the "
        "value run-deepagent.sh started with)."
    )
# Placeholder SSO values for local mock servers, which do not check them. Only safe while every
# configured connector is on this machine: sending them to a real MCP server turns a missing
# setting into an AUTH card deep inside a dashboard's mcp() call, which is far from the cause.
_DEV_SSO_TOKEN = _DEV_CONFIG.sso_token or "spike"
_DEV_SSO_URL = _DEV_CONFIG.sso_url or "http://spike.invalid"
_REMOTE_CONNECTOR_IDS = connectors_needing_real_sso(_DEV_CONFIG.connectors)
if _REMOTE_CONNECTOR_IDS and not (_DEV_CONFIG.sso_token and _DEV_CONFIG.sso_url):
    raise RuntimeError(
        f"connectors {', '.join(_REMOTE_CONNECTOR_IDS)} are not on a loopback host, but "
        "DEV_SSO_TOKEN/DEV_SSO_URL are unset. Set both in one-local.properties rather than "
        "letting the placeholder values reach a real MCP server."
    )

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
logger.info(
    "connectors=%s deepagent_url=%s sso=%s",
    sorted(_CONNECTORS_BY_ID),
    _DEEPAGENT_URL,
    "real" if (_DEV_CONFIG.sso_token and _DEV_CONFIG.sso_url) else "placeholder(loopback)",
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
        "mcp_call connector=%s tool=%s arg_keys=%s ms=%s %s",
        call_request.connector,
        call_request.tool,
        arg_keys,
        elapsed_ms,
        outcome,
    )


@app.post("/api/mcp/call")
async def call_mcp_tool(call_request: McpCallRequest) -> JSONResponse:
    connector_spec = _CONNECTORS_BY_ID.get(call_request.connector)
    if connector_spec is None:
        # Mirrors the Java hop's INVALID_CALL wording (tests/fixtures/mcp_result_examples.json)
        # for a connector id the current session's catalog does not carry.
        allowed_ids = ", ".join(sorted(_CONNECTORS_BY_ID))
        error_message = (
            f"connector '{call_request.connector}' is not enabled for this session; "
            f"allowed: {allowed_ids}"
        )
        logger.info(
            "mcp_call connector=%s tool=%s INVALID_CALL (not in DEV_CONNECTORS)",
            call_request.connector,
            call_request.tool,
        )
        return JSONResponse(content={"error": {"code": "INVALID_CALL", "message": error_message}})

    start_time = time.monotonic()
    request_body = {
        "connector": connector_spec,
        "tool": call_request.tool,
        "args": call_request.args,
    }
    request_headers = {
        "Authorization": f"Bearer {_AGENT_API_BEARER_TOKEN}",
        _DEV_CONFIG.sso_token_header: _DEV_SSO_TOKEN,
        _DEV_CONFIG.sso_url_header: _DEV_SSO_URL,
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
