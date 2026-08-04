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


def test_load_runtime_logs_selected_runtime(monkeypatch, caplog) -> None:
    monkeypatch.delenv("AGENT_RUNTIME", raising=False)
    with caplog.at_level("INFO", logger="app.agent.runtime"):
        load_runtime()
    assert "runtime=deepagents" in caplog.text
    assert "app.agent.runtime.deepagents_runtime" in caplog.text


def test_build_model_logs_config_without_api_key(monkeypatch, caplog) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "super-secret-key")
    monkeypatch.setenv("AGENT_MODEL", "test-model")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://internal.example/v1")
    with caplog.at_level("INFO", logger="app.agent.runtime.deepagents_runtime"):
        DeepAgentsRuntime().build_model()
    assert "model=test-model" in caplog.text
    # base-url 只記有無設定：值可能是內部位址，NEVER 落進 log 蒐集系統。
    assert "baseUrlSet=True" in caplog.text
    assert "super-secret-key" not in caplog.text
    assert "internal.example" not in caplog.text
