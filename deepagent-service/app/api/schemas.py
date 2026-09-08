"""定義 /chat 與 /repair 兩個對外 API 的請求 schema."""

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
