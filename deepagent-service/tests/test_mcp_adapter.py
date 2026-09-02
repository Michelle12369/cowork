"""MCP stateless adapter 測試——用真的本地 `fastmcp` v3 server(`http_app(stateless_http=
True)`)fixture 驗證 `load_mcp_connector`:tools/list 列舉、tools/call round-trip、每次呼叫的
可配置 SSO header(`Settings.SSO_TOKEN_HEADER`/`SSO_URL_HEADER`,入站出站同一組名稱)真的以
設定的名稱送達伺服端(自寫的 ASGI middleware 攔截,見 `_HeaderCapturingMiddleware`)、MCP 端
錯誤透傳成 `ConnectorToolError`、目錄式 `skill://{name}/{relative_path}`(`SkillsDirectoryProvider`)
逐一讀取成 `Connector.skills`(含零 skill 時的空 skill＋warning、單一 skill 讀取失敗不拖累
其他 skill、同目錄與子目錄下所有 `.md` 檔整包掛載＋非 `.md` 與 `_manifest` 略過不消費、單一
skill 的檔數/字元數上限)、缺身分時 `require_sso_token` fail loud(不送出未認證請求)、伺服端
不可達時包成可行動的 `ConnectorToolError`。

選真實 `fastmcp` server 而非陽春 ASGI stub——重點是驗證 adapter 與真實 `fastmcp` v3 client 的
stateless streamable HTTP 線路相容,陽春 stub 測不出這件事。跑在背景執行緒的隨機埠上,模組層
fixture 全測試共用一個伺服器。
"""

import asyncio
import contextlib
import json
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from fastmcp import FastMCP
from fastmcp.server.providers.skills import SkillsDirectoryProvider

from app.agent.connectors.mcp_adapter import _SKILL_FILE_COUNT_LIMIT, load_mcp_connector
from app.agent.connectors.model import Connector, ConnectorToolError
from app.config import get_settings
from app.engine.request_context import reset_request_identity, set_request_identity

_FAILING_TOOL_MESSAGE = "上游資料源逾時，請縮小查詢範圍後重試"


class _CapturedRequest:
    __slots__ = ("headers", "method_name")

    def __init__(self, method_name: str | None, headers: dict[str, str]) -> None:
        self.method_name = method_name
        self.headers = headers

    def header(self, name: str) -> str | None:
        """大小寫不敏感取值——HTTP header 名稱本就不分大小寫,測試斷言不該綁死大小寫。"""
        return self.headers.get(name.lower())


class _HeaderCapturingMiddleware:
    """包在 fastmcp streamable-http ASGI app 外層——只為了讓測試斷言可配置的 SSO token/url
    header 真的以設定的名稱送達伺服端(見 mcp_adapter.py 模組 docstring),不介入 MCP 協定
    本身;body 讀出後原樣重放給下游 app,不改變回應內容。"""

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


class _ForcedStatusMiddleware:
    """把底層 app 的每個 HTTP 回應狀態碼強制改寫成固定值——`fastmcp` client 對非預期狀態碼
    走 `httpx.raise_for_status()`,訊息裡會帶原始狀態碼文字,用來驗證診斷用的狀態碼確實
    透傳到 `ConnectorToolError` 訊息(見 `test_http_status_error_message_includes_status_code_for_diagnosis`)。"""

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


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe_socket:
        probe_socket.bind(("127.0.0.1", 0))
        return probe_socket.getsockname()[1]


def _run_server_in_thread(app: Any, port: int) -> uvicorn.Server:
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


def _write_skill(
    root: Path, name: str, *, main_body: str, supporting_files: dict[str, str] | None = None
) -> None:
    """在 `root/{name}/` 下擺一份 FastMCP v3 目錄式 skill:`SKILL.md` 主文件外加選用的
    支援檔(key 可含子目錄,例如 `references/detail.md`)——供 `SkillsDirectoryProvider`
    掃描。"""
    skill_dir = root / name
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(main_body, encoding="utf-8")
    for file_name, file_body in (supporting_files or {}).items():
        file_path = skill_dir / file_name
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(file_body, encoding="utf-8")


