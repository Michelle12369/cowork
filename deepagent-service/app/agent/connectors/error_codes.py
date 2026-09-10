"""spec §4 分類表在程式碼裡唯一的落地處:五個錯誤 code、每一列的訊息模板函式、以及把
`ConnectorToolError` 分類成其中一個 code 的 classify_connector_error。純模組——不碰
FastAPI、settings 或任何 IO,方便 contract fixture 與測試直接呼叫模板函式核對訊息字串。"""

from dataclasses import dataclass
from typing import Literal

from app.agent.connectors.model import ConnectorToolError

ErrorCode = Literal["AUTH", "RETRYABLE", "TOOL_ERROR", "INVALID_CALL", "CONNECTOR_UNAVAILABLE"]

# HTTP 4xx 中被視為認證被拒的狀態碼(row 6)——其餘 4xx 都是 row 7(base URL 本身有問題)。
_REJECTED_CREDENTIAL_STATUSES = frozenset({401, 403})


@dataclass(frozen=True)
class ToolCallError:
    code: ErrorCode
    message: str


def missing_sso_header(header_name: str) -> ToolCallError:
    return ToolCallError("AUTH", f"sign-in required: missing {header_name}")


def bearer_key_unconfigured(connector_id: str, key: str) -> ToolCallError:
    return ToolCallError(
        "CONNECTOR_UNAVAILABLE",
        f"connector '{connector_id}' is misconfigured on the server (bearer token key '{key}' "
        "not configured); ask the connector owner",
    )


def empty_tool_name() -> ToolCallError:
    return ToolCallError("INVALID_CALL", "tool name is empty")


def args_not_object(type_name: str) -> ToolCallError:
    return ToolCallError("INVALID_CALL", f"args must be a JSON object, got {type_name}")


def no_response(connector_id: str, cause_name: str, attempts: int) -> ToolCallError:
    return ToolCallError(
        "RETRYABLE",
        f"connector '{connector_id}' did not respond ({cause_name}) after {attempts} attempts; "
        "retry",
    )


def credentials_rejected(connector_id: str, status: int) -> ToolCallError:
    return ToolCallError(
        "AUTH",
        f"connector '{connector_id}' rejected your credentials (HTTP {status}); sign in again",
    )


def base_url_error(connector_id: str, status: int) -> ToolCallError:
    return ToolCallError(
        "CONNECTOR_UNAVAILABLE",
        f"connector '{connector_id}' returned HTTP {status} at its base URL; ask the connector "
        "owner",
    )


def server_error(connector_id: str, status: int) -> ToolCallError:
    return ToolCallError("RETRYABLE", f"connector '{connector_id}' returned HTTP {status}; retry")


def tool_reported_error(tool: str, detail: str | None) -> ToolCallError:
    return ToolCallError("TOOL_ERROR", detail or f"tool '{tool}' failed with no message")


def no_structured_data(connector_id: str, tool: str) -> ToolCallError:
    return ToolCallError(
        "CONNECTOR_UNAVAILABLE",
        f"tool '{tool}' on connector '{connector_id}' no longer returns structured data; ask the "
        "connector owner",
    )


def unexpected_failure(connector_id: str, tool: str, type_name: str) -> ToolCallError:
    return ToolCallError(
        "RETRYABLE",
        f"unexpected failure calling '{connector_id}.{tool}' ({type_name}); retry",
    )


def classify_connector_error(
    error: ConnectorToolError, connector_id: str, tool: str
) -> ToolCallError:
    """kind → row 2、5–10. Row 2(config)也在這裡處理, 讓 wrapper 的 log line 能重用同一個
    分類器。"""
    if error.kind == "config":
        return bearer_key_unconfigured(connector_id, error.detail or "")
    if error.kind == "transport":
        return no_response(connector_id, error.cause_name or "Exception", error.attempts or 1)
    if error.kind == "http":
        status = error.status or 0
        if status in _REJECTED_CREDENTIAL_STATUSES:
            return credentials_rejected(connector_id, status)
        if 400 <= status < 500:
            return base_url_error(connector_id, status)
        return server_error(connector_id, status)
    if error.kind == "tool":
        return tool_reported_error(tool, error.detail)
    return no_structured_data(connector_id, tool)
