"""init_langfuse:runtime 提供 build_langfuse 時完整交由它接管;否則走 OSS 預設路徑
(顯式建構、半套 key fail-loud)。is_tracing_enabled() 反映每次呼叫的結果。"""

import pytest

import app.agent.tracing as tracing_module
from app.agent.tracing import init_langfuse, is_tracing_enabled
from app.config import Settings


class _RuntimeWithBuilder:
    def __init__(self, client):
        self._client = client
        self.calls = []

    def build_langfuse(self):
        self.calls.append("invoked")
        return self._client


class _RuntimeWithoutBuilder:
    pass


def _settings(**overrides) -> Settings:
    return Settings(**overrides)  # init kwargs 來源(見 config 的 init_settings)


def test_runtime_builder_called_once_enables_when_client_returned(monkeypatch):
    created = []
    monkeypatch.setattr(tracing_module, "Langfuse", lambda **kw: created.append(kw))
    settings = _settings()
    runtime = _RuntimeWithBuilder(client=object())

    init_langfuse(settings, runtime)

    assert runtime.calls == ["invoked"]
    assert is_tracing_enabled() is True
    assert created == []  # OSS 預設建構路徑完全不應被呼叫


def test_runtime_builder_returns_none_disables(monkeypatch):
    created = []
    monkeypatch.setattr(tracing_module, "Langfuse", lambda **kw: created.append(kw))
    runtime = _RuntimeWithBuilder(client=None)

    init_langfuse(_settings(), runtime)

    assert is_tracing_enabled() is False
    assert created == []


def test_both_keys_absent_is_noop_and_disabled(monkeypatch):
    created = []
    monkeypatch.setattr(tracing_module, "Langfuse", lambda **kw: created.append(kw))
    init_langfuse(_settings(), _RuntimeWithoutBuilder())
    assert created == []
    assert is_tracing_enabled() is False


@pytest.mark.parametrize(
    "overrides", [{"LANGFUSE_PUBLIC_KEY": "pk"}, {"LANGFUSE_SECRET_KEY": "sk"}]
)
def test_half_configured_fails_loud(overrides):
    with pytest.raises(RuntimeError, match="LANGFUSE"):
        init_langfuse(_settings(**overrides), _RuntimeWithoutBuilder())


def test_default_path_builds_client_with_mask_none_and_enables(monkeypatch):
    client_holder = {}
    monkeypatch.setattr(
        tracing_module, "Langfuse", lambda **kw: client_holder.setdefault("kwargs", kw)
    )
    init_langfuse(
        _settings(
            LANGFUSE_PUBLIC_KEY="pk", LANGFUSE_SECRET_KEY="sk", LANGFUSE_HOST="https://lf.corp"
        ),
        _RuntimeWithoutBuilder(),
    )
    assert client_holder["kwargs"] == {
        "public_key": "pk",
        "secret_key": "sk",
        "host": "https://lf.corp",
        "mask": None,
    }
    assert is_tracing_enabled() is True
