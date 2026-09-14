"""給多個測試檔共用的 MCP fixture server 工具:隨機空 port、背景執行緒啟動 uvicorn、以及
會改寫回應狀態碼／攔截 header／計數請求的 ASGI middleware——原本各自散在測試檔裡,抽出來
避免 test_mcp_adapter.py 與 test_tool_call_endpoint.py 重複實作。"""

import json
import socket
import threading
import time
from typing import Any

import uvicorn


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe_socket:
        probe_socket.bind(("127.0.0.1", 0))
        return probe_socket.getsockname()[1]


def run_server_in_thread(app: Any, port: int) -> uvicorn.Server:
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if getattr(server, "started", False):
            return server
        time.sleep(0.02)
    raise RuntimeError("fixture uvicorn server 未在時限內就緒")


class ForcedStatusMiddleware:
    """把底層 app 的每個 HTTP 回應狀態碼強制改寫成固定值——`fastmcp` client 對非預期狀態碼
    走 `httpx.raise_for_status()`,訊息裡會帶原始狀態碼文字,用來驗證診斷用的狀態碼確實
    透傳到 `ConnectorToolError` 訊息。"""

    def __init__(self, app: Any, forced_status: int) -> None:
        self._app = app
        self._forced_status = forced_status

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        async def send_wrapper(message: dict) -> None:
            if message["type"] == "http.response.start":
                message["status"] = self._forced_status
            await send(message)

        await self._app(scope, receive, send_wrapper)


class ForcedStatusOnMethodMiddleware:
    """跟 ForcedStatusMiddleware 一樣改寫回應狀態碼, 但只在 JSON-RPC `method` 等於
    `method_name` 的請求上生效——其餘請求(例如 session initialize)原樣通過, 用來驗證
    「錯誤發生在 tools/call 那個 POST 本身」而不是連線階段就先失敗。"""

    def __init__(self, app: Any, method_name: str, forced_status: int) -> None:
        self._app = app
        self._method_name = method_name
        self._forced_status = forced_status

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        body_chunks: list[bytes] = []
        more_body = True
        while more_body:
            message = await receive()
            body_chunks.append(message.get("body", b""))
            more_body = message.get("more_body", False)
        body = b"".join(body_chunks)

        method_name = None
        try:
            payload = json.loads(body or b"{}")
            method_name = payload.get("method")
        except json.JSONDecodeError:
            method_name = None

        replayed = False

        async def _replay_receive() -> dict:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        if method_name != self._method_name:
            await self._app(scope, _replay_receive, send)
            return

        async def send_wrapper(message: dict) -> None:
            if message["type"] == "http.response.start":
                message["status"] = self._forced_status
            await send(message)

        await self._app(scope, _replay_receive, send_wrapper)


class _CapturedRequest:
    __slots__ = ("headers", "method_name")

    def __init__(self, method_name: str | None, headers: dict[str, str]) -> None:
        self.method_name = method_name
        self.headers = headers

    def header(self, name: str) -> str | None:
        """大小寫不敏感取值——HTTP header 名稱本就不分大小寫,測試斷言不該綁死大小寫。"""
        return self.headers.get(name.lower())


class HeaderCapturingMiddleware:
    """包在 fastmcp streamable-http ASGI app 外層——只為了讓測試斷言可配置的 SSO token/url
    header 真的以設定的名稱送達伺服端,不介入 MCP 協定本身;body 讀出後原樣重放給下游 app,
    不改變回應內容。"""

    def __init__(self, app: Any) -> None:
        self._app = app
        self.captured: list[_CapturedRequest] = []

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        raw_headers = dict(scope.get("headers") or [])
        headers = {name.decode().lower(): value.decode() for name, value in raw_headers.items()}

        body_chunks: list[bytes] = []
        more_body = True
        while more_body:
            message = await receive()
            body_chunks.append(message.get("body", b""))
            more_body = message.get("more_body", False)
        body = b"".join(body_chunks)

        method_name = None
        try:
            payload = json.loads(body or b"{}")
            method_name = payload.get("method")
        except json.JSONDecodeError:
            method_name = None

        self.captured.append(_CapturedRequest(method_name, headers))

        replayed = False

        async def _replay_receive() -> dict:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            # 已重放完 body——後續 receive() 呼叫(例如串流回應期間的 client 斷線偵測)轉發
            # 給真正的底層 channel,不可合成 http.disconnect(會被誤判成 client 真的斷線,
            # 讓伺服端提早砍斷還在寫的 SSE 回應)。
            return await receive()

        await self._app(scope, _replay_receive, send)


class RequestCountingMiddleware:
    """計數每個 JSON-RPC method 收到的 HTTP 請求次數——用來斷言像「tools/list 完全沒被
    呼叫」這種請求次數不變式;body 讀出後原樣重放給下游 app,不改變回應內容。"""

    def __init__(self, app: Any) -> None:
        self._app = app
        self.counts: dict[str, int] = {}

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        body_chunks: list[bytes] = []
        more_body = True
        while more_body:
            message = await receive()
            body_chunks.append(message.get("body", b""))
            more_body = message.get("more_body", False)
        body = b"".join(body_chunks)

        method_name = None
        try:
            payload = json.loads(body or b"{}")
            method_name = payload.get("method")
        except json.JSONDecodeError:
            method_name = None

        if method_name is not None:
            self.counts[method_name] = self.counts.get(method_name, 0) + 1

        replayed = False

        async def _replay_receive() -> dict:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self._app(scope, _replay_receive, send)