_SUPPORTING_MARKDOWN_BODY = "支援檔內容——.md 應被整包收進 skills map"


@pytest.fixture(scope="module")
def echo_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Any]]:
    """帶一個 echo tool、一個 structuredContent 缺席的 tool、一個 failing tool,外加兩份
    目錄式 skill(`usage`／`advanced`,驗證一個 server 供多個 skill)＋`usage` skill 另帶一份
    巢狀 `.md` 支援檔(`references/detail.md`,驗證整包掛載含子目錄)與一個非 `.md` 檔
    (`script.py`,驗證非 `.md` 一律略過)的 fixture server。"""
    skills_root = tmp_path_factory.mktemp("echo-skills")
    _write_skill(
        skills_root,
        "usage",
        main_body=(
            "---\nname: fixture-usage\ndescription: fixture usage skill。\n---\n\n"
            "# fixture skill(usage)\n\nload_mcp_connector 讀 skill://usage/SKILL.md 驗證用。"
        ),
        supporting_files={
            "references/detail.md": _SUPPORTING_MARKDOWN_BODY,
            "script.py": "print('非 .md 支援檔——不應被收進 skills map')",
        },
    )
    _write_skill(
        skills_root,
        "advanced",
        main_body=(
            "---\nname: fixture-advanced\ndescription: fixture advanced skill。\n---\n\n"
            "# fixture skill(advanced)\n\n驗證一個 connector 供多份 skill。"
        ),
    )

    mcp_server = FastMCP("fixture-echo-server")

    @mcp_server.tool()
    def echo_tool(message: str) -> dict[str, Any]:
        """回傳原樣訊息,驗證 tools/call round-trip(structuredContent 路徑)。"""
        return {"echo": message}

    @mcp_server.tool(output_schema=None)
    def text_only_echo_tool(message: str) -> str:
        """`output_schema=None`＋回傳非 dict(純字串)——server 只給 content text block、無
        structuredContent,驗證 adapter 對缺 structuredContent 的 fail-loud 路徑。"""
        return json.dumps({"echo": message})

    @mcp_server.tool()
    def failing_tool() -> dict[str, Any]:
        """恆拋錯——驗證 MCP isError 回應透傳成 ConnectorToolError。"""
        raise ValueError(_FAILING_TOOL_MESSAGE)

    # supporting_files="resources"——讓 reference.md 出現在 list_resources(),才測得出
    # adapter「支援檔略過不消費」的分支(預設 "template" 模式支援檔根本不會被列出)。
    mcp_server.add_provider(
        SkillsDirectoryProvider(roots=skills_root, supporting_files="resources")
    )

    capturing_app = _HeaderCapturingMiddleware(mcp_server.http_app(stateless_http=True))
    port = _free_port()
    server = _run_server_in_thread(capturing_app, port)

    yield {"base_url": f"http://127.0.0.1:{port}/mcp", "captured": capturing_app.captured}

    server.should_exit = True


@pytest.fixture(scope="module")
def partial_skill_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """一個 skill 正常、一個 skill 的 `SKILL.md` 在伺服器啟動後(discovery 已快取)被刪除的
    fixture server——驗證單一 skill 讀取失敗只跳過該份、不拖累其他 skill(partial success)。
    """
    skills_root = tmp_path_factory.mktemp("partial-skills")
    _write_skill(
        skills_root,
        "usage",
        main_body="---\nname: partial-skill-usage\ndescription: 可讀 skill。\n---\n\n# 可讀 skill",
    )
    _write_skill(skills_root, "broken", main_body="# 稍後會被刪除，讀取時才失敗")

    mcp_server = FastMCP("fixture-partial-skill-server")

    @mcp_server.tool()
    def noop_tool() -> dict[str, Any]:
        return {"ok": True}

    # SkillsDirectoryProvider 預設 reload=False——discovery 於 add_provider 當下即完成並
    # 快取,之後刪主文件不影響 list_resources() 的快取清單,但實際 read_resource() 會因
    # 檔案不存在而失敗,重現「resource 列表存在、讀取當下才失敗」的情境。
    mcp_server.add_provider(SkillsDirectoryProvider(roots=skills_root))
    (skills_root / "broken" / "SKILL.md").unlink()

    port = _free_port()
    server = _run_server_in_thread(mcp_server.http_app(stateless_http=True), port)

    yield f"http://127.0.0.1:{port}/mcp"

    server.should_exit = True


