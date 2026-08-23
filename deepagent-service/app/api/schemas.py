"""`/chat`、`/repair`、`/replay` 的對外請求介面定義。"""

from pydantic import BaseModel


class HistoryItem(BaseModel):
    role: str
    text: str


class SourceItem(BaseModel):
    alias: str
    path: str
    fileType: str


class ChatRequest(BaseModel):
    sessionId: str
    userId: str
    message: str
    history: list[HistoryItem] = []
    sources: list[SourceItem] = []
    # 使用者選定歷史版本繼續編輯時帶上該版「注入後」rawHtml；沒選就沒有這個 key。
    # 基底重建見 `ChatTurn.__aenter__` 內 mtime 快照之前那段。
    previousDashboardHtml: str | None = None
    # 使用者勾選的 connector group 名稱；空=不變式=全部可見(見 ConnectorRegistry.filter_by_groups)。
    selectedGroups: list[str] = []


class ConnectorGroupInfo(BaseModel):
    name: str
    display: str
    description: str


class RepairErrorItem(BaseModel):
    message: str


class RepairRequest(BaseModel):
    sessionId: str
    userId: str
    html: str
    errors: list[RepairErrorItem]


class ReplayError(BaseModel):
    code: str
    message: str


class ReplayRequest(BaseModel):
    recipe: dict
    html: str
    # viewerToken/paramsOverride 簽名先在但 2a 不使用——viewer 身分透傳與參數互動式重放留給
    # 後續分期(design §7)。
    viewerToken: str | None = None
    paramsOverride: dict | None = None


class ReplayResponse(BaseModel):
    html: str | None = None
    error: ReplayError | None = None
