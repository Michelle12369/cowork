"""預設 AgentRuntime 實作:deepagents + ChatOpenAI + 記憶體 checkpointer。"""

import logging
from typing import Any

from deepagents import create_deep_agent
from deepagents.backends.filesystem import FilesystemBackend
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph

from app.agent.auth import token_exchange_http_clients
from app.config import get_settings

logger = logging.getLogger(__name__)


class DeepAgentsRuntime:
    def build_model(self) -> BaseChatModel:
        settings = get_settings()
        # 單次呼叫 output 上限(reasoning+正文+tool args);太低會切斷整份 dashboard 的單次寫入,
        # 0=交給 provider 預設。
        max_tokens_setting = settings.AGENT_MAX_TOKENS
        # reasoning 獨立預算(OpenRouter reasoning.max_tokens):把思考封頂,避免整個 output 預算
        # 燒在思考裡而正文歸零。0 = 不送 reasoning 參數,交給 provider 預設。
        reasoning_budget = settings.AGENT_REASONING_MAX_TOKENS
        extra_body: dict = {}
        if reasoning_budget > 0:
            extra_body["reasoning"] = {"max_tokens": reasoning_budget}
        # OpenRouter 供應商路由:sort=throughput 挑最快、ignore 排除黑名單;都不設=交給
        # OpenRouter 預設路由。
        provider_routing: dict = {}
        provider_sort = settings.AGENT_PROVIDER_SORT.strip()
        if provider_sort:
            provider_routing["sort"] = provider_sort
        provider_ignore = [
            name.strip() for name in settings.AGENT_PROVIDER_IGNORE.split(",") if name.strip()
        ]
        if provider_ignore:
            provider_routing["ignore"] = provider_ignore
        # require_parameters: 只路由到支援 tools 參數的 provider,避免整輪零工具呼叫。
        if settings.AGENT_PROVIDER_REQUIRE_PARAMETERS.strip().lower() == "true":
            provider_routing["require_parameters"] = True
        if provider_routing:
            extra_body["provider"] = provider_routing
        # internal 環境 AGENT_AUTH_MODE=token-exchange 時走自帶 client(j1→j2 交換＋401 重試,
        # 見 app.agent.auth);bearer 模式兩者為 None,SDK 用預設 client。
        sync_http_client, async_http_client = token_exchange_http_clients()
        # 只記「有無設定」不記 base-url 的值——它可能是 internal 位址。NEVER 記 api key。
        logger.info(
            "building chat model model=%s baseUrlSet=%s authMode=%s",
            settings.AGENT_MODEL,
            bool(settings.OPENAI_BASE_URL),
            settings.AGENT_AUTH_MODE,
        )
        return ChatOpenAI(
            model=settings.AGENT_MODEL,
            base_url=settings.OPENAI_BASE_URL or None,
            api_key=settings.OPENAI_API_KEY,
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
        subagents: list[dict[str, Any]],
    ) -> CompiledStateGraph:
        return create_deep_agent(
            model=model,
            tools=tools,
            system_prompt=system_prompt,
            backend=backend,
            skills=skills,
            checkpointer=checkpointer,
            middleware=middleware,
            subagents=subagents,
        )
