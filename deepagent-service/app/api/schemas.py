"""`/chat` 與 `/repair` 的對外請求介面定義。"""

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


class RepairErrorItem(BaseModel):
    message: str


class RepairRequest(BaseModel):
    sessionId: str
    userId: str
    html: str
    errors: list[RepairErrorItem]