@pytest.fixture(scope="module")
def capped_skill_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """一份 skill 底下有 `SKILL.md` 外加 25 份支援檔(超過檔數上限 `_SKILL_FILE_COUNT_LIMIT`
    ＝20)——驗證量上限:`SKILL.md` 永遠優先保留,超出上限的支援檔略過＋warning。"""
    skills_root = tmp_path_factory.mktemp("capped-skills")
    supporting_files = {f"notes/note{index:02d}.md": f"# note {index}" for index in range(25)}
    _write_skill(
        skills_root,
        "bulky",
        main_body=(
            "---\nname: capped-bulky\ndescription: 驗證支援檔數量上限。\n---\n\n"
            "# fixture skill(bulky)\n\n驗證支援檔數量上限。"
        ),
        supporting_files=supporting_files,
    )

    mcp_server = FastMCP("fixture-capped-skill-server")

    @mcp_server.tool()
    def noop_tool() -> dict[str, Any]:
        return {"ok": True}

    mcp_server.add_provider(
        SkillsDirectoryProvider(roots=skills_root, supporting_files="resources")
    )

    port = _free_port()
    server = _run_server_in_thread(mcp_server.http_app(stateless_http=True), port)

    yield f"http://127.0.0.1:{port}/mcp"

    server.should_exit = True


@pytest.fixture(scope="module")
def unauthorized_server() -> Iterator[str]:
    """所有回應強制改寫成 401——模擬上游 gateway 拒絕請求;用來驗證 `ConnectorToolError`
    訊息帶狀態碼(見 `test_http_status_error_message_includes_status_code_for_diagnosis`)。"""
    mcp_server = FastMCP("fixture-unauthorized-server")

    @mcp_server.tool()
    def unreachable_tool() -> dict[str, Any]:
        return {"ok": True}

    app = _ForcedStatusMiddleware(mcp_server.http_app(stateless_http=True), forced_status=401)
    port = _free_port()
    server = _run_server_in_thread(app, port)

    yield f"http://127.0.0.1:{port}/mcp"

    server.should_exit = True


@pytest.fixture(scope="module")
def no_skill_server() -> Iterator[str]:
    """無任何 skill provider 的 fixture server——驗證 skill 缺席時空 skill＋warning 的分支。"""
    mcp_server = FastMCP("fixture-no-skill-server")

    @mcp_server.tool()
    def noop_tool() -> dict[str, Any]:
        return {"ok": True}

    port = _free_port()
    server = _run_server_in_thread(mcp_server.http_app(stateless_http=True), port)

    yield f"http://127.0.0.1:{port}/mcp"

    server.should_exit = True


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Iterator[None]:
    """本檔部分測試以 env var override `SSO_TOKEN_HEADER`/
    `SSO_URL_HEADER` 證明 header 名稱可配置——`get_settings()` 有
    `lru_cache`,前後都要清才不會漏到別的測試。"""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@contextlib.contextmanager
def _identity(
    sso_token: str | None = "test-bearer-token",
    sso_url: str | None = "https://sso.test.example/auth",
) -> Iterator[None]:
    tokens = set_request_identity("user-1", "session-1", sso_token, sso_url)
    try:
        yield
    finally:
        reset_request_identity(tokens)


