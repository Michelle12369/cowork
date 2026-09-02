"""`/chat` 與 `/repair` 的對外請求介面定義。"""

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
    url: str  # MCP server base URL
    bearerTokenKey: str | None = (
        None  # CONNECTOR_BEARER_TOKENS 查表 key;None＝此 connector 不需認證
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
    ] = []  # 本輪使用的 MCP connector 清單;預設空＝不使用任何 API 資料源(檔案模式)


class RepairErrorItem(BaseModel):
    message: str


class RepairRequest(BaseModel):
    sessionId: str
    userId: str
    html: str
    errors: list[RepairErrorItem]
