"""scripts/dev_chat.py 純函式的行為測試: connector 參數解析、合併、header 組裝."""

import importlib.util
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "dev_chat.py"
spec = importlib.util.spec_from_file_location("dev_chat", SCRIPT_PATH)
assert spec is not None and spec.loader is not None
dev_chat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dev_chat)


def test_parse_connector_idAndUrlOnly_defaultsNameAndNoTokenKey() -> None:
    connector = dev_chat.parse_connector(["sales", "http://127.0.0.1:8765/mcp"])
    assert connector == {
        "id": "sales",
        "name": "Sales",
        "url": "http://127.0.0.1:8765/mcp",
        "bearerTokenKey": None,
    }


def test_parse_connector_fourTokens_keepsNameAndTokenKey() -> None:
    connector = dev_chat.parse_connector(["crm", "https://crm.example/mcp", "CRM", "crm-key"])
    assert connector["name"] == "CRM"
    assert connector["bearerTokenKey"] == "crm-key"


@pytest.mark.parametrize("tokens", [["sales"], ["a", "b", "c", "d", "e"], ["", "http://x"]])
def test_parse_connector_badArity_raisesValueError(tokens: list[str]) -> None:
    with pytest.raises(ValueError):
        dev_chat.parse_connector(tokens)


def test_merge_connectors_sameId_overridesInPlaceAndAppendsNew() -> None:
    existing = [
        {"id": "sales", "name": "Sales", "url": "http://old/mcp", "bearerTokenKey": None},
        {"id": "crm", "name": "CRM", "url": "http://crm/mcp", "bearerTokenKey": None},
    ]
    incoming = [
        {"id": "sales", "name": "Sales", "url": "http://new/mcp", "bearerTokenKey": None},
        {"id": "hr", "name": "HR", "url": "http://hr/mcp", "bearerTokenKey": "hr-key"},
    ]
    merged = dev_chat.merge_connectors(existing, incoming)
    assert [connector["id"] for connector in merged] == ["sales", "crm", "hr"]
    assert merged[0]["url"] == "http://new/mcp"


def test_build_headers_noConnectors_onlyBearer() -> None:
    headers = dev_chat.build_headers(
        bearer_token="secret",
        has_connectors=False,
        sso_token=None,
        sso_url=None,
        sso_token_header="X-SSO-Token",
        sso_url_header="X-SSO-Url",
    )
    assert headers == {"authorization": "Bearer secret"}


def test_build_headers_connectorsWithoutSso_sendsDummySsoHeaders() -> None:
    headers = dev_chat.build_headers(
        bearer_token="secret",
        has_connectors=True,
        sso_token=None,
        sso_url=None,
        sso_token_header="X-SSO-Token",
        sso_url_header="X-SSO-Url",
    )
    assert headers["authorization"] == "Bearer secret"
    assert headers["X-SSO-Token"] == dev_chat.DUMMY_SSO_TOKEN
    assert headers["X-SSO-Url"] == dev_chat.DUMMY_SSO_URL


def test_build_headers_connectorsWithSso_usesGivenValuesAndHeaderNames() -> None:
    headers = dev_chat.build_headers(
        bearer_token="secret",
        has_connectors=True,
        sso_token="tok",
        sso_url="https://sso.example",
        sso_token_header="X-Custom-Token",
        sso_url_header="X-Custom-Url",
    )
    assert headers["X-Custom-Token"] == "tok"
    assert headers["X-Custom-Url"] == "https://sso.example"
    assert "X-SSO-Token" not in headers
