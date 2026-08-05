"""init_langfuse：顯式建構、半套 key fail-loud、mask 經 runtime seam 傳入。"""

import pytest

import app.agent.tracing as tracing_module
from app.agent.tracing import init_langfuse
from app.config import Settings


class _FakeLangfuse:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _RuntimeWithMask:
    def build_langfuse_mask(self):
        return _mask_function


class _RuntimeWithoutMask:
    pass


def _mask_function(**kwargs):
    return kwargs


def _settings(**overrides) -> Settings:
    return Settings(**overrides)  # init kwargs 來源（見 config 的 init_settings）


def test_both_keys_absent_is_noop(monkeypatch):
    created = []
    monkeypatch.setattr(tracing_module, "Langfuse", lambda **kw: created.append(kw))
    init_langfuse(_settings(), _RuntimeWithMask())
    assert created == []


@pytest.mark.parametrize(
    "overrides", [{"LANGFUSE_PUBLIC_KEY": "pk"}, {"LANGFUSE_SECRET_KEY": "sk"}]
)
def test_half_configured_fails_loud(overrides):
    with pytest.raises(RuntimeError, match="LANGFUSE"):
        init_langfuse(_settings(**overrides), _RuntimeWithMask())


def test_full_config_builds_client_with_mask(monkeypatch):
    monkeypatch.setattr(tracing_module, "Langfuse", _FakeLangfuse)
    client_holder = {}
    monkeypatch.setattr(
        tracing_module, "Langfuse", lambda **kw: client_holder.setdefault("kwargs", kw)
    )
    init_langfuse(
        _settings(
            LANGFUSE_PUBLIC_KEY="pk", LANGFUSE_SECRET_KEY="sk", LANGFUSE_HOST="https://lf.corp"
        ),
        _RuntimeWithMask(),
    )
    assert client_holder["kwargs"] == {
        "public_key": "pk",
        "secret_key": "sk",
        "host": "https://lf.corp",
        "mask": _mask_function,
    }


def test_runtime_without_mask_method_passes_none(monkeypatch):
    client_holder = {}
    monkeypatch.setattr(
        tracing_module, "Langfuse", lambda **kw: client_holder.setdefault("kwargs", kw)
    )
    init_langfuse(
        _settings(LANGFUSE_PUBLIC_KEY="pk", LANGFUSE_SECRET_KEY="sk"), _RuntimeWithoutMask()
    )
    assert client_holder["kwargs"]["mask"] is None
    assert client_holder["kwargs"]["host"] is None
