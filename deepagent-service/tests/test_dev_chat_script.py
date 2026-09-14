"""scripts/dev_chat.py 純函式的行為測試: connector 參數解析、合併、header 組裝、preflight、
main() 的 connector 解析與 exit code。"""

import contextlib
import http.server
import importlib.util
import json
import socket
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app.config import get_settings

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "dev_chat.py"
spec = importlib.util.spec_from_file_location("dev_chat", SCRIPT_PATH)
assert spec is not None and spec.loader is not None
dev_chat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dev_chat)


class _Recorder:
    """記錄每個 POST 請求的 path 與解析過的 JSON body, 供斷言送出的 payload 用."""

    def __init__(self) -> None:
        self.posts: list[dict[str, Any]] = []


def _make_handler(routes: dict[str, tuple[int, bytes]], recorder: _Recorder | None) -> type:
    """`routes`: path -> (status_code, body bytes)。未列的 path 一律 404。"""

    class _StubHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format_string: str, *args: Any) -> None:
            pass  # 測試不需要 http.server 預設印到 stderr 的 access log

        def _respond(self) -> None:
            status_code, body = routes.get(self.path, (404, b""))
            self.send_response(status_code)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def do_GET(self) -> None:
            self._respond()

        def do_POST(self) -> None:
            if recorder is not None:
                content_length = int(self.headers.get("Content-Length", 0))
                body_bytes = self.rfile.read(content_length)
                recorder.posts.append({"path": self.path, "json": json.loads(body_bytes)})
            self._respond()

    return _StubHandler


def _reserve_closed_port() -> int:
    """回傳一個當下沒人在聽的 port——綁定後立刻關閉, 讓後續連線得到 connection refused."""
    probe_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe_socket.bind(("127.0.0.1", 0))
    port = probe_socket.getsockname()[1]
    probe_socket.close()
    return port


@contextlib.contextmanager
def _run_stub_server(
    routes: dict[str, tuple[int, bytes]], *, recorder: _Recorder | None = None
) -> Iterator[str]:
    """啟動一個 threading HTTP stub server, yield 它的 base URL, 結束時關掉。"""
    handler_class = _make_handler(routes, recorder)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_class)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)


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


def test_resolve_new_session_connectors_useConfigTrue_mergesConfigAndCliCliWins() -> None:
    config_connectors = [
        {"id": "sales", "name": "Sales", "url": "http://config/mcp", "bearerTokenKey": None},
        {"id": "crm", "name": "CRM", "url": "http://config-crm/mcp", "bearerTokenKey": None},
    ]
    cli_connectors = [
        {"id": "sales", "name": "Sales", "url": "http://cli/mcp", "bearerTokenKey": None},
    ]

    resolved = dev_chat.resolve_new_session_connectors(
        config_connectors, cli_connectors, use_config_connectors=True
    )

    assert [connector["id"] for connector in resolved] == ["sales", "crm"]
    assert resolved[0]["url"] == "http://cli/mcp"


def test_resolve_new_session_connectors_noConnectorsFlag_dropsConfigConnectors() -> None:
    config_connectors = [
        {"id": "sales", "name": "Sales", "url": "http://config/mcp", "bearerTokenKey": None},
    ]
    cli_connectors = [
        {"id": "crm", "name": "CRM", "url": "http://cli/mcp", "bearerTokenKey": None},
    ]

    resolved = dev_chat.resolve_new_session_connectors(
        config_connectors, cli_connectors, use_config_connectors=False
    )

    assert resolved == cli_connectors


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


def test_preflight_healthUnreachable_exitsNonZero() -> None:
    dead_port = _reserve_closed_port()
    with pytest.raises(SystemExit) as excinfo:
        dev_chat._preflight(f"http://127.0.0.1:{dead_port}", [])
    assert excinfo.value.code


def test_preflight_healthNon200_exitsNonZero() -> None:
    with (
        _run_stub_server({"/health": (500, b"")}) as base_url,
        pytest.raises(SystemExit) as excinfo,
    ):
        dev_chat._preflight(base_url, [])
    assert excinfo.value.code