def _tool_by_name(connector: Connector, tool_name: str):
    return next(tool for tool in connector.tools if tool.name == tool_name)


def _load(
    connector_id: str, display_name: str, base_url: str, bearer_token_key: str | None = None
) -> Connector:
    """給 sync-by-design 的測試用:這些測試接著呼叫 `ConnectorTool.call`(內部自己
    `asyncio.run()`),測試函式本身 MUST 沒有 running loop,故不能是 `async def`——
    這裡改用 `asyncio.run()` 承接 async 的 `load_mcp_connector`。"""
    return asyncio.run(load_mcp_connector(connector_id, display_name, base_url, bearer_token_key))


async def test_load_mcp_connector_enumerates_tools_with_input_schema(echo_server) -> None:
    with _identity():
        connector = await load_mcp_connector("fixture", "Fixture Server", echo_server["base_url"])

    assert connector.connector_id == "fixture"
    assert connector.display_name == "Fixture Server"
    tool_names = {tool.name for tool in connector.tools}
    assert tool_names == {"echo_tool", "text_only_echo_tool", "failing_tool"}

    echo_tool = _tool_by_name(connector, "echo_tool")
    assert echo_tool.input_schema["type"] == "object"
    assert "message" in echo_tool.input_schema["properties"]
    assert echo_tool.input_schema["required"] == ["message"]


def test_tool_call_round_trips_args_and_returns_parsed_json(echo_server) -> None:
    with _identity():
        connector = _load("fixture", "Fixture Server", echo_server["base_url"])
        echo_tool = _tool_by_name(connector, "echo_tool")
        result = echo_tool.call({"message": "hello mcp"})

    assert result == {"echo": "hello mcp"}


def test_tool_call_without_structured_content_raises_actionable_error(
    echo_server,
) -> None:
    """structuredContent-only 契約:`output_schema=None`＋非 dict 回傳值的 tool 只給 text
    block——adapter 不退回解析 text,直接以可行動訊息拒收。"""
    with _identity():
        connector = _load("fixture", "Fixture Server", echo_server["base_url"])
        text_only_tool = _tool_by_name(connector, "text_only_echo_tool")
        with pytest.raises(ConnectorToolError, match="structuredContent"):
            text_only_tool.call({"message": "hello text-only"})


def test_tool_call_sends_default_sso_token_header_with_current_token(echo_server) -> None:
    """預設 header 名稱 X-SSO-Token(`Settings.SSO_TOKEN_HEADER` 未覆寫時)。"""
    captured = echo_server["captured"]
    captured.clear()

    with _identity(sso_token="call-time-token-42"):
        connector = _load("fixture", "Fixture Server", echo_server["base_url"])
        echo_tool = _tool_by_name(connector, "echo_tool")
        echo_tool.call({"message": "check header"})

    tool_call_requests = [entry for entry in captured if entry.method_name == "tools/call"]
    assert tool_call_requests, "tools/call 請求未送達伺服端"
    assert tool_call_requests[-1].header("X-SSO-Token") == "call-time-token-42"

    # tools/list(load 階段)也要帶上呼叫當下的 token——同屬「每次呼叫」。
    list_requests = [entry for entry in captured if entry.method_name == "tools/list"]
    assert list_requests
    assert list_requests[-1].header("X-SSO-Token") == "call-time-token-42"

    # resources/read(skill 讀取,同屬 load 階段)也要帶上同一個 token。
    resource_read_requests = [entry for entry in captured if entry.method_name == "resources/read"]
    assert resource_read_requests
    assert resource_read_requests[-1].header("X-SSO-Token") == "call-time-token-42"


