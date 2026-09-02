"""MCP stateless adapter——`fastmcp` v3 package

每次操作(`tools/list`,`tools/call`,skill 讀取整體)都開一個全新 `Client`(對應全新
session);headers(SSO token/url)於呼叫當下現取。skill 交付通道採
FastMCP v3「目錄式」慣例(`skill://{name}/SKILL.md` 為主文件,`skill://{name}/_manifest`
為合成的檔案清單)——對每個 skill 下載到 temp 目錄後,本地端只收**所有 `.md` 檔**
"""

import asyncio
import logging
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TypeVar

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

_REQUEST_TIMEOUT_SECONDS = 30.0
_DEFAULT_INPUT_SCHEMA = {"type": "object", "properties": {}}

_ResultType = TypeVar("_ResultType")


async def load_mcp_connector(
    connector_id: str, display_name: str, base_url: str, bearer_token_key: str | None = None
) -> Connector:
    """連上 `base_url` 的 stateless MCP server:打 `tools/list` 列舉 tools、用
    `fastmcp.utilities.skills` 的 `list_skills`/`download_skill` 列舉並下載所有目錄式
    `skill://{name}/SKILL.md` skill,組成 `Connector`。
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
    """單一 session 內先 `list_skills` 列舉可用 skill,再逐 skill 呼叫 `download_skill`
    下載到共用 temp 目錄(整批用畢自動清除),下載結果交 `_collect_skill_files` 本地端
    篩選出 `.md` 檔組成該 skill 的字典。整體列舉失敗或零 skill 皆回空字典＋一則
    warning;單一 skill 下載失敗只跳過該份＋warning,不拖累其他skill
    """
    headers = _build_headers(bearer_token)
    try:
        transport = StreamableHttpTransport(base_url, headers=headers)
        async with Client(transport, timeout=_REQUEST_TIMEOUT_SECONDS) as client:
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
                    except Exception as download_error:  # noqa: BLE001 -- 單一 skill 下載失敗不拖累其他 skill
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
    except Exception as list_error:  # noqa: BLE001 -- 列舉失敗非致命,比照舊版缺 skill 語意
        logger.warning(
            "connector %s skill resources listing failed, skill left empty: %s",
            connector_id,
            list_error,
        )
        return {}


def _collect_skill_files(
    connector_id: str, skill_name: str, skill_dir: Path
) -> dict[str, str] | None:
    """`download_skill` 已把單一 skill 的整包內容(含非 `.md` 檔)下載到本地
    `skill_dir`——這裡純本地檔案操作,只揀選 `.md` 檔讀
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
        # 錯誤訊息只存在於 text content block(無 structuredContent)
        error_text = "\n".join(
            block.text for block in result.content if isinstance(block, TextContent)
        )
        raise ConnectorToolError(error_text or f"tool '{tool_name}' 呼叫失敗（server 未給訊息）")

    if result.structured_content is None:
        raise ConnectorToolError(
            f"tool '{tool_name}' 回應缺 structuredContent——server 的 tool MUST 回傳"
            " dict/list（FastMCP 會自動生成 structured output）"
        )
    return result.structured_content


async def _call(
    base_url: str,
    method_name: str,
    headers: dict[str, str],
    operation: Callable[[Client], Awaitable[_ResultType]],
) -> _ResultType:
    """對 stateless server 執行單次操作:每次呼叫開全新 `Client`(對應全新 session)。
    連線/協定層例外一律包成帶方法名的 `ConnectorToolError`,NEVER 帶 header 或 token 值
    (httpx/`fastmcp`/`mcp` 的例外字串本身不含 request headers)。"""
    try:
        transport = StreamableHttpTransport(base_url, headers=headers)
        async with Client(transport, timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            return await operation(client)
    except Exception as raised_exception:
        raise ConnectorToolError(
            _actionable_message(method_name, raised_exception)
        ) from raised_exception


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
    """fastmcp 的例外訊息已含底層原因(連線失敗訊息內嵌 cause 內容、HTTP 錯誤自帶狀態碼)"""
    return (
        f"MCP server 呼叫失敗（method={method_name}）："
        f"{type(raised_exception).__name__}：{raised_exception}"
    )
