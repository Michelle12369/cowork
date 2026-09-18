"""MCP 的 stateless adapter, 用 fastmcp v3. 每次操作都開一個全新的 Client 和 session.
connector tools 唯讀且重複呼叫無副作用, 所以任何呼叫失敗都可以立即重試; tool 本身回報的錯誤不重試."""

import asyncio
import logging
import tempfile
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import TypeVar

import httpx
from fastmcp import Client
from fastmcp.client.client import CallToolResult
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.utilities.skills import download_skill, list_skills
from mcp.shared.exceptions import McpError
from mcp.types import TextContent, Tool

from app.agent.connectors.model import (
    Connector,
    ConnectorTool,
    ConnectorToolError,
    ConnectorToolErrorKind,
)
from app.config import connector_bearer_token, get_settings
from app.engine.request_context import require_sso_token, require_sso_url

logger = logging.getLogger(__name__)

_SKILL_MAIN_FILE = "SKILL.md"

_SKILL_FILE_COUNT_LIMIT = 20
_SKILL_TOTAL_CHARS_LIMIT = 200_000

_DEFAULT_INPUT_SCHEMA = {"type": "object", "properties": {}}

# httpx 的傳輸層例外與 MCP 協定例外——出現在 cause chain 裡都分類成 "transport".
_TRANSPORT_CAUSE_TYPES: tuple[type[BaseException], ...] = (
    TimeoutError,
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.RemoteProtocolError,
    httpx.TransportError,
    McpError,
)

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
        bearer_token = connector_bearer_token(bearer_token_key)
        if bearer_token is None:
            raise ConnectorToolError(
                f"connector '{connector_id}' declares bearerTokenKey '{bearer_token_key}' but "
                "CONNECTOR_BEARER_TOKENS has no such key or the value is empty -- fix the configuration",
                kind="config",
                detail=bearer_token_key,
            )
    tool_definitions: list[Tool] = await _call(
        connector_id,
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
            call=_make_tool_call(connector_id, base_url, tool_definition.name, bearer_token),
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


async def call_tool(
    connector_id: str, base_url: str, tool_name: str, args: dict, bearer_token: str | None
) -> object:
    """對一個 tool 打一次 tools/call, 是 view-time 呼叫端唯一該用的公開進入點——
    chat mode 的 _make_tool_call 也只是包一層 asyncio.run 呼叫這裡."""
    headers = _build_headers(bearer_token)
    # 用公開的 Client.call_tool: 它內建 session monitoring, 且每個 session 第一次打某個
    # tool 時可能會多打一次 tools/list 去填 output-schema cache——這裡接受這個成本.
    result = await _call(
        connector_id,
        base_url,
        "tools/call",
        headers,
        lambda client: client.call_tool(tool_name, args, raise_on_error=False),
    )
    return _extract_tool_payload(result, tool_name, connector_id)


def _make_tool_call(
    connector_id: str, base_url: str, tool_name: str, bearer_token: str | None
) -> Callable[[dict], object]:
    def call(args: dict) -> object:
        return asyncio.run(call_tool(connector_id, base_url, tool_name, args, bearer_token))

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
        return await _run_with_retry(connector_id, base_url, "skills/list", attempt_read_all_skills)
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
    skipped_by_limit: list[str] = []

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
            skipped_by_limit.append(relative_path)
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
            skipped_by_limit.append(relative_path)
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

    if skipped_by_limit:
        # 讓模型知道這些檔案不存在是被上限砍掉的, 不要再去讀.
        files[_SKILL_MAIN_FILE] = skill_md_content + _skipped_files_note(skipped_by_limit)
    return files


def _skipped_files_note(skipped_paths: list[str]) -> str:
    listed = ", ".join(skipped_paths)
    return (
        f"\n\n(Note from the system: {len(skipped_paths)} support file(s) of this skill were not "
        f"loaded because the skill exceeds the limit of {_SKILL_FILE_COUNT_LIMIT} files or "
        f"{_SKILL_TOTAL_CHARS_LIMIT} characters: {listed}. Do not try to read them; work with "
        "the files that are present.)\n"
    )


def _extract_tool_payload(result: CallToolResult, tool_name: str, connector_id: str) -> object:
    if result.is_error:
        # 錯誤訊息只會出現在 text content block 裡, 沒有 structured_content.
        error_text = "\n".join(
            block.text for block in result.content if isinstance(block, TextContent)
        )
        message = error_text or f"tool '{tool_name}' call failed (server returned no message)"
        logger.warning(
            "MCP tool reported error: connector=%s tool=%s message=%s",
            connector_id,
            tool_name,
            message,
        )
        raise ConnectorToolError(
            f"Tool '{tool_name}' on connector '{connector_id}' reported an error "
            f"(raised inside the MCP server, not by this service): {message}",
            kind="tool",
            detail=error_text or None,
        )

    if result.structured_content is None:
        raise ConnectorToolError(
            f"tool '{tool_name}' on connector '{connector_id}' response has no structuredContent "
            "-- the server tool MUST return a dict/list (FastMCP generates structured output "
            "automatically)",
            kind="no_structured_content",
        )
    return result.structured_content


def _iter_cause_chain(raised: BaseException) -> Iterator[BaseException]:
    """走訪 __cause__/__context__ 鏈與 BaseExceptionGroup 的 .exceptions, 用 id() 記錄
    已訪問的例外防止循環, 讓第一個符合分類的例外(不論在鏈的哪一層)勝出.
    __suppress_context__ 為真(即 `raise X from None`)時不追 __context__, 尊重呼叫端
    刻意切斷的因果鏈."""
    seen_ids: set[int] = set()
    pending: list[BaseException] = [raised]
    while pending:
        current = pending.pop(0)
        if id(current) in seen_ids:
            continue
        seen_ids.add(id(current))
        yield current
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None and not current.__suppress_context__:
            pending.append(current.__context__)


def _classify_cause(
    raised: BaseException,
) -> tuple[ConnectorToolErrorKind, int | None, str]:
    """走訪整條 cause chain, 第一個符合的例外類型就決定分類. fastmcp 會把連線失敗包成
    RuntimeError from 原始例外, anyio 則可能包成 BaseExceptionGroup, 兩者都要穿透."""
    for cause in _iter_cause_chain(raised):
        if isinstance(cause, httpx.HTTPStatusError):
            return "http", cause.response.status_code, "HTTPStatusError"
        if isinstance(cause, _TRANSPORT_CAUSE_TYPES):
            return "transport", None, type(cause).__name__
    return "transport", None, type(raised).__name__


def _max_attempt_count() -> int:
    settings = get_settings()
    return 1 + max(0, settings.CONNECTOR_CALL_RETRIES)


async def _call(
    connector_id: str,
    base_url: str,
    method_name: str,
    headers: dict[str, str],
    operation: Callable[[Client], Awaitable[_ResultType]],
) -> _ResultType:
    """對 stateless server 執行一次操作, 每次嘗試都開全新的 Client, 失敗過的不重用.
    連線或協定層例外一律包成帶 connector id/方法名/url 的 ConnectorToolError, 不帶
    header 或 token 值."""

    async def attempt_operation() -> _ResultType:
        settings = get_settings()
        transport = StreamableHttpTransport(base_url, headers=headers)
        async with Client(transport, timeout=settings.CONNECTOR_REQUEST_TIMEOUT_SECONDS) as client:
            return await operation(client)

    try:
        return await _run_with_retry(connector_id, base_url, method_name, attempt_operation)
    except Exception as raised_exception:
        kind, status, cause_name = _classify_cause(raised_exception)
        raise ConnectorToolError(
            _actionable_message(connector_id, base_url, method_name, raised_exception),
            kind=kind,
            status=status,
            attempts=_max_attempt_count(),
            detail=cause_name if kind == "transport" else None,
        ) from raised_exception


async def _run_with_retry(
    connector_id: str,
    base_url: str,
    method_name: str,
    attempt: Callable[[], Awaitable[_ResultType]],
) -> _ResultType:
    """最多執行 1 + CONNECTOR_CALL_RETRIES 次, attempt 每次都要重新建立連線; 每次失敗一律立即
    再試(不分失敗類型), 放棄時記一則含 traceback 的 warning 再拋最後一個例外."""
    max_attempt_count = _max_attempt_count()

    for attempt_index in range(1, max_attempt_count + 1):
        try:
            return await attempt()
        except Exception as raised_exception:
            is_last_attempt = attempt_index == max_attempt_count
            if is_last_attempt:
                # exc_info 會連 cause/context 鏈一起印出完整 traceback.
                logger.warning(
                    "MCP call failed: connector=%s method=%s url=%s attempts=%d",
                    connector_id,
                    method_name,
                    base_url,
                    attempt_index,
                    exc_info=raised_exception,
                )
                raise
            logger.warning(
                "MCP call (connector=%s method=%s url=%s) failed on attempt %d/%d (%s), retrying",
                connector_id,
                method_name,
                base_url,
                attempt_index,
                max_attempt_count,
                type(raised_exception).__name__,
            )


def _build_headers(bearer_token: str | None = None) -> dict[str, str]:
    settings = get_settings()
    headers = {
        settings.SSO_TOKEN_HEADER: require_sso_token(),
        settings.SSO_URL_HEADER: require_sso_url(),
    }
    if bearer_token is not None:
        headers["Authorization"] = f"Bearer {bearer_token}"
    return headers


def _actionable_message(
    connector_id: str, base_url: str, method_name: str, raised_exception: BaseException
) -> str:
    """fastmcp 的例外訊息本身已經帶有底層原因, 例如連線失敗的訊息內嵌了 cause 內容,
    HTTP 錯誤自己帶著狀態碼."""
    return (
        f"MCP server call failed (connector={connector_id}, method={method_name}, "
        f"url={base_url}): {type(raised_exception).__name__}: {raised_exception}"
    )
