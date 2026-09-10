"""定義 /chat、/repair 與 /tool-call 三個對外 API 的請求/回應 schema."""

from typing import Any, Literal

from pydantic import BaseModel


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
    connector: ConnectorSpec
    tool: str  # 空字串是 row-3 INVALID_CALL, 不是 422
    args: Any  # 非 JSON object 是 row-3 INVALID_CALL, 不是 422


class ToolCallErrorBody(BaseModel):
    code: Literal["AUTH", "RETRYABLE", "TOOL_ERROR", "INVALID_CALL", "CONNECTOR_UNAVAILABLE"]
    message: str


class ToolCallSuccess(BaseModel):
    data: Any  # structured_content, 原樣未動


class ToolCallFailure(BaseModel):
    error: ToolCallErrorBody
