"""測試用假 chat model——依建構時給定的 AIMessage 清單依序回放,供 test_events.py 與 Task 11
的 e2e 測試共用(deepagents 的 create_deep_agent 會對 model 呼叫 `.bind_tools(...)`,一般
BaseChatModel 假實作沒有這個方法,這裡回傳 self 讓 bind 後仍是同一顆假 model)。"""

from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field


class ScriptedChatModel(BaseChatModel):
    """`_generate` 依序 pop `scripted_messages`;回放耗盡時回空 content AIMessage 而非
    raise——deepagents 的迴圈可能比腳本長度多問一輪(例如工具呼叫後再確認一次),讓它拿到
    一個「沒事可做」的空答案比整個測試炸掉更貼近真實 gpt-oss 行為。

    `received_message_batches` 記下每次 `_generate` 收到的完整 messages 快照(依呼叫序
    append),供測試斷言「模型實際看到哪些訊息」(例如某條 system note 是否真的織進本輪
    context)——不影響回放行為,純觀察用。
    """

    scripted_messages: list[AIMessage]
    received_message_batches: list[list[BaseMessage]] = Field(default_factory=list)

    def __init__(self, scripted_messages: list[AIMessage], **kwargs: Any) -> None:
        super().__init__(
            scripted_messages=list(scripted_messages), received_message_batches=[], **kwargs
        )

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedChatModel":
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.received_message_batches.append(list(messages))
        if self.scripted_messages:
            message = self.scripted_messages.pop(0)
        else:
            message = AIMessage(content="")
        return ChatResult(generations=[ChatGeneration(message=message)])

    @property
    def _llm_type(self) -> str:
        return "scripted"


class FailingChatModel(BaseChatModel):
    """non-bean: instantiate per test. 一被呼叫就拋例外,用來驅動 /chat 的 ERROR 路徑。"""

    def bind_tools(self, tools: Any, **kwargs: Any) -> "FailingChatModel":
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        raise RuntimeError("scripted model failure")

    @property
    def _llm_type(self) -> str:
        return "failing"
