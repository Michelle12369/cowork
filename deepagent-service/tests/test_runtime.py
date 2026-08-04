from app.agent.runtime.deepagents_runtime import DeepAgentsRuntime
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver


def test_deepagents_runtime_builds_model(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "unused")
    assert isinstance(DeepAgentsRuntime().build_model(), BaseChatModel)


def test_deepagents_runtime_builds_checkpointer() -> None:
    checkpointer = DeepAgentsRuntime().build_checkpointer()
    assert isinstance(checkpointer, BaseCheckpointSaver)
    # 每次呼叫 MUST 是新實例——reset_for_tests() 靠這點清掉跨測試殘留的 thread 歷史。
    assert checkpointer is not DeepAgentsRuntime().build_checkpointer()
