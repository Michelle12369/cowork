from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver

from app.agent.runtime.deepagents_runtime import DeepAgentsRuntime


def test_deepagents_runtime_builds_model(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "unused")
    assert isinstance(DeepAgentsRuntime().build_model(), BaseChatModel)


def test_deepagents_runtime_builds_checkpointer() -> None:
    checkpointer = DeepAgentsRuntime().build_checkpointer()
    assert isinstance(checkpointer, BaseCheckpointSaver)
    # 每次呼叫 MUST 是新實例——reset_for_tests() 靠這點清掉跨測試殘留的 thread 歷史。
    assert checkpointer is not DeepAgentsRuntime().build_checkpointer()


import pytest

from app.agent.runtime import load_runtime


@pytest.fixture(autouse=True)
def _clear_runtime_cache():
    load_runtime.cache_clear()
    yield
    load_runtime.cache_clear()


def test_load_runtime_defaults_to_deepagents(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_RUNTIME", raising=False)
    assert isinstance(load_runtime(), DeepAgentsRuntime)


def test_load_runtime_internal_without_impl_raises_with_module_name(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_RUNTIME", "internal")
    with pytest.raises(RuntimeError) as error:
        load_runtime()
    # 訊息 MUST 指出缺哪個模組，否則公司端只會看到一句無資訊的啟動失敗。
    assert "app.agent.runtime.internal_runtime" in str(error.value)


def test_load_runtime_unknown_value_raises(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_RUNTIME", "nope")
    with pytest.raises(RuntimeError) as error:
        load_runtime()
    assert "nope" in str(error.value)
