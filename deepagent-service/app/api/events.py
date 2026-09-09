"""/chat SSE wire 事件的型別化契約, 類別名稱與欄位對應到 Java backend 的 agent event DTO.
一律建構這些類別而不是手刻 dict, 欄位打錯或型別比對錯了會在建構時直接爆掉."""

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


class DashboardHtmlEvent(BaseModel):
    """DASHBOARD_HTML 事件故意沒有對應的 Java 類別. LangGraphAnalysisProvider 會在 Jackson
    反序列化之前先用 type 欄位攔截並特別處理這個事件, 所以它不在 Java 端 AgentEvent 的
    JsonSubTypes 清單裡. 不需要為了補齊這個不對稱而新增 Java class, 這是刻意的設計.
    """

    type: Literal["DASHBOARD_HTML"] = "DASHBOARD_HTML"
    html: str


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


# ChatTurn 與 main.py 的 SSE handler 共用這個事件聯集型別.
WireEvent = StepEvent | TokenEvent | DashboardHtmlEvent | AnswerEvent | QuestionEvent | ErrorEvent