def test_preflight_connectorUnreachable_exitsNonZero() -> None:
    dead_port = _reserve_closed_port()
    with _run_stub_server({"/health": (200, b"")}) as base_url:
        connectors = [
            {
                "id": "sales",
                "name": "Sales",
                "url": f"http://127.0.0.1:{dead_port}/mcp",
                "bearerTokenKey": None,
            }
        ]
        with pytest.raises(SystemExit) as excinfo:
            dev_chat._preflight(base_url, connectors)
    assert excinfo.value.code


def test_preflight_connectorAnswersAnyHttpStatus_passes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with _run_stub_server({"/health": (200, b""), "/mcp": (404, b"")}) as base_url:
        connectors = [
            {"id": "sales", "name": "Sales", "url": f"{base_url}/mcp", "bearerTokenKey": None}
        ]
        dev_chat._preflight(base_url, connectors)  # 404 也算連得上, 不該拋

    printed = capsys.readouterr().out
    assert "connector sales" in printed


def test_main_dashboardOutGiven_answerWithoutDashboardHtml_exitsCodeTwo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ONE_PROPERTIES_PATH", str(tmp_path / "one-local.properties"))
    monkeypatch.setenv("AGENT_API_BEARER_TOKEN", "test-token")
    get_settings.cache_clear()

    csv_path = tmp_path / "sample.csv"
    csv_path.write_text("a,b\n1,2\n", encoding="utf-8")
    chat_sse_body = b'data: {"type": "ANSWER", "text": "no dashboard this turn"}\n\n'

    try:
        with _run_stub_server({"/health": (200, b""), "/chat": (200, chat_sse_body)}) as base_url:
            argv = [
                "dev_chat.py",
                "--new",
                "--csv",
                str(csv_path),
                "--base-url",
                base_url,
                "--state-dir",
                str(tmp_path / "state"),
                "--dashboard-out",
                str(tmp_path / "dashboard-out.html"),
                "hello",
            ]
            monkeypatch.setattr(sys, "argv", argv)
            with pytest.raises(SystemExit) as excinfo:
                dev_chat.main()
        assert excinfo.value.code == 2
    finally:
        get_settings.cache_clear()


