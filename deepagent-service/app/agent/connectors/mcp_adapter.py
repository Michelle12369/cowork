"""MCP 的 stateless adapter, 用 fastmcp v3. 每次操作都開一個全新的 Client 和 session.
connector tools 唯讀且冪等, 連線層的暫時性失敗可以重試; tool 本身回報的錯誤不重試."""

import asyncio
import logging
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TypeVar

import httpx
from fastmcp import Client
from fastmcp.client.client import CallToolResult
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.utilities.skills import download_skill, list_skills
from mcp.types import TextContent, Tool

from app.agent.connectors.model import Connector, ConnectorTool, ConnectorToolError
from app.config import SecretResolutionError, connector_bearer_token, get_settings
from app.engine.request_context import require_sso_token, require_sso_url

logger = logging.getLogger(__name__)

_SKILL_MAIN_FILE = "SKILL.md"

_SKILL_FILE_COUNT_LIMIT = 20
_SKILL_TOTAL_CHARS_LIMIT = 200_000

_DEFAULT_INPUT_SCHEMA = {"type": "object", "properties": {}}

# 沿 __cause__/__context__ 鏈往下找暫時性失敗根因時最多走幾層, 避免萬一遇到極長的
# 包裝鏈時卡住.
_TRANSIENT_FAILURE_CHAIN_DEPTH_LIMIT = 10

_ResultType = TypeVar("_ResultType")


async def load_mcp_connector(
    connector_id: str, display_name: str, base_url: str, bearer_token_key: str | None = None
) -> Connector:
    """連上 base_url 這個 stateless MCP server: 打 tools/list 列舉工具, 再用
    fastmcp.utilities.skills 的 list_skills 和 download_skill 列舉並下載所有目錄式的
    skill://{name}/SKILL.md skill, 最後組成一個 Connector.
    """
    bearer_token: str | None = None
    if bearer_token_key is not None:
        try:
            bearer_token = connector_bearer_token(bearer_token_key)
        except SecretResolutionError as resolution_error:
            raise ConnectorToolError(str(resolution_error)) from resolution_error
        if bearer_token is None:
            raise ConnectorToolError(
                f"connector '{connector_id}' declares bearerTokenKey '{bearer_token_key}' but "
                "CONNECTOR_BEARER_TOKENS has no such key or the value is empty -- fix the configuration"
            )
    tool_definitions: list[Tool] = await _call(
        base_url,
        "tools/list",
        _build_headers(bearer_token),
        lambda client: client.list_tools(),
    )

    tools = tuple(
        ConnectorTool(
            name=tool_definition.name,
            description=tool_definition.description or "",
            input_schema=tool_definition.inputSchema or dict(_DEFAULT_INPUT_SCHEMA),
            call=_make_tool_call(base_url, tool_definition.name, bearer_token),
        )
        for tool_definition in tool_definitions
    )

    skills = await _read_skills(base_url, connector_id, bearer_token)

    return Connector(
        connector_id=connector_id,
        display_name=display_name,
        tools=tools,
        skills=skills,
    )


def _make_tool_call(
    base_url: str, tool_name: str, bearer_token: str | None
) -> Callable[[dict], object]:
    def call(args: dict) -> object:
        headers = _build_headers(bearer_token)
        result = asyncio.run(
            _call(
                base_url,
                "tools/call",
                headers,
                lambda client: client.call_tool(tool_name, args, raise_on_error=False),
            )
        )
        return _extract_tool_payload(result, tool_name)

    return call


