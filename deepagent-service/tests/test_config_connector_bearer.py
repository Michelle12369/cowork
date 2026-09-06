"""connector_bearer_token() 對 CONNECTOR_BEARER_TOKENS 這個 dict 欄位的取值語意."""

import pytest

from app.config import connector_bearer_token, get_settings


@pytest.fixture(autouse=True)
def reset_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_unset_returns_none(monkeypatch):
    monkeypatch.delenv("CONNECTOR_BEARER_TOKENS", raising=False)
    assert connector_bearer_token("mes") is None


def test_blank_env_value_returns_none(monkeypatch):
    monkeypatch.setenv("CONNECTOR_BEARER_TOKENS", "")
    assert connector_bearer_token("mes") is None


def test_valid_json_matching_key_returns_token(monkeypatch):
    monkeypatch.setenv("CONNECTOR_BEARER_TOKENS", '{"mes": "secret-token-value"}')
    assert connector_bearer_token("mes") == "secret-token-value"


def test_valid_json_missing_key_returns_none(monkeypatch):
    monkeypatch.setenv("CONNECTOR_BEARER_TOKENS", '{"mes": "secret-token-value"}')
    assert connector_bearer_token("other-key") is None


def test_valid_json_empty_string_value_returns_none(monkeypatch):
    monkeypatch.setenv("CONNECTOR_BEARER_TOKENS", '{"mes": ""}')
    assert connector_bearer_token("mes") is None


def test_invalid_json_fails_settings_construction_without_leaking_value(monkeypatch):
    monkeypatch.setenv("CONNECTOR_BEARER_TOKENS", "not-json-SECRETVALUE123")
    get_settings.cache_clear()
    with pytest.raises(ValueError) as excinfo:
        get_settings()
    assert "SECRETVALUE123" not in str(excinfo.value)


def test_connector_call_budget_default_and_override(monkeypatch):
    monkeypatch.delenv("CONNECTOR_CALL_BUDGET", raising=False)
    get_settings.cache_clear()
    assert get_settings().CONNECTOR_CALL_BUDGET == 50
    monkeypatch.setenv("CONNECTOR_CALL_BUDGET", "3")
    get_settings.cache_clear()
    assert get_settings().CONNECTOR_CALL_BUDGET == 3
