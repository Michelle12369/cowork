"""AgentRuntime -- agent 建構層的三個接縫點。internal 環境以另一個實作整組替換 model、
checkpointer 與 agent 的建立方式;型別一律用 langchain/langgraph base type,因為 internal lib
是 langgraph wrapper,兩個實作天然滿足同一組簽名。"""

from typing import TYPE_CHECKING, Any, Protocol

from deepagents.backends.filesystem import FilesystemBackend
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

if TYPE_CHECKING:
    from app.config import Settings


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
        subagents: list[dict[str, Any]],
    ) -> CompiledStateGraph: ...

    def build_langfuse(self, settings: "Settings") -> Any | None:
        """建構並回傳 Langfuse client（建構子本身會註冊全域 client，CallbackHandler 依賴它），
        回 None＝tracing 關閉。internal 覆寫以完整接管建構（自家 host/auth/mask/wrapper）。
        取用端一律 getattr fallback——結構實作可不提供此方法，OSS 預設建構路徑接手。"""
        ...