async def _read_skills(
    base_url: str, connector_id: str, bearer_token: str | None
) -> dict[str, dict[str, str]]:
    """列出這個 connector 的 skill, 逐一下載到暫存目錄, 只留 .md 檔案內容組成字典.
    單一 skill 下載失敗只跳過那一份, 整體列舉失敗回傳空字典, 都不會拋給呼叫端."""
    headers = _build_headers(bearer_token)

    async def attempt_read_all_skills() -> dict[str, dict[str, str]]:
        settings = get_settings()
        transport = StreamableHttpTransport(base_url, headers=headers)
        async with Client(transport, timeout=settings.CONNECTOR_REQUEST_TIMEOUT_SECONDS) as client:
            skill_summaries = await list_skills(client)

            if not skill_summaries:
                logger.warning(
                    "connector %s did not provide any skill://{name}/%s resource, skill left empty",
                    connector_id,
                    _SKILL_MAIN_FILE,
                )
                return {}

            skills: dict[str, dict[str, str]] = {}
            with tempfile.TemporaryDirectory(prefix=f"mcp-skills-{connector_id}-") as temp_root:
                temp_root_path = Path(temp_root)
                for skill_summary in skill_summaries:
                    skill_name = skill_summary.name
                    try:
                        skill_dir = await download_skill(client, skill_name, temp_root_path)
                    except Exception as download_error:  # noqa: BLE001 -- 單一 skill 下載失敗不應影響其他 skill
                        logger.warning(
                            "connector %s skill (%s) download failed, skipping: %s",
                            connector_id,
                            skill_name,
                            download_error,
                        )
                        continue

                    skill_files = _collect_skill_files(connector_id, skill_name, skill_dir)
                    if skill_files is None:
                        continue
                    skills[skill_name] = skill_files

            if not skills:
                logger.warning(
                    "connector %s candidate skills are all missing %s main file or failed to "
                    "download, skill left empty",
                    connector_id,
                    _SKILL_MAIN_FILE,
                )
            return skills

    try:
        return await _run_with_retry("skills/list", attempt_read_all_skills)
    except Exception as list_error:  # noqa: BLE001 -- 列舉失敗不是致命錯誤, 處理方式跟沒有 skill 一樣
        logger.warning(
            "connector %s skill resources listing failed, skill left empty: %s",
            connector_id,
            list_error,
        )
        return {}


def _collect_skill_files(
    connector_id: str, skill_name: str, skill_dir: Path
) -> dict[str, str] | None:
    """download_skill 已經把單一 skill 的整包內容(含非 .md 檔)下載到本地的 skill_dir,
    這裡只做純本地的檔案操作, 只挑 .md 檔來讀.
    """
    resolved_skill_dir = skill_dir.resolve()
    skill_md_path = skill_dir / _SKILL_MAIN_FILE

    if not skill_md_path.is_file():
        logger.warning(
            "connector %s skill (%s) downloaded without %s main file, skipping the whole skill",
            connector_id,
            skill_name,
            _SKILL_MAIN_FILE,
        )
        return None

    try:
        skill_md_content = skill_md_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as read_error:
        logger.warning(
            "connector %s skill (%s) failed to read downloaded %s, skipping the whole skill: %s",
            connector_id,
            skill_name,
            _SKILL_MAIN_FILE,
            read_error,
        )
        return None

    files: dict[str, str] = {_SKILL_MAIN_FILE: skill_md_content}
    total_chars = len(skill_md_content)
    limit_reached = False

    for file_path in sorted(skill_dir.rglob("*")):
        if not file_path.is_file():
            continue

        relative_path = file_path.relative_to(skill_dir).as_posix()
        if relative_path == _SKILL_MAIN_FILE:
            continue
        if not relative_path.endswith(".md"):
            logger.debug(
                "connector %s skill (%s) downloaded file (%s) is not .md, skipping",
                connector_id,
                skill_name,
                relative_path,
            )
            continue

        resolved_file_path = file_path.resolve()
        if not resolved_file_path.is_relative_to(resolved_skill_dir):
            logger.warning(
                "connector %s skill (%s) downloaded file (%s) escapes the skill directory,"
                " skipping",
                connector_id,
                skill_name,
                relative_path,
            )
            continue

        if limit_reached:
            continue
        if len(files) >= _SKILL_FILE_COUNT_LIMIT or total_chars >= _SKILL_TOTAL_CHARS_LIMIT:
            logger.warning(
                "connector %s skill (%s) support files exceeded the limit (%d files or %d "
                "chars), skipping the rest (starting from %s)",
                connector_id,
                skill_name,
                _SKILL_FILE_COUNT_LIMIT,
                _SKILL_TOTAL_CHARS_LIMIT,
                relative_path,
            )
            limit_reached = True
            continue

        try:
            file_content = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as read_error:
            logger.warning(
                "connector %s skill (%s) file (%s) failed to read, skipping: %s",
                connector_id,
                skill_name,
                relative_path,
                read_error,
            )
            continue

        files[relative_path] = file_content
        total_chars += len(file_content)

    return files