def test_main_continueTurn_keepsStoredConnectorsIgnoringLargerDevConnectors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ONE_PROPERTIES_PATH", str(tmp_path / "one-local.properties"))
    monkeypatch.setenv("AGENT_API_BEARER_TOKEN", "test-token")
    get_settings.cache_clear()

    recorder = _Recorder()
    chat_sse_body = b'data: {"type": "ANSWER", "text": "ok"}\n\n'

    try:
        with _run_stub_server(
            {"/health": (200, b""), "/mcp": (200, b""), "/chat": (200, chat_sse_body)},
            recorder=recorder,
        ) as base_url:
            state_dir = tmp_path / "state"
            state_dir.mkdir()
            stored_connectors = [
                {"id": "sales", "name": "Sales", "url": f"{base_url}/mcp", "bearerTokenKey": None}
            ]
            (state_dir / "state.json").write_text(
                json.dumps(
                    {
                        "sessionId": "dev-existing",
                        "userId": "dev-user",
                        "sources": [],
                        "connectors": stored_connectors,
                        "history": [],
                    }
                ),
                encoding="utf-8",
            )
            # DEV_CONNECTORS 現在列了更多台 —— 續接輪不該重新套用它, 存量 connectors 該原封不動.
            # DEV_* 不讀 env, 寫進 ONE_PROPERTIES_PATH 指到的檔案才會被 load_dev_config() 看到.
            (tmp_path / "one-local.properties").write_text(
                "DEV_CONNECTORS="
                + json.dumps(
                    [
                        {"id": "sales", "url": f"{base_url}/mcp"},
                        {"id": "crm", "url": f"{base_url}/mcp"},
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            argv = [
                "dev_chat.py",
                "--base-url",
                base_url,
                "--state-dir",
                str(state_dir),
                "second message",
            ]
            monkeypatch.setattr(sys, "argv", argv)
            dev_chat.main()

        assert len(recorder.posts) == 1
        posted_connector_ids = [
            connector["id"] for connector in recorder.posts[0]["json"]["connectors"]
        ]
        assert posted_connector_ids == ["sales"]
    finally:
        get_settings.cache_clear()


def test_main_noConnectorsFlag_newSession_ignoresDevConnectors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ONE_PROPERTIES_PATH", str(tmp_path / "one-local.properties"))
    monkeypatch.setenv("AGENT_API_BEARER_TOKEN", "test-token")
    # 一個打不通的位址: --no-connectors 若真的擋掉 DEV_CONNECTORS, preflight 就不會去碰它.
    # DEV_* 不讀 env, 寫進 ONE_PROPERTIES_PATH 指到的檔案才會被 load_dev_config() 看到.
    (tmp_path / "one-local.properties").write_text(
        "DEV_CONNECTORS="
        + json.dumps([{"id": "sales", "url": f"http://127.0.0.1:{_reserve_closed_port()}/mcp"}])
        + "\n",
        encoding="utf-8",
    )
    get_settings.cache_clear()

    recorder = _Recorder()
    chat_sse_body = b'data: {"type": "ANSWER", "text": "ok"}\n\n'

    try:
        with _run_stub_server(
            {"/health": (200, b""), "/chat": (200, chat_sse_body)}, recorder=recorder
        ) as base_url:
            csv_path = tmp_path / "sample.csv"
            csv_path.write_text("a,b\n1,2\n", encoding="utf-8")
            argv = [
                "dev_chat.py",
                "--new",
                "--no-connectors",
                "--csv",
                str(csv_path),
                "--base-url",
                base_url,
                "--state-dir",
                str(tmp_path / "state"),
                "hello",
            ]
            monkeypatch.setattr(sys, "argv", argv)
            dev_chat.main()

        assert recorder.posts[0]["json"]["connectors"] == []
    finally:
        get_settings.cache_clear()


def test_resolve_option_cliGiven_isCliSource_evenWhenEmpty() -> None:
    assert dev_chat.resolve_option("http://cli", "http://file", "properties") == (
        dev_chat.ResolvedOption("http://cli", "cli")
    )
    assert dev_chat.resolve_option("", "http://file", "properties").source == "cli"


def test_resolve_option_cliMissing_usesFallbackValueAndSource() -> None:
    assert dev_chat.resolve_option(None, "http://file", "properties") == (
        dev_chat.ResolvedOption("http://file", "properties")
    )


def test_collect_config_source_rows_secretsShowSourceOnly_neverValues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    properties_file = tmp_path / "one-local.properties"
    properties_file.write_text("SSO_TOKEN_HEADER=X-File-Token\n", encoding="utf-8")
    monkeypatch.setenv("ONE_PROPERTIES_PATH", str(properties_file))
    get_settings.cache_clear()
    try:
        rows = dev_chat.collect_config_source_rows(
            base_url=dev_chat.ResolvedOption("http://127.0.0.1:8000", "default"),
            token=dev_chat.ResolvedOption("sk-BEARER-SECRET", "env"),
            sso_token=dev_chat.ResolvedOption("sso-SECRET", "cli"),
            sso_url=dev_chat.ResolvedOption(None, "default"),
            config_connectors=[
                {
                    "id": "sales",
                    "name": "Sales",
                    "url": "http://x/mcp?key=URLSECRET",
                    "bearerTokenKey": None,
                }
            ],
            cli_connectors=[],
            settings=get_settings(),
        )
    finally:
        get_settings.cache_clear()

    rows_by_label = {row.label: row for row in rows}
    rendered = "\n".join(f"{row.label} {row.source} {row.shown_value}" for row in rows)
    assert "sk-BEARER-SECRET" not in rendered
    assert "sso-SECRET" not in rendered
    assert "URLSECRET" not in rendered
    assert rows_by_label["--token (AGENT_API_BEARER_TOKEN)"].source == "env"
    assert rows_by_label["--token (AGENT_API_BEARER_TOKEN)"].shown_value == dev_chat.HIDDEN_VALUE
    assert rows_by_label["--sso-token (DEV_SSO_TOKEN)"].source == "cli"
    assert rows_by_label["--sso-url (DEV_SSO_URL)"].shown_value == dev_chat.UNSET_VALUE
    assert rows_by_label["DEV_CONNECTORS"].shown_value == "ids: sales"
    assert rows_by_label["--connector"].shown_value == dev_chat.NO_CONNECTORS_VALUE
    assert rows_by_label["SSO_TOKEN_HEADER"].source == "properties"
    assert rows_by_label["SSO_TOKEN_HEADER"].shown_value == "X-File-Token"
    assert rows_by_label["ONE_PROPERTIES_PATH"].source == "env"
    assert rows_by_label["ONE_PROPERTIES_PATH"].shown_value == f"{properties_file} (exists)"


def test_main_verbose_printsSourcesAndNeverTheToken(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    properties_file = tmp_path / "one-local.properties"
    properties_file.write_text("DEV_DEEPAGENT_URL=http://127.0.0.1:1\n", encoding="utf-8")
    monkeypatch.setenv("ONE_PROPERTIES_PATH", str(properties_file))
    monkeypatch.setenv("AGENT_API_BEARER_TOKEN", "test-token-SECRET")
    get_settings.cache_clear()

    csv_path = tmp_path / "sample.csv"
    csv_path.write_text("a,b\n1,2\n", encoding="utf-8")
    chat_sse_body = b'data: {"type": "ANSWER", "text": "ok"}\n\n'

    try:
        with _run_stub_server({"/health": (200, b""), "/chat": (200, chat_sse_body)}) as base_url:
            argv = [
                "dev_chat.py",
                "--new",
                "--verbose",
                "--csv",
                str(csv_path),
                "--base-url",
                base_url,  # CLI 蓋掉檔案裡連不上的 DEV_DEEPAGENT_URL
                "--state-dir",
                str(tmp_path / "state"),
                "hello",
            ]
            monkeypatch.setattr(sys, "argv", argv)
            dev_chat.main()
    finally:
        get_settings.cache_clear()

    printed = capsys.readouterr().out
    assert "test-token-SECRET" not in printed
    verbose_lines = [line for line in printed.splitlines() if line.startswith("   ")]
    assert any(
        line.startswith("   --base-url (DEV_DEEPAGENT_URL)") and " cli " in line
        for line in verbose_lines
    )
    assert any(
        line.startswith("   --token (AGENT_API_BEARER_TOKEN)")
        and " env " in line
        and line.endswith(dev_chat.HIDDEN_VALUE)
        for line in verbose_lines
    )
    assert any(
        line.startswith("   ONE_PROPERTIES_PATH") and line.endswith(f"{properties_file} (exists)")
        for line in verbose_lines
    )


def test_main_verbose_missingToken_stillPrintsSourcesBeforeExit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """缺 token 正是最需要知道讀了哪個檔的時候: --verbose 表要印在缺 token 的離開之前."""
    monkeypatch.setenv("ONE_PROPERTIES_PATH", str(tmp_path / "missing.properties"))
    monkeypatch.delenv("AGENT_API_BEARER_TOKEN", raising=False)
    get_settings.cache_clear()
    try:
        monkeypatch.setattr(sys, "argv", ["dev_chat.py", "--verbose", "hello"])
        with pytest.raises(SystemExit) as excinfo:
            dev_chat.main()
    finally:
        get_settings.cache_clear()

    assert "AGENT_API_BEARER_TOKEN" in str(excinfo.value.code)
    printed = capsys.readouterr().out
    assert "--token (AGENT_API_BEARER_TOKEN)" in printed
    assert f"{tmp_path / 'missing.properties'} (missing)" in printed
