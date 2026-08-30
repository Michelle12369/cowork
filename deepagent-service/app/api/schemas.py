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
    # SSO token,供 connector 逐請求呼叫外部 API 時附加；NEVER 進 log/prompt/落盤。
    # dev/無 SSO 環境為 None。
    ssoToken: str | None = None
    # 使用者本輪勾選的 connector id 清單；預設空＝不使用任何 API 資料源。
    selectedConnectors: list[str] = []


class RepairErrorItem(BaseModel):
    message: str


class RepairRequest(BaseModel):
    sessionId: str
    userId: str
    html: str
    errors: list[RepairErrorItem]
    # SSO token,語意同 ChatRequest.ssoToken；NEVER 進 log/prompt/落盤。
    ssoToken: str | None = None
