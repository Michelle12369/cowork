"""view-time /tool-call 的核心流程: 先做不碰網路的前置檢查, 打恰好一次 tools/call, 再把任何
失敗分類成五個 code 之一. 永遠回傳, 從不把例外拋給呼叫端."""

import logging
import time

from app.agent.connectors.error_codes import (
    ToolCallError,
    bearer_key_unconfigured,
    classify_connector_error,
    missing_sso_header,
    unexpected_failure,
)
from app.agent.connectors.mcp_adapter import call_tool
from app.agent.connectors.model import ConnectorToolError
from app.api.schemas import ToolCallErrorBody, ToolCallFailure, ToolCallRequest, ToolCallSuccess
from app.config import connector_bearer_token, get_settings
from app.engine.request_context import sso_identity

logger = logging.getLogger(__name__)


async def execute_tool_call(
    request: ToolCallRequest, *, sso_token: str | None, sso_url: str | None
) -> ToolCallSuccess | ToolCallFailure:
    """前置檢查(SSO header, bearer key)先於網路; 之後恰好一次 tools/call. tool 名與 args 形狀
    已由 ToolCallRequest schema 擋在 422——這裡不再重複檢查. 任何例外都收成 RETRYABLE 的最後
    防線, 每次呼叫記一行 `tool_call ...` log."""
    started_at = time.monotonic()
    result: ToolCallSuccess | ToolCallFailure
    error: ToolCallError | None = None

    pre_check_error = _pre_call_checks(request, sso_token, sso_url)
    if pre_check_error is not None:
        result = _to_failure(pre_check_error)
        error = pre_check_error
    else:
        try:
            with sso_identity(sso_token, sso_url):
                payload = await call_tool(
                    request.connector.id,
                    request.connector.url,
                    request.tool,
                    request.args,
                    connector_bearer_token(request.connector.bearerTokenKey)
                    if request.connector.bearerTokenKey is not None
                    else None,
                )
            result = ToolCallSuccess(data=payload)
        except ConnectorToolError as tool_error:
            error = classify_connector_error(tool_error, request.connector.id, request.tool)
            result = _to_failure(error)
        except Exception as unexpected_error:
            logger.exception(
                "tool_call unexpected failure connector=%s tool=%s",
                request.connector.id,
                request.tool,
            )
            error = unexpected_failure(
                request.connector.id, request.tool, type(unexpected_error).__name__
            )
            result = _to_failure(error)

    _log_call(request, started_at, error)
    return result


def _to_failure(error: ToolCallError) -> ToolCallFailure:
    return ToolCallFailure(error=ToolCallErrorBody(code=error.code, message=error.message))


def _pre_call_checks(
    request: ToolCallRequest, sso_token: str | None, sso_url: str | None
) -> ToolCallError | None:
    settings = get_settings()
    for header_name, header_value in (
        (settings.SSO_TOKEN_HEADER, sso_token),
        (settings.SSO_URL_HEADER, sso_url),
    ):
        if not header_value:
            return missing_sso_header(header_name)

    bearer_token_key = request.connector.bearerTokenKey
    if bearer_token_key is not None and connector_bearer_token(bearer_token_key) is None:
        return bearer_key_unconfigured(request.connector.id, bearer_token_key)

    return None


def _log_call(request: ToolCallRequest, started_at: float, error: ToolCallError | None) -> None:
    elapsed_ms = round((time.monotonic() - started_at) * 1000)
    arg_keys_text = "[" + ",".join(sorted(request.args)) + "]"
    succeeded = error is None
    code = error.code if error is not None else "-"
    logger.info(
        "tool_call connector=%s tool=%s arg_keys=%s ms=%d ok=%s code=%s",
        request.connector.id,
        request.tool,
        arg_keys_text,
        elapsed_ms,
        "true" if succeeded else "false",
        code,
    )