def test_tool_call_sends_sso_url_header_and_missing_url_fails_loud(echo_server) -> None:
    """sso_url 與 sso_token 同為必須:有值時每次呼叫都帶 url header;缺 url 時
    `require_sso_url` fail loud(LookupError),不送出任何請求。"""
    captured = echo_server["captured"]
    captured.clear()

    with _identity(sso_token="tok", sso_url="https://sso.internal.example/auth"):
        connector = _load("fixture", "Fixture Server", echo_server["base_url"])
        _tool_by_name(connector, "echo_tool").call({"message": "with url"})

    with_url_requests = [entry for entry in captured if entry.method_name == "tools/call"]
    assert with_url_requests
    assert with_url_requests[-1].header("X-SSO-Url") == "https://sso.internal.example/auth"

    captured.clear()
    with _identity(sso_token="tok", sso_url=None), pytest.raises(LookupError):
        _load("fixture", "Fixture Server", echo_server["base_url"])

    assert captured == [], "缺 sso_url 時不應該送出任何請求"


def test_tool_call_uses_configured_header_names(echo_server, monkeypatch) -> None:
    """`SSO_TOKEN_HEADER`/`SSO_URL_HEADER` 覆寫後,adapter 出站改用新名稱送出
    token/url——出站與入站共用同一組可配置名稱(internal header 名不進版控)。"""
    monkeypatch.setenv("SSO_TOKEN_HEADER", "X-Internal-Token")
    monkeypatch.setenv("SSO_URL_HEADER", "X-Internal-Url")
    get_settings.cache_clear()

    captured = echo_server["captured"]
    captured.clear()

    with _identity(sso_token="custom-header-token", sso_url="https://sso.internal.example/auth"):
        connector = _load("fixture", "Fixture Server", echo_server["base_url"])
        _tool_by_name(connector, "echo_tool").call({"message": "custom headers"})

    tool_call_requests = [entry for entry in captured if entry.method_name == "tools/call"]
    assert tool_call_requests
    request = tool_call_requests[-1]
    assert request.header("X-Internal-Token") == "custom-header-token"
    assert request.header("X-Internal-Url") == "https://sso.internal.example/auth"
    # 舊的預設名稱不該同時出現——確認是「改名」而非「兩者都送」。
    assert request.header("X-SSO-Token") is None
    assert request.header("X-SSO-Url") is None


async def test_missing_identity_raises_lookup_error_without_calling_server(echo_server) -> None:
    captured = echo_server["captured"]
    captured.clear()

    with pytest.raises(LookupError):
        await load_mcp_connector("fixture", "Fixture Server", echo_server["base_url"])

    assert captured == [], "缺身分時不應該送出任何未認證請求"


def test_server_error_raises_connector_tool_error_with_verbatim_message(echo_server) -> None:
    with _identity():
        connector = _load("fixture", "Fixture Server", echo_server["base_url"])
        failing_tool = _tool_by_name(connector, "failing_tool")

        with pytest.raises(ConnectorToolError, match=_FAILING_TOOL_MESSAGE):
            failing_tool.call({})


async def test_resources_list_loads_every_skill_md_by_directory_name(
    echo_server,
) -> None:
    """每個 `skill://{name}/SKILL.md` 都成為一個獨立 skill,name 取自目錄名——
    `usage` 慣例上仍是主 skill,但這裡不特殊處理,與 `advanced` 一視同仁。"""
    with _identity():
        connector = await load_mcp_connector("fixture", "Fixture Server", echo_server["base_url"])

    assert set(connector.skills) == {"usage", "advanced"}
    assert "fixture skill(usage)" in connector.skills["usage"]["SKILL.md"]
    assert "fixture skill(advanced)" in connector.skills["advanced"]["SKILL.md"]


