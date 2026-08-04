"""預設 AgentRuntime 實作:deepagents + ChatOpenAI + 記憶體 checkpointer。"""

import logging
import os
from typing import Any

from deepagents import create_deep_agent
from deepagents.backends.filesystem import FilesystemBackend
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph

from app.agent.auth import token_exchange_http_clients

logger = logging.getLogger(__name__)


class DeepAgentsRuntime:
    def build_model(self) -> BaseChatModel:
        # 單次呼叫 output 上限(reasoning+正文+tool args);太低會切斷整份 dashboard 的單次寫入,
        # 0=交給 provider 預設。
        max_tokens_setting = int(os.environ.get("AGENT_MAX_TOKENS", "32768"))
        # reasoning 獨立預算(OpenRouter reasoning.max_tokens):把思考封頂,避免整個 output 預算
        # 燒在思考裡而正文歸零。0 = 不送 reasoning 參數,交給 provider 預設。
        reasoning_budget = int(os.environ.get("AGENT_REASONING_MAX_TOKENS", "8192"))
        extra_body: dict = {}
        if reasoning_budget > 0:
            extra_body["reasoning"] = {"max_tokens": reasoning_budget}
        # OpenRouter 供應商路由:sort=throughput 挑最快、ignore 排除黑名單;都不設=交給
        # OpenRouter 預設路由。
        provider_routing: dict = {}
        provider_sort = os.environ.get("AGENT_PROVIDER_SORT", "").strip()
        if provider_sort:
            provider_routing["sort"] = provider_sort
        provider_ignore = [
            name.strip()
            for name in os.environ.get("AGENT_PROVIDER_IGNORE", "").split(",")
            if name.strip()
        ]
        if provider_ignore:
            provider_routing["ignore"] = provider_ignore
        if provider_routing:
            extra_body["provider"] = provider_routing
        # internal 環境 AGENT_AUTH_MODE=token-exchange 時走自帶 client(j1→j2 交換＋401 重試,
        # 見 app.agent.auth);bearer 模式兩者為 None,SDK 用預設 client。
        sync_http_client, async_http_client = token_exchange_http_clients()
        # 只記「有無設定」不記 base-url 的值——它可能是 internal 位址。NEVER 記 api key。
        logger.info(
            "building chat model model=%s baseUrlSet=%s authMode=%s",
            os.environ.get("AGENT_MODEL", "qwen3.6-35b"),
            bool(os.environ.get("OPENAI_BASE_URL")),
            os.environ.get("AGENT_AUTH_MODE", "bearer"),
        )
        return ChatOpenAI(
            model=os.environ.get("AGENT_MODEL", "qwen3.6-35b"),
            base_url=os.environ.get("OPENAI_BASE_URL") or None,
            api_key=os.environ.get("OPENAI_API_KEY", "unused"),
            streaming=True,
            temperature=0,
            max_tokens=max_tokens_setting if max_tokens_setting > 0 else None,
            extra_body=extra_body or None,
            http_client=sync_http_client,
            http_async_client=async_http_client,
        )

    def build_checkpointer(self) -> BaseCheckpointSaver:
        return InMemorySaver()

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
    ) -> CompiledStateGraph:
        return create_deep_agent(
            model=model,
            tools=tools,
            system_prompt=system_prompt,
            backend=backend,
            skills=skills,
            checkpointer=checkpointer,
            middleware=middleware,
        )
