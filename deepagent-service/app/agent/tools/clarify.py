"""`ask_user` 反問工具——需求模糊時的結構化出口。走 tool args 而非文字協定：deepagent 在
工具啟動前的模型文字會以 TOKEN 直接串流上畫面，文字協定會讓使用者看到原始 JSON。

`QuestionHolder` 比照 `ToolResultRecorder`：per-request 建立（app.main.chat）、tool 在
executor thread 寫入、SSE handler 在 stream 結束後讀取，同一把 lock 保護。"""

import threading

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

# 單一 /chat 請求內反問題數的累計硬上限——跨多次 ask_user 呼叫累計，超出靜默捨棄。
MAX_QUESTIONS = 3

# 回給模型的固定指示：結束回合，不做分析。
ASK_USER_TOOL_RESULT = (
    "Questions delivered to the user. End your turn NOW with one short Traditional-Chinese "
    "sentence asking the user to answer them; do NOT call any more tools and do NOT start "
    "the analysis."
)


class ClarifyQuestion(BaseModel):
    """單一反問題目——欄位即 LLM 可見的 args schema；wire 端 multiSelect 為 camelCase。"""

    text: str = Field(description="The question, in Traditional Chinese.")
    options: list[str] = Field(
        default_factory=list,
        description="2-4 short answer choices in Traditional Chinese; [] for free-form.",
    )
    multi_select: bool = Field(
        default=False, description="Whether the user may pick multiple options."
    )

    def to_wire(self) -> dict:
        return {"text": self.text, "options": self.options, "multiSelect": self.multi_select}


class QuestionHolder:
    """non-bean: instantiate per /chat request."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._questions: list[ClarifyQuestion] = []

    def add(self, questions: list[ClarifyQuestion]) -> None:
        with self._lock:
            remaining_capacity = MAX_QUESTIONS - len(self._questions)
            if remaining_capacity > 0:
                self._questions.extend(questions[:remaining_capacity])

    def questions(self) -> list[ClarifyQuestion]:
        with self._lock:
            return list(self._questions)


def build_ask_user_tool(holder: QuestionHolder) -> BaseTool:
    @tool("ask_user")
    def ask_user_tool(questions: list[ClarifyQuestion]) -> str:
        """Ask the user clarifying questions BEFORE starting any analysis.

        Call this when the request is ambiguous or has several reasonable interpretations
        (unclear metric, scope, time range, grouping, or chart preference). At most 3
        questions per turn; write text and options in Traditional Chinese. After calling,
        end your turn -- do not run other tools.
        """
        holder.add(questions)
        return ASK_USER_TOOL_RESULT

    return ask_user_tool
