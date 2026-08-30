"""MCP stateless adapter——把一個 stateless streamable HTTP MCP server 的 tools 映射進
connector 抽象（`load_mcp_connector`）。internal 環境用這個實作接上自家 MCP server；
dev/CI 走 `registry.demo_connector` 的 in-code 模擬版（見 `catalog.py`）。

**每個 JSON-RPC POST 自包含（stateless）**：`tools/list`、`resources/read`
（`skill://usage`）、每個 `tools/call` 都在呼叫當下用 `require_sso_token()` 現取 token
塞進 `Authorization` header，NEVER 快取 token（stateless 下沒有「連線」可綁定身分，
token 也可能在呼叫前過期）。缺身分時 `require_sso_token()` fail loud（`LookupError`）、
不送出任何未認證請求——`load_mcp_connector` 本身就是一次「呼叫」，一樣受這條規則約束。

**`load_mcp_connector` MUST 在已 `set_request_identity` 的請求脈絡內呼叫**——即
`resolve_connectors`（發生在 `/chat` turn 內）那條路徑。NEVER 從目錄列舉路徑
（`catalog.load_connectors`／`GET /connectors`）呼叫本函式——那條路徑在請求脈絡外執行，
會讓 `require_sso_token()` 把目錄列舉端點炸成未接住的例外。

回應可能以 `text/event-stream`（單一 `data:` 事件）或 `application/json` 送達，兩種
`Content-Type` 都解析（`_parse_jsonrpc_envelope`）。

**工具回應解析**：若 tool 回傳值可轉成 JSON Schema，伺服端會同時給 `structuredContent`
（已解析 dict）與 `content`（同一份資料序列化成的 text block）；若不行，只會有
`content`。本 adapter 優先取 `structuredContent`，缺席時退回解析 `content` 裡的 text
block 當 JSON。MCP tool 錯誤（`CallToolResult.isError=True`）與 JSON-RPC 協定層
`error` 兩種管道都轉成 `ConnectorToolError`，訊息原樣透傳。HTTP/連線層失敗（伺服端不可
達、逾時、非 2xx）另外包成 `ConnectorToolError`，訊息只含方法名與例外類型，NEVER 帶
header 或 token 值。
"""

import json
import logging
from collections.abc import Callable

import httpx

from app.agent.connectors.model import Connector, ConnectorTool, ConnectorToolError
from app.engine.request_context import require_sso_token

logger = logging.getLogger(__name__)

_SKILL_RESOURCE_URI = "skill://usage"
_REQUEST_TIMEOUT_SECONDS = 30.0
_DEFAULT_INPUT_SCHEMA = {"type": "object", "properties": {}}


def load_mcp_connector(connector_id: str, display_name: str, base_url: str) -> Connector:
    """連上 `base_url` 的 stateless MCP server：打 `tools/list` 列舉 tools、打
    `resources/read`(`skill://usage`)讀劇本，組成 `Connector`。

    呼叫當下就需要有效的 request identity（`tools/list`／`resources/read` 都算「呼叫」，
    見模組 docstring）；缺身分時 `require_sso_token()` fail loud，不會發出未認證請求。
    """
    tools_result = _post_jsonrpc(base_url, "tools/list", {})
    tool_definitions = tools_result.get("tools") or []

    tools = tuple(
        ConnectorTool(
            name=tool_definition["name"],
            description=tool_definition.get("description") or "",
            input_schema=tool_definition.get("inputSchema") or dict(_DEFAULT_INPUT_SCHEMA),
            call=_make_tool_call(base_url, tool_definition["name"]),
        )
        for tool_definition in tool_definitions
    )

    skill_markdown = _read_skill_markdown(base_url, connector_id)

    return Connector(
        connector_id=connector_id,
        display_name=display_name,
        tools=tools,
        skill_markdown=skill_markdown,
    )