def _extract_tool_payload(result: CallToolResult, tool_name: str) -> object:
    if result.is_error:
        # 錯誤訊息只會出現在 text content block 裡, 沒有 structuredContent.
        error_text = "\n".join(
            block.text for block in result.content if isinstance(block, TextContent)
        )
        raise ConnectorToolError(
            error_text or f"tool '{tool_name}' call failed (server returned no message)"
        )

    if result.structured_content is None:
        raise ConnectorToolError(
            f"tool '{tool_name}' response has no structuredContent -- the server tool MUST "
            "return a dict/list (FastMCP generates structured output automatically)"
        )
    return result.structured_content


async def _call(
    base_url: str,
    method_name: str,
    headers: dict[str, str],
    operation: Callable[[Client], Awaitable[_ResultType]],
) -> _ResultType:
    """對 stateless server 執行一次操作, 每次嘗試都開全新的 Client, 失敗過的不重用.
    連線或協定層例外一律包成帶方法名的 ConnectorToolError, 不帶 header 或 token 值."""

    async def attempt_operation() -> _ResultType:
        settings = get_settings()
        transport = StreamableHttpTransport(base_url, headers=headers)
        async with Client(transport, timeout=settings.CONNECTOR_REQUEST_TIMEOUT_SECONDS) as client:
            return await operation(client)

    try:
        return await _run_with_retry(method_name, attempt_operation)
    except Exception as raised_exception:
        raise ConnectorToolError(
            _actionable_message(method_name, raised_exception)
        ) from raised_exception


async def _run_with_retry(
    method_name: str, attempt: Callable[[], Awaitable[_ResultType]]
) -> _ResultType:
    """最多執行 1 + CONNECTOR_CALL_RETRIES 次, attempt 每次都要重新建立連線.
    只有暫時性失敗才重試, 其他例外一律在第一次就往外拋, 重試之間不等待.
    重試耗盡後把最後一個例外往外拋, 交給呼叫端處理."""
    settings = get_settings()
    max_attempt_count = 1 + max(0, settings.CONNECTOR_CALL_RETRIES)

    for attempt_index in range(1, max_attempt_count + 1):
        try:
            return await attempt()
        except Exception as raised_exception:
            is_last_attempt = attempt_index == max_attempt_count
            if is_last_attempt or not _is_transient_failure(raised_exception):
                raise
            logger.warning(
                "MCP call (method=%s) transient failure on attempt %d/%d (%s), retrying",
                method_name,
                attempt_index,
                max_attempt_count,
                type(raised_exception).__name__,
            )


def _is_transient_failure(raised_exception: BaseException) -> bool:
    """判斷連線層失敗是不是暫時性: 逾時, 連線錯誤, 或狀態碼 >= 500.
    會沿 __cause__/__context__ 鏈往下找根因, 深度有上限."""
    current_exception: BaseException | None = raised_exception
    for _ in range(_TRANSIENT_FAILURE_CHAIN_DEPTH_LIMIT):
        if current_exception is None:
            return False
        if isinstance(current_exception, httpx.HTTPStatusError):
            if current_exception.response.status_code >= 500:
                return True
        elif isinstance(current_exception, httpx.TransportError | TimeoutError | ConnectionError):
            return True
        current_exception = current_exception.__cause__ or current_exception.__context__
    return False


def _build_headers(bearer_token: str | None = None) -> dict[str, str]:
    settings = get_settings()
    headers = {
        settings.SSO_TOKEN_HEADER: require_sso_token(),
        settings.SSO_URL_HEADER: require_sso_url(),
    }
    if bearer_token is not None:
        headers["Authorization"] = f"Bearer {bearer_token}"
    return headers


def _actionable_message(method_name: str, raised_exception: BaseException) -> str:
    """fastmcp 的例外訊息本身已經帶有底層原因, 例如連線失敗的訊息內嵌了 cause 內容,
    HTTP 錯誤自己帶著狀態碼."""
    return (
        f"MCP server call failed (method={method_name}): "
        f"{type(raised_exception).__name__}: {raised_exception}"
    )