async def test_supporting_markdown_is_mounted_and_non_markdown_and_manifest_are_skipped(
    echo_server, caplog
) -> None:
    """整包掛載:同目錄與子目錄下的 `.md` 支援檔(`references/detail.md`)全數收進
    `Connector.skills[name]`,與 `SKILL.md` 同構(漸進揭露);非 `.md`(`script.py`)與
    `_manifest`(`SkillsDirectoryProvider(supporting_files="resources")` 讓三者都出現在
    list_resources())一律不進 `Connector.skills`——`script.py` 略過留 debug log,
    `_manifest` 略過不留 log;`usage` 的 `SKILL.md` 仍正常讀入,不被拖累。"""
    with caplog.at_level("DEBUG"), _identity():
        connector = await load_mcp_connector("fixture", "Fixture Server", echo_server["base_url"])

    usage_files = connector.skills["usage"]
    assert "# fixture skill(usage)" in usage_files["SKILL.md"]
    assert usage_files["references/detail.md"] == _SUPPORTING_MARKDOWN_BODY
    assert all("script.py" not in file_name for file_name in usage_files)
    assert all("_manifest" not in file_name for file_name in usage_files)

    # 只看 adapter 自己的 logger——`caplog.at_level("DEBUG")` 對 root logger 生效,連
    # httpx/mcp 等第三方 debug log(內含整包 wire JSON,含 "_manifest" 字樣)都會混進來。
    debug_messages = [
        record.message
        for record in caplog.records
        if record.levelname == "DEBUG" and record.name == "app.agent.connectors.mcp_adapter"
    ]
    assert any("script.py" in message for message in debug_messages), (
        "非 .md 支援檔略過 MUST 留 debug log 供除錯"
    )
    assert not any("_manifest" in message for message in debug_messages), (
        "_manifest 略過 MUST 不留任何 log(不讀不警告)"
    )


async def test_missing_skill_resource_returns_empty_skill_and_warns(
    no_skill_server, caplog
) -> None:
    with caplog.at_level("WARNING"), _identity():
        connector = await load_mcp_connector("no-skill", "No Skill Server", no_skill_server)

    assert connector.skills == {}
    assert any("skill" in record.message.lower() for record in caplog.records)


async def test_one_skill_resource_read_failure_does_not_block_other_skills(
    partial_skill_server, caplog
) -> None:
    with caplog.at_level("WARNING"), _identity():
        connector = await load_mcp_connector(
            "partial-skill", "Partial Skill Server", partial_skill_server
        )

    assert set(connector.skills) == {"usage"}
    assert "# 可讀 skill" in connector.skills["usage"]["SKILL.md"]
    assert any(
        "broken" in record.message and "partial-skill" in record.message
        for record in caplog.records
    )


async def test_supporting_file_count_over_limit_keeps_skill_md_and_warns(
    capped_skill_server, caplog
) -> None:
    """25 份支援檔超過檔數上限(`_SKILL_FILE_COUNT_LIMIT`＝20,含 `SKILL.md`)——`SKILL.md`
    永遠優先保留,超出上限的支援檔略過＋warning,不中止其他 skill/檔案的讀取。"""
    with caplog.at_level("WARNING"), _identity():
        connector = await load_mcp_connector("capped", "Capped Skill Server", capped_skill_server)

    bulky_files = connector.skills["bulky"]
    assert "# fixture skill(bulky)" in bulky_files["SKILL.md"]
    assert len(bulky_files) <= _SKILL_FILE_COUNT_LIMIT
    assert any("bulky" in record.message and "limit" in record.message for record in caplog.records)


async def test_unreachable_server_raises_connector_tool_error_without_leaking_token() -> None:
    with (
        _identity(sso_token="must-not-leak-token"),
        pytest.raises(ConnectorToolError) as error_info,
    ):
        await load_mcp_connector("unreachable", "Unreachable Server", "http://127.0.0.1:1/mcp")

    assert "must-not-leak-token" not in str(error_info.value)


async def test_unreachable_server_error_message_is_actionable() -> None:
    with (
        _identity(),
        pytest.raises(ConnectorToolError, match="tools/list|連線|連接|unreachable|MCP"),
    ):
        await load_mcp_connector("unreachable", "Unreachable Server", "http://127.0.0.1:1/mcp")


