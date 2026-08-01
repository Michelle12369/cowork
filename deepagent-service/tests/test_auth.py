"""TokenExchangeAuth 行為測試——快取/TTL/401 重試/key file 重讀,sync 與 async 雙流。"""

import json

import httpx
import pytest

from app.agent import auth as auth_module
from app.agent.auth import TokenExchangeAuth, token_exchange_http_clients

EXCHANGE_URL = "http://auth.internal/exchange"
API_URL = "http://llm.internal/v1/chat/completions"
HEADER = "X-Test-Token-Header"


def make_auth(**overrides):
    """建一個 exchange endpoint 被 mock 掉的 auth;回傳 (auth, exchange_requests)。"""
    exchange_requests: list[httpx.Request] = []

    def exchange_handler(request: httpx.Request) -> httpx.Response:
        exchange_requests.append(request)
        return httpx.Response(200, json={"token": f"j2-{len(exchange_requests)}"})

    transport = httpx.MockTransport(exchange_handler)
    kwargs = {
        "exchange_url": EXCHANGE_URL,
        "header_name": HEADER,
        "ttl_seconds": 300,
        "service_account_key": "j1-key",
        "exchange_transport": transport,
        "async_exchange_transport": transport,
    }
    kwargs.update(overrides)
    return TokenExchangeAuth(**kwargs), exchange_requests


def test_token_injected_and_cached_within_ttl() -> None:
    token_auth, exchange_requests = make_auth()
    seen_headers: list[httpx.Headers] = []

    def api_handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(request.headers)
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(api_handler), auth=token_auth)
    client.post(API_URL, headers={"Authorization": "Bearer unused"})
    client.post(API_URL, headers={"Authorization": "Bearer unused"})

    assert len(exchange_requests) == 1  # TTL 內共用快取 token
    assert seen_headers[0][HEADER] == "j2-1"
    assert seen_headers[1][HEADER] == "j2-1"
    # 鏡像 Java:token-exchange 模式不送 Bearer
    assert "authorization" not in seen_headers[0]


def test_401_invalidates_and_retries_once_with_fresh_token() -> None:
    token_auth, exchange_requests = make_auth()
    api_statuses = [401, 200]
    seen_tokens: list[str] = []

    def api_handler(request: httpx.Request) -> httpx.Response:
        seen_tokens.append(request.headers[HEADER])
        return httpx.Response(api_statuses[len(seen_tokens) - 1], json={})

    client = httpx.Client(transport=httpx.MockTransport(api_handler), auth=token_auth)
    response = client.post(API_URL)

    assert response.status_code == 200
    assert seen_tokens == ["j2-1", "j2-2"]  # 401 後換新 token 重試
    assert len(exchange_requests) == 2


def test_persistent_401_gives_up_after_single_retry() -> None:
    token_auth, exchange_requests = make_auth()
    attempt_count = 0

    def api_handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempt_count
        attempt_count += 1
        return httpx.Response(401, json={})

    client = httpx.Client(transport=httpx.MockTransport(api_handler), auth=token_auth)
    response = client.post(API_URL)

    assert response.status_code == 401  # 重試一次後放棄,把 401 交回呼叫端
    assert attempt_count == 2
    assert len(exchange_requests) == 2


def test_ttl_expiry_triggers_reexchange() -> None:
    token_auth, exchange_requests = make_auth(ttl_seconds=0)

    def api_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    client = httpx.Client(transport=httpx.MockTransport(api_handler), auth=token_auth)
    client.post(API_URL)
    client.post(API_URL)

    assert len(exchange_requests) == 2


def test_service_account_key_file_reread_every_exchange(tmp_path) -> None:
    key_file = tmp_path / "sa.key"
    key_file.write_text("j1-original\n", encoding="utf-8")
    token_auth, exchange_requests = make_auth(
        ttl_seconds=0, service_account_key=None, service_account_key_file=str(key_file)
    )

    def api_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    client = httpx.Client(transport=httpx.MockTransport(api_handler), auth=token_auth)
    client.post(API_URL)
    key_file.write_text("j1-rotated\n", encoding="utf-8")  # 模擬 k8s secret 輪替
    client.post(API_URL)

    sent_keys = [json.loads(request.content)["key"] for request in exchange_requests]
    assert sent_keys == ["j1-original", "j1-rotated"]


async def test_async_flow_injects_and_retries_on_401() -> None:
    token_auth, exchange_requests = make_auth()
    api_statuses = [401, 200]
    seen_tokens: list[str] = []

    def api_handler(request: httpx.Request) -> httpx.Response:
        seen_tokens.append(request.headers[HEADER])
        return httpx.Response(api_statuses[len(seen_tokens) - 1], json={})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(api_handler), auth=token_auth
    ) as client:
        response = await client.post(API_URL, headers={"Authorization": "Bearer unused"})

    assert response.status_code == 200
    assert seen_tokens == ["j2-1", "j2-2"]
    assert len(exchange_requests) == 2


def test_missing_config_raises() -> None:
    with pytest.raises(ValueError, match="AGENT_TOKEN_EXCHANGE_URL"):
        TokenExchangeAuth(exchange_url="", header_name=HEADER, service_account_key="j1")
    with pytest.raises(ValueError, match="AGENT_TOKEN_HEADER"):
        TokenExchangeAuth(exchange_url=EXCHANGE_URL, header_name="", service_account_key="j1")
    with pytest.raises(ValueError, match="AGENT_SERVICE_ACCOUNT_KEY"):
        TokenExchangeAuth(exchange_url=EXCHANGE_URL, header_name=HEADER)


def test_bearer_mode_returns_no_clients(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_AUTH_MODE", raising=False)
    assert token_exchange_http_clients() == (None, None)


def test_token_exchange_mode_returns_shared_clients(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_AUTH_MODE", "token-exchange")
    monkeypatch.setenv("AGENT_TOKEN_EXCHANGE_URL", EXCHANGE_URL)
    monkeypatch.setenv("AGENT_TOKEN_HEADER", HEADER)
    monkeypatch.setenv("AGENT_SERVICE_ACCOUNT_KEY", "j1-key")
    monkeypatch.setattr(auth_module, "_clients", None)

    first_pair = token_exchange_http_clients()
    second_pair = token_exchange_http_clients()

    assert isinstance(first_pair[0], httpx.Client)
    assert isinstance(first_pair[1], httpx.AsyncClient)
    assert first_pair == second_pair  # process 級單例,token 快取跨請求共享
    # 兩個 client 共用同一個 auth 實例
    assert first_pair[0].auth is first_pair[1].auth
    monkeypatch.setattr(auth_module, "_clients", None)
