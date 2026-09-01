"""MCP stateless adapter——官方 `mcp` SDK client，把 FastMCP server 映射進 connector 抽象。

每次操作（`tools/list`／`tools/call`／`resources/*`）都開一個全新 session；headers（SSO
token/url）於呼叫當下現取，NEVER 落 log 或快取。
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import TypeVar

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
from mcp.shared.exceptions import McpError
from mcp.types import CallToolResult, ReadResourceResult, TextContent, TextResourceContents, Tool
from pydantic import AnyUrl

from app.agent.connectors.model import Connector, ConnectorTool, ConnectorToolError
from app.config import get_settings
from app.engine.request_context import get_sso_url, require_sso_token

logger = logging.getLogger(__name__)

# skill resource 的 URI scheme 慣例——`skill://usage` 沿用為主 skill 命名，但不再特殊處理，
# 每個 `skill://` resource 都是獨立一份 skill（見 model.Connector.skills）。
_SKILL_URI_SCHEME = "skill"
_REQUEST_TIMEOUT_SECONDS = 30.0
_DEFAULT_INPUT_SCHEMA = {"type": "object", "properties": {}}

_ResultType = TypeVar("_ResultType")


async def load_mcp_connector(connector_id: str, display_name: str, base_url: str) -> Connector:
    """連上 `base_url` 的 stateless MCP server：打 `tools/list` 列舉 tools、打
    `resources/list` 找出所有 `skill://` resource 並逐一讀取 skill，組成 `Connector`。

    呼叫當下就需要有效的 request identity（`tools/list`／`resources/*` 都算「呼叫」，
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

    skills = await _read_skills(base_url, connector_id)

    return Connector(
        connector_id=connector_id,
        display_name=display_name,
        tools=tools,
        skills=skills,
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


def _normalize_skill_uri(uri: AnyUrl) -> str:
    """`skill://usage` → `usage`；`skill://spc-analysis` → `spc-analysis`；netloc+path
    去頭尾斜線、內部 `/` 轉 `-`，供 staging 當檔案系統 segment（見 workspace.py）。"""
    joined = f"{uri.host or ''}{uri.path or ''}"
    return joined.strip("/").replace("/", "-")


def _first_text_content(read_result: ReadResourceResult) -> str | None:
    contents = read_result.contents
    if not contents:
        return None
    first_content = contents[0]
    if isinstance(first_content, TextResourceContents):
        return first_content.text
    return None


async def _read_skills(base_url: str, connector_id: str) -> dict[str, str]:
    """單一 session 內先 `resources/list`、篩出 `skill://` scheme 者，逐一 `read_resource`
    組成 `{skill_name: markdown}`。單一 resource 讀取失敗只跳過（warning，partial success），
    整體列舉失敗或零 `skill://` resource 皆回空字典＋一則 warning（與舊版缺 skill 語意一致）。
    正規化後名稱撞名則後者覆蓋前者（warning）。
    """
    headers = _build_headers()
    try:
        async with _open_session(base_url, headers) as session:
            resources_result = await session.list_resources()
            skill_resources = [
                resource
                for resource in resources_result.resources
                if resource.uri.scheme == _SKILL_URI_SCHEME
            ]
            if not skill_resources:
                logger.warning(
                    "connector %s 未提供任何 %s:// resource，skill 留空",
                    connector_id,
                    _SKILL_URI_SCHEME,
                )
                return {}

            skills: dict[str, str] = {}
            for resource in skill_resources:
                skill_name = _normalize_skill_uri(resource.uri)
                try:
                    read_result = await session.read_resource(resource.uri)
                except Exception as read_error:  # noqa: BLE001 -- 單一 resource 失敗不中止其他
                    logger.warning(
                        "connector %s 的 skill resource(%s)讀取失敗，略過：%s",
                        connector_id,
                        resource.uri,
                        read_error,
                    )
                    continue

                skill_markdown = _first_text_content(read_result)
                if skill_markdown is None:
                    logger.warning(
                        "connector %s 的 skill resource(%s)無文字內容，略過",
                        connector_id,
                        resource.uri,
                    )
                    continue

                if skill_name in skills:
                    logger.warning(
                        "connector %s 的 skill 名稱正規化後撞名：%s（後者覆蓋前者）",
                        connector_id,
                        skill_name,
                    )
                skills[skill_name] = skill_markdown
            return skills
    except Exception as list_error:  # noqa: BLE001 -- 列舉失敗非致命，比照舊版缺 skill 語意
        logger.warning(
            "connector %s 的 skill resources 列舉失敗，skill 留空：%s", connector_id, list_error
        )
        return {}


def _extract_tool_payload(result: CallToolResult, tool_name: str) -> object:
    if result.isError:
        # 錯誤訊息只存在於 text content block（無 structuredContent）——原文透傳給 agent。
        error_text = "\n".join(
            block.text for block in result.content if isinstance(block, TextContent)
        )
        raise ConnectorToolError(error_text or f"tool '{tool_name}' 呼叫失敗（server 未給訊息）")

    if result.structuredContent is None:
        raise ConnectorToolError(
            f"tool '{tool_name}' 回應缺 structuredContent——server 的 tool MUST 回傳"
            " dict/list（FastMCP 會自動生成 structured output）"
        )
    return result.structuredContent


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
