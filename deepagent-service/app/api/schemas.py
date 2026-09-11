"""定義 /chat、/repair 與 /tool-call 三個對外 API 的請求/回應 schema."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class HistoryItem(BaseModel):
    role: str
    text: str


class SourceItem(BaseModel):
    alias: str
    path: str
    fileType: str


class ConnectorSpec(BaseModel):
    id: str
    name: str
    url: str  # MCP server 的 base URL
    bearerTokenKey: str | None = (
        None  # 用來查 CONNECTOR_BEARER_TOKENS 表的 key, None 代表這個 connector 不需要認證
    )


class ChatRequest(BaseModel):
    sessionId: str
    userId: str
    message: str
    history: list[HistoryItem] = []
    sources: list[SourceItem] = []
    previousDashboardHtml: str | None = None
    connectors: list[
        ConnectorSpec
    ] = []  # 這一輪要用的 MCP connector 清單, 預設是空的, 代表不用任何 API 資料源, 走檔案模式


class RepairErrorItem(BaseModel):
    message: str


class RepairRequest(BaseModel):
    sessionId: str
    userId: str
    html: str
    errors: list[RepairErrorItem]


class ToolCallRequest(BaseModel):
    """Request body for `POST /tool-call`.

    **Validation errors become INVALID_CALL.** When this schema rejects a body, FastAPI answers
    HTTP 422. The Java proxy (and the spike bridge) turn that 422 into an `INVALID_CALL` result
    before it reaches the dashboard, so the page still sees the normal `{error: {code, message}}`
    shape.

    **Why `tool` must be non-empty.** fastmcp does not check the tool name on the client side.
    An empty name is sent to the MCP server, which answers `is_error` with `Unknown tool: ''`.
    `classify_connector_error` (app/agent/connectors/error_codes.py) would report that as
    `TOOL_ERROR`, which is the wrong code: the dashboard's call is malformed, not its argument
    values.

    **Why `args` must be a JSON object.** For a list or a string, fastmcp raises a
    `pydantic.ValidationError` inside its own client before anything is sent. Without this
    constraint that exception would reach the `except Exception` catch-all in `execute_tool_call`
    (app/agent/connectors/tool_call_flow.py) and be reported as `RETRYABLE`, showing the viewer
    a Retry button that can never succeed.
    """

    connector: ConnectorSpec
    tool: str = Field(min_length=1)
    args: dict[str, Any]


class ToolCallErrorBody(BaseModel):
    code: Literal["AUTH", "RETRYABLE", "TOOL_ERROR", "INVALID_CALL", "CONNECTOR_UNAVAILABLE"]
    message: str


class ToolCallSuccess(BaseModel):
    data: Any  # structured_content, 原樣未動


class ToolCallFailure(BaseModel):
    error: ToolCallErrorBody
