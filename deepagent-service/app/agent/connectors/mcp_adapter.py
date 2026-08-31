"""MCP stateless adapter——把一個 FastMCP `stateless_http=True` server 的 tools 映射進
connector 抽象（`load_mcp_connector`）。改用官方 `mcp` SDK client
（`streamable_http_client` + `ClientSession`），取代手寫 JSON-RPC/SSE 信封解析。

**前提**：所有 MCP server 皆 FastMCP `stateless_http=True`——每次操作（`tools/list`／
`tools/call`／`resources/read`）都開一個全新 SDK session 並先 `initialize()`，對 stateless
server 這只是可容忍的重複成本，換來 NEVER 快取「連線」與身分綁定。SSO token/url 於呼叫當下
現取（`require_sso_token()`/`get_sso_url()`），token 一律附上（缺身分時 fail loud、不送出
任何未認證請求）；url header 只在有值時才附加。header 名稱皆可經
`Settings.CONNECTOR_SSO_TOKEN_HEADER`/`CONNECTOR_SSO_URL_HEADER` 配置。

**工具回應解析**：優先取 `CallToolResult.structuredContent`，缺席時退回解析 text content
當 JSON。`isError=True` 與 SDK/傳輸層例外（`McpError`、httpx 逾時/連線/狀態碼錯誤，含 anyio
TaskGroup 攤平出的 `ExceptionGroup`）一律轉成 `ConnectorToolError`，訊息含方法名與可行動
描述，NEVER 帶 header 或 token 值。
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import TypeVar

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
from mcp.shared.exceptions import McpError
from mcp.types import CallToolResult, TextContent, TextResourceContents, Tool

from app.agent.connectors.model import Connector, ConnectorTool, ConnectorToolError
from app.config import get_settings
from app.engine.request_context import get_sso_url, require_sso_token

logger = logging.getLogger(__name__)

_SKILL_RESOURCE_URI = "skill://usage"
_REQUEST_TIMEOUT_SECONDS = 30.0
_DEFAULT_INPUT_SCHEMA = {"type": "object", "properties": {}}

_ResultType = TypeVar("_ResultType")


async def load_mcp_connector(connector_id: str, display_name: str, base_url: str) -> Connector:
    """連上 `base_url` 的 stateless MCP server：打 `tools/list` 列舉 tools、打
    `resources/read`(`skill://usage`)讀劇本，組成 `Connector`。

    呼叫當下就需要有效的 request identity（`tools/list`／`resources/read` 都算「呼叫」，
    見模組 docstring）；缺身分時 `require_sso_token()` fail loud，不會發出未認證請求。
    """
    tools_result = await _call(
        base_url, "tools/list", _build_headers(), lambda session: session.list_tools()
    )
    tool_definitions: list[Tool] = tools_result.tools

    tools = tuple(
        ConnectorTool(
            name=tool_definition.name,
            description=tool_definition.description or "",
            input_schema=tool_definition.inputSchema or dict(_DEFAULT_INPUT_SCHEMA),
            call=_make_tool_call(base_url, tool_definition.name),
        )
        for tool_definition in tool_definitions
    )

    skill_markdown = await _read_skill_markdown(base_url, connector_id)

    return Connector(
        connector_id=connector_id,
        display_name=display_name,
        tools=tools,
        skill_markdown=skill_markdown,
    )


def _make_tool_call(base_url: str, tool_name: str) -> Callable[[dict], object]:
    def call(args: dict) -> object:
        # headers 先在這條 LangChain executor thread 上現取（此處從無 running loop，
        # 是安全的同步呼叫），再進 asyncio.run——確保 token 解析永遠發生在跨 loop/thread
        # 邊界之前。
        headers = _build_headers()
        result = asyncio.run(
            _call(
                base_url, "tools/call", headers, lambda session: session.call_tool(tool_name, args)
            )
        )
        return _extract_tool_payload(result, tool_name)

    return call


async def _read_skill_markdown(base_url: str, connector_id: str) -> str:
    try:
        result = await _call(
            base_url,
            "resources/read",
            _build_headers(),
            lambda session: session.read_resource(_SKILL_RESOURCE_URI),
        )
    except ConnectorToolError as resource_error:
        logger.warning(
            "connector %s 的 skill resource(%s)讀取失敗，劇本留空：%s",
            connector_id,
            _SKILL_RESOURCE_URI,
            resource_error,
        )
        return ""

    contents = result.contents
    if not contents:
        logger.warning(
            "connector %s 未提供 skill resource(%s)，劇本留空", connector_id, _SKILL_RESOURCE_URI
        )
        return ""

    first_content = contents[0]
    if isinstance(first_content, TextResourceContents):
        return first_content.text
    return ""


def _extract_tool_payload(result: CallToolResult, tool_name: str) -> object:
    if result.isError:
        message = _content_text(result) or f"tool '{tool_name}' 呼叫失敗（server 未給訊息）"
        raise ConnectorToolError(message)

    if result.structuredContent is not None:
        return result.structuredContent

    text = _content_text(result)
    if text is None:
        raise ConnectorToolError(f"tool '{tool_name}' 回應無可解析內容")
    try:
        return json.loads(text)
    except json.JSONDecodeError as decode_error:
        raise ConnectorToolError(
            f"tool '{tool_name}' 回應非合法 JSON：{decode_error}"
        ) from decode_error


def _content_text(result: CallToolResult) -> str | None:
    text_blocks = [block.text for block in result.content if isinstance(block, TextContent)]
    if not text_blocks:
        return None
    return "\n".join(text_blocks)


@asynccontextmanager
async def _open_session(base_url: str, headers: dict[str, str]) -> AsyncIterator[ClientSession]:
    """開一個全新 SDK session（stateless 前提下重複 `initialize()` 可接受）；headers/timeout
    走 `create_mcp_http_client` 預配置，client 由本 context 持有並隨之關閉。"""
    async with (
        create_mcp_http_client(
            headers=headers, timeout=httpx.Timeout(_REQUEST_TIMEOUT_SECONDS)
        ) as http_client,
        streamable_http_client(base_url, http_client=http_client) as (
            read_stream,
            write_stream,
            _get_session_id,
        ),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        yield session


async def _call(
    base_url: str,
    method_name: str,
    headers: dict[str, str],
    operation: Callable[[ClientSession], Awaitable[_ResultType]],
) -> _ResultType:
    """對 stateless server 執行單次操作。SDK/傳輸層例外一律攤平轉成帶方法名的
    `ConnectorToolError`，NEVER 帶 header 或 token 值（httpx/McpError 的例外字串本身不含
    request headers）。"""
    try:
        async with _open_session(base_url, headers) as session:
            return await operation(session)
    except Exception as raised_exception:
        raise ConnectorToolError(
            _actionable_message(method_name, raised_exception)
        ) from raised_exception


def _build_headers() -> dict[str, str]:
    settings = get_settings()
    headers = {settings.CONNECTOR_SSO_TOKEN_HEADER: require_sso_token()}
    sso_url = get_sso_url()
    if sso_url is not None:
        headers[settings.CONNECTOR_SSO_URL_HEADER] = sso_url
    return headers


def _actionable_message(method_name: str, raised_exception: BaseException) -> str:
    leaf_exceptions = _flatten_exception(raised_exception)
    detail = "；".join(_describe_exception(leaf_exception) for leaf_exception in leaf_exceptions)
    return f"MCP server 呼叫失敗（method={method_name}）：{detail}"


def _flatten_exception(raised_exception: BaseException) -> list[BaseException]:
    """anyio TaskGroup 把子任務例外包成（可能巢狀的）`ExceptionGroup`——攤平取出葉節點
    例外（`McpError`／httpx 例外）才能組出對診斷有意義的訊息。"""
    nested_exceptions = getattr(raised_exception, "exceptions", None)
    if not nested_exceptions:
        return [raised_exception]
    flattened_exceptions: list[BaseException] = []
    for nested_exception in nested_exceptions:
        flattened_exceptions.extend(_flatten_exception(nested_exception))
    return flattened_exceptions


def _describe_exception(raised_exception: BaseException) -> str:
    if isinstance(raised_exception, McpError):
        return raised_exception.error.message
    return f"{type(raised_exception).__name__}：{raised_exception}"
