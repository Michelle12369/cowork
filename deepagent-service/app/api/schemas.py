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
    """Request body for `POST /tool-call`. A 422 raised here is folded to `INVALID_CALL` by the
    Java proxy / spike bridge before it reaches a viewer. Both constraints are enforced by this
    schema, not left to the MCP call: fastmcp does not reject an empty tool name client-side, so it
    would reach the server and come back `is_error: Unknown tool: ''` (classified `TOOL_ERROR`,
    the wrong code for a malformed call); non-object `args` makes fastmcp raise a
    `pydantic.ValidationError` inside its own client, which the classifier's fallback would read
    as `RETRYABLE` (inviting a pointless Retry button)."""

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
