"""AgentRuntime -- agent 建構層的三個接縫點。internal 環境以另一個實作整組替換 model、
checkpointer 與 agent 的建立方式;型別一律用 langchain/langgraph base type,因為 internal lib
是 langgraph wrapper,兩個實作天然滿足同一組簽名。"""

from collections.abc import Callable
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
        skills: list[str],
        checkpointer: BaseCheckpointSaver,
        middleware: list[Any],
    ) -> CompiledStateGraph: ...

    def build_langfuse_mask(self) -> Callable[..., Any] | None:
        """Langfuse mask function;OSS 環境無遮罩需求回 None。internal 覆寫回傳公司 lib 的
        mask。取用端一律 getattr fallback——結構實作可不提供此方法。"""
        ...
