"""`/chat` SSE wire 事件的 typed 契約，鏡射 Java `backend/.../agent/event/*.java` 的 DTO 名稱
與欄位（`AgentEvent` 的 `@JsonSubTypes`）。`type` 用 `Literal[...]` 定值，字串只在一處宣告；
`EventBridge`/`ChatTurn`/`main.py` 一律建構這些類別而非手刻 dict，欄位名打錯或事件型別比對
打錯會在建構/型別檢查時當場炸掉，不會像 dict 一樣悄悄過關。
"""

from typing import Literal

from pydantic import BaseModel


class StepEvent(BaseModel):
    type: Literal["STEP"] = "STEP"
    stepKey: str
    title: str
    status: str


class TokenEvent(BaseModel):
    type: Literal["TOKEN"] = "TOKEN"
    delta: str


class TableEvent(BaseModel):
    type: Literal["TABLE"] = "TABLE"
    tableId: str
    intent: str
    columns: list[str]
    rows: list[list[object]]
    truncated: bool


class DashboardHtmlEvent(BaseModel):
    """DASHBOARD_HTML 事件——刻意沒有 Java 對應類別。`LangGraphAnalysisProvider` 在 Jackson
    反序列化前先用 `type` 欄位攔截並特殊處理這個事件，所以它不在 Java 端 `AgentEvent` 的
    `@JsonSubTypes` 清單裡。NEVER 為了「補齊」這個不對稱而新增 Java class——那是設計如此。
    正因如此，這裡加欄位是 additive-safe：Java 端沒有對應 class 可能漏欄，多出的
    `recipe`/`hasUploadSources` 不會撞上任何 Jackson 反序列化。
    """

    type: Literal["DASHBOARD_HTML"] = "DASHBOARD_HTML"
    html: str
    recipe: dict | None = None
    hasUploadSources: bool = False


class AnswerEvent(BaseModel):
    type: Literal["ANSWER"] = "ANSWER"
    text: str


class ClarifyingQuestion(BaseModel):
    text: str
    options: list[str]
    multiSelect: bool


class QuestionEvent(BaseModel):
    type: Literal["QUESTION"] = "QUESTION"
    questions: list[ClarifyingQuestion]


class ErrorEvent(BaseModel):
    type: Literal["ERROR"] = "ERROR"
    code: str
    message: str


# `ChatTurn`/main.py 的 SSE handler 共用的事件聯集型別註記。
WireEvent = (
    StepEvent
    | TokenEvent
    | TableEvent
    | DashboardHtmlEvent
    | AnswerEvent
    | QuestionEvent
    | ErrorEvent
)
