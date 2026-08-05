"""j1→j2 token exchange 認證(公司環境)——鏡像 Java TokenExchangeClient 語意:
POST exchange url `{"key": j1}` → `{"token": j2}`;j2 放自訂 header(raw token,無 Bearer
前綴)、TTL 快取、401 時 invalidate 換新 token 重試一次;j1 每次重讀 key file(k8s secret
輪替免重啟)。"""

import threading
import time
from pathlib import Path

import httpx

from app.config import get_settings

_EXCHANGE_TIMEOUT_SECONDS = 10.0
# 自帶 http client 時 openai SDK 不再套自己的預設 timeout,必須在這裡給。
_LLM_TIMEOUT = httpx.Timeout(600.0, connect=10.0)


class TokenExchangeAuth(httpx.Auth):
    """httpx Auth:每個請求注入快取的 j2 token;收到 401 時換新 token 重試一次。sync/async
    雙流實作(langchain astream_events 走 async client)。token 快取 last-write-wins,
    不做交換去重——與 Java 端相同的取捨(併發下最多多換一次)。
    """

    def __init__(
        self,
        exchange_url: str,
        header_name: str,
        ttl_seconds: int = 300,
        service_account_key: str | None = None,
        service_account_key_file: str | None = None,
        exchange_transport: httpx.BaseTransport | None = None,
        async_exchange_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not exchange_url:
            raise ValueError("token exchange url is required (AGENT_TOKEN_EXCHANGE_URL)")
        if not header_name:
            raise ValueError("token exchange header name is required (AGENT_TOKEN_HEADER)")
        if not service_account_key and not service_account_key_file:
            raise ValueError(
                "token exchange requires AGENT_SERVICE_ACCOUNT_KEY or"
                " AGENT_SERVICE_ACCOUNT_KEY_FILE"
            )
        self._exchange_url = exchange_url
        self._header_name = header_name
        self._ttl_seconds = ttl_seconds
        self._service_account_key = service_account_key
        self._service_account_key_file = service_account_key_file
        self._exchange_transport = exchange_transport
        self._async_exchange_transport = async_exchange_transport
        self._cache_lock = threading.Lock()
        self._token: str | None = None
        self._expires_at = 0.0

    def invalidate(self) -> None:
        with self._cache_lock:
            self._token = None

    def _cached_token(self) -> str | None:
        with self._cache_lock:
            if self._token is not None and time.monotonic() < self._expires_at:
                return self._token
        return None

    def _store_token(self, token: str) -> None:
        with self._cache_lock:
            self._token = token
            self._expires_at = time.monotonic() + self._ttl_seconds

    def _resolve_service_account_key(self) -> str:
        # file 優先且每次重讀——k8s secret 輪替後不需重啟即生效。
        if self._service_account_key_file:
            return Path(self._service_account_key_file).read_text(encoding="utf-8").strip()
        return self._service_account_key or ""

    def _extract_token(self, response: httpx.Response) -> str:
        response.raise_for_status()
        token = response.json().get("token", "")
        if not token:
            raise ValueError("token exchange response has no 'token' field")
        self._store_token(token)
        return token

    def _exchange_sync(self) -> str:
        with httpx.Client(
            transport=self._exchange_transport, timeout=_EXCHANGE_TIMEOUT_SECONDS
        ) as exchange_client:
            response = exchange_client.post(
                self._exchange_url, json={"key": self._resolve_service_account_key()}
            )
        return self._extract_token(response)

    async def _exchange_async(self) -> str:
        async with httpx.AsyncClient(
            transport=self._async_exchange_transport, timeout=_EXCHANGE_TIMEOUT_SECONDS
        ) as exchange_client:
            response = await exchange_client.post(
                self._exchange_url, json={"key": self._resolve_service_account_key()}
            )
        return self._extract_token(response)

    def _apply(self, request: httpx.Request, token: str) -> None:
        # 鏡像 Java:token-exchange 模式只送自訂 header(raw token),不送 Bearer。
        request.headers.pop("Authorization", None)
        request.headers[self._header_name] = token

    def sync_auth_flow(self, request: httpx.Request):
        token = self._cached_token() or self._exchange_sync()
        self._apply(request, token)
        response = yield request
        if response.status_code == 401:
            self.invalidate()
            self._apply(request, self._exchange_sync())
            yield request

    async def async_auth_flow(self, request: httpx.Request):
        token = self._cached_token() or await self._exchange_async()
        self._apply(request, token)
        response = yield request
        if response.status_code == 401:
            self.invalidate()
            self._apply(request, await self._exchange_async())
            yield request


def auth_mode() -> str:
    return get_settings().AGENT_AUTH_MODE.strip() or "bearer"


def _build_auth_from_env() -> TokenExchangeAuth:
    settings = get_settings()
    return TokenExchangeAuth(
        exchange_url=settings.AGENT_TOKEN_EXCHANGE_URL.strip(),
        header_name=settings.AGENT_TOKEN_HEADER,
        ttl_seconds=settings.AGENT_TOKEN_TTL,
        service_account_key=settings.AGENT_SERVICE_ACCOUNT_KEY or None,
        service_account_key_file=settings.AGENT_SERVICE_ACCOUNT_KEY_FILE or None,
    )


_clients_lock = threading.Lock()
_clients: tuple[httpx.Client, httpx.AsyncClient] | None = None


def token_exchange_http_clients() -> tuple[httpx.Client | None, httpx.AsyncClient | None]:
    """auth-mode=token-exchange 時回傳 process 級共用的 (sync, async) client——兩者共享同一個
    TokenExchangeAuth,token 快取因此跨請求生效;bearer 模式回 (None, None) 交給 SDK 預設。"""
    if auth_mode() != "token-exchange":
        return None, None
    global _clients
    with _clients_lock:
        if _clients is None:
            shared_auth = _build_auth_from_env()
            _clients = (
                httpx.Client(auth=shared_auth, timeout=_LLM_TIMEOUT),
                httpx.AsyncClient(auth=shared_auth, timeout=_LLM_TIMEOUT),
            )
    return _clients
