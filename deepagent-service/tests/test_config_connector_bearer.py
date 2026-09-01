"""`connector_bearer_token()`——CONNECTOR_BEARER_TOKENS(JSON dict 字串)取值語意。"""

import pytest

from app.config import SecretResolutionError, connector_bearer_token, get_settings


@pytest.fixture(autouse=True)
def reset_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_unset_returns_none(monkeypatch):
    monkeypatch.delenv("CONNECTOR_BEARER_TOKENS", raising=False)
    assert connector_bearer_token("mes") is None


def test_empty_string_returns_none(monkeypatch):
    monkeypatch.setenv("CONNECTOR_BEARER_TOKENS", "")
    assert connector_bearer_token("mes") is None


def test_valid_json_matching_id_returns_token(monkeypatch):
    monkeypatch.setenv("CONNECTOR_BEARER_TOKENS", '{"mes": "secret-token-value"}')
    assert connector_bearer_token("mes") == "secret-token-value"


def test_valid_json_missing_id_returns_none(monkeypatch):
    monkeypatch.setenv("CONNECTOR_BEARER_TOKENS", '{"mes": "secret-token-value"}')
    assert connector_bearer_token("other-connector") is None


def test_valid_json_empty_string_value_returns_none(monkeypatch):
    monkeypatch.setenv("CONNECTOR_BEARER_TOKENS", '{"mes": ""}')
    assert connector_bearer_token("mes") is None


def test_invalid_json_raises_secret_resolution_error(monkeypatch):
    monkeypatch.setenv("CONNECTOR_BEARER_TOKENS", "{not-json")
    with pytest.raises(SecretResolutionError):
        connector_bearer_token("mes")


def test_json_list_raises_secret_resolution_error(monkeypatch):
    monkeypatch.setenv("CONNECTOR_BEARER_TOKENS", '["mes", "secret-token-value"]')
    with pytest.raises(SecretResolutionError):
        connector_bearer_token("mes")