def test_bearer_token_key_declared_sends_authorization_header_on_every_call(
    echo_server, monkeypatch
) -> None:
    """`bearer_token_key` 宣告且 `CONNECTOR_BEARER_TOKENS` 查有值時,tools/list 與
    tools/call 出站 headers 都要帶 `Authorization: Bearer <值>`——key 與 connector_id 刻意
    取不同名字,證明查表用的是宣告的 key,不是 connector_id。"""
    monkeypatch.setenv("CONNECTOR_BEARER_TOKENS", '{"shared-token-key": "service-token-value"}')
    get_settings.cache_clear()

    captured = echo_server["captured"]
    captured.clear()

    with _identity():
        connector = _load(
            "fixture",
            "Fixture Server",
            echo_server["base_url"],
            bearer_token_key="shared-token-key",
        )
        echo_tool = _tool_by_name(connector, "echo_tool")
        echo_tool.call({"message": "with bearer"})

    for method_name in ("tools/list", "tools/call"):
        requests = [entry for entry in captured if entry.method_name == method_name]
        assert requests, f"{method_name} 請求未送達伺服端"
        assert requests[-1].header("Authorization") == "Bearer service-token-value"


def test_bearer_token_key_not_declared_omits_authorization_header_even_with_residual_dict_value(
    echo_server, monkeypatch
) -> None:
    """`bearer_token_key` 為 `None`(catalog 未宣告)＝此 connector 不需認證,headers 不含
    Authorization——即使 `CONNECTOR_BEARER_TOKENS` 裡有其他 key 的殘留值,也不會被誤用。"""
    monkeypatch.setenv("CONNECTOR_BEARER_TOKENS", '{"some-other-key": "residual-value"}')
    get_settings.cache_clear()

    captured = echo_server["captured"]
    captured.clear()

    with _identity():
        connector = _load("fixture", "Fixture Server", echo_server["base_url"])
        echo_tool = _tool_by_name(connector, "echo_tool")
        echo_tool.call({"message": "without bearer"})

    for method_name in ("tools/list", "tools/call"):
        requests = [entry for entry in captured if entry.method_name == method_name]
        assert requests
        assert requests[-1].header("Authorization") is None


def test_bearer_token_key_declared_but_missing_from_dict_raises_fail_loud(
    echo_server, monkeypatch
) -> None:
    """`bearer_token_key` 宣告了,但 `CONNECTOR_BEARER_TOKENS` 查無此 key(或值為空)——
    配置不完整,載入時直接 fail loud(`ConnectorToolError`,訊息含 connector id 與 key 名、
    NEVER 含任何殘留 token 值),不是靜默省略 header。"""
    monkeypatch.setenv("CONNECTOR_BEARER_TOKENS", '{"some-other-key": "must-not-leak-value"}')
    get_settings.cache_clear()

    captured = echo_server["captured"]
    captured.clear()

    with _identity(), pytest.raises(ConnectorToolError) as error_info:
        _load("fixture", "Fixture Server", echo_server["base_url"], bearer_token_key="ghost-key")

    message = str(error_info.value)
    assert "fixture" in message
    assert "ghost-key" in message
    assert "must-not-leak-value" not in message
    assert captured == [], "key 查無值時不應該送出任何請求"


async def test_http_status_error_message_includes_status_code_for_diagnosis(
    unauthorized_server,
) -> None:
    """訊息 MUST 帶狀態碼,401 才分辨得出跟 500/timeout 不同。用 `_ForcedStatusMiddleware`
    把每個回應狀態碼強制改成 401(模擬上游 gateway 拒絕請求)——`fastmcp` client 對非預期
    狀態碼走 `httpx.raise_for_status()`,訊息裡帶原始狀態碼文字。"""
    with (
        _identity(sso_token="must-not-leak-401-token"),
        pytest.raises(ConnectorToolError, match="401") as error_info,
    ):
        await load_mcp_connector("fixture", "Fixture Server", unauthorized_server)

    assert "must-not-leak-401-token" not in str(error_info.value)