def _make_tool_call(base_url: str, tool_name: str) -> Callable[[dict], object]:
    def call(args: dict) -> object:
        result = _post_jsonrpc(base_url, "tools/call", {"name": tool_name, "arguments": args})
        return _extract_tool_payload(result, tool_name)

    return call


def _read_skill_markdown(base_url: str, connector_id: str) -> str:
    try:
        result = _post_jsonrpc(base_url, "resources/read", {"uri": _SKILL_RESOURCE_URI})
    except ConnectorToolError as resource_error:
        logger.warning(
            "connector %s 的 skill resource(%s)讀取失敗，劇本留空：%s",
            connector_id,
            _SKILL_RESOURCE_URI,
            resource_error,
        )
        return ""

    contents = result.get("contents") or []
    if not contents:
        logger.warning(
            "connector %s 未提供 skill resource(%s)，劇本留空", connector_id, _SKILL_RESOURCE_URI
        )
        return ""

    return contents[0].get("text") or ""


def _extract_tool_payload(result: dict, tool_name: str) -> object:
    if result.get("isError"):
        message = _content_text(result) or f"tool '{tool_name}' 呼叫失敗（server 未給訊息）"
        raise ConnectorToolError(message)

    structured_content = result.get("structuredContent")
    if structured_content is not None:
        return structured_content

    text = _content_text(result)
    if text is None:
        raise ConnectorToolError(f"tool '{tool_name}' 回應無可解析內容")
    try:
        return json.loads(text)
    except json.JSONDecodeError as decode_error:
        raise ConnectorToolError(
            f"tool '{tool_name}' 回應非合法 JSON：{decode_error}"
        ) from decode_error


def _content_text(result: dict) -> str | None:
    content_blocks = result.get("content") or []
    text_blocks = [block.get("text", "") for block in content_blocks if block.get("type") == "text"]
    if not text_blocks:
        return None
    return "\n".join(text_blocks)


def _post_jsonrpc(base_url: str, method: str, params: dict) -> dict:
    """發一個自包含的 JSON-RPC POST——`Authorization` header 在這裡現取 token，是全模組
    唯一呼叫 `require_sso_token()` 的地方（每個 JSON-RPC method 都經這裡送出）。"""
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {require_sso_token()}",
    }
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}

    try:
        response = httpx.post(
            base_url, json=body, headers=headers, timeout=_REQUEST_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        envelope = _parse_jsonrpc_envelope(response)
    except httpx.InvalidURL as url_error:
        # httpx.InvalidURL 不是 httpx.HTTPError 或 ValueError 子類別，需獨立一條 except
        # ——base_url 設定寫錯時要給可行動訊息，而不是讓例外原樣炸穿。
        raise ConnectorToolError(
            f"MCP server base_url 格式不合法（method={method}）：{url_error}"
        ) from url_error
    except httpx.HTTPError as request_error:
        # httpx 例外訊息含狀態碼/連線層原因(可分辨 401 與 500/timeout)但不含 request
        # headers，不會連帶洩漏 Authorization token。
        raise ConnectorToolError(
            f"MCP server 不可達或回應異常（method={method}）："
            f"{type(request_error).__name__}：{request_error}"
        ) from request_error
    except (json.JSONDecodeError, ValueError) as parse_error:
        raise ConnectorToolError(
            f"MCP server 回應格式無法解析（method={method}）：{parse_error}"
        ) from parse_error

    protocol_error = envelope.get("error")
    if protocol_error is not None:
        raise ConnectorToolError(
            protocol_error.get("message") or f"MCP server 回傳協定層錯誤（method={method}）"
        )
    return envelope.get("result") or {}


def _parse_jsonrpc_envelope(response: httpx.Response) -> dict:
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        return _parse_sse_envelope(response.text)
    return response.json()


def _parse_sse_envelope(body: str) -> dict:
    for line in body.splitlines():
        if line.startswith("data:"):
            return json.loads(line[len("data:") :].strip())
    raise ValueError("text/event-stream 回應找不到 data 事件")
