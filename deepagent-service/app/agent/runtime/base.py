"""AgentRuntime 定義 agent 建構層的三個接縫: model, checkpointer, agent 各自怎麼建立. internal
環境會用另一份實作整組換掉這三個, 所以介面型別統一用 langchain/langgraph 的 base type; 因為
internal 那個函式庫本身就是 langgraph 的 wrapper, 兩邊實作自然能滿足同一組簽名."""

from typing import Any, Protocol

from deepagents.backends.filesystem import FilesystemBackend
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph


class AgentRuntime(Protocol):
    def build_model(self) -> BaseChatModel: ...

    def build_checkpointer(self) -> BaseCheckpointSaver: ...

    def build_agent(
        self,
        *,
        model: BaseChatModel,
        tools: list[Any],
        system_prompt: str,
        backend: FilesystemBackend,
        skills: list[str] | None,
        checkpointer: BaseCheckpointSaver,
        middleware: list[Any],
    ) -> CompiledStateGraph:
        """skills=None 代表呼叫端自己在 middleware list 裡掛了 skills middleware
        (例如每輪重新掃描的變體), deepagents 就不會再掛內建的 SkillsMiddleware."""
        ...

    def build_langfuse(self) -> Any | None:
        """建立並回傳 Langfuse client, 回傳 None 代表 tracing 關閉.
        呼叫端一律用 getattr 加預設值來讀, 沒實作這個方法的 runtime 會退回內建流程."""
        ...
