"""mcp_adapter 重試層測試, 涵蓋 _run_with_retry. monkeypatch 模組層的 Client 驗證逾時值,
重試次數, 失敗時的可觀測性 log, 以及 skill 讀取整組重試的語意."""

import asyncio
import typing
from typing import ClassVar, Self

import httpx
import pytest

from app.agent.connectors import mcp_adapter
from app.agent.connectors.model import ConnectorToolError
from app.config import get_settings
from app.engine.request_context import reset_request_identity, set_request_identity


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class _FakeClient:
    """假的 fastmcp Client, 記錄建構時收到的 timeout 與嘗試次數, aenter 依序回傳
    成功或拋出設定好的例外. configure 在每個測試開頭重設狀態, 避免互相汙染."""

    enter_outcomes: ClassVar[list[object]] = []
    enter_count: ClassVar[int] = 0
    received_timeouts: ClassVar[list[float]] = []

    def __init__(self, transport: object, timeout: float) -> None:
        type(self).received_timeouts.append(timeout)

    async def __aenter__(self) -> Self:
        type(self).enter_count += 1
        outcome = type(self).enter_outcomes[type(self).enter_count - 1]
        if isinstance(outcome, BaseException):
            raise outcome
        return self

    async def __aexit__(self, exc_type: object, exc_value: object, traceback: object) -> bool:
        return False

    @classmethod
    def configure(cls, outcomes: list[object]) -> None:
        cls.enter_outcomes = outcomes
        cls.enter_count = 0
        cls.received_timeouts = []


async def _return_ok(client: object) -> str:
    return "ok"


def test_call_uses_configured_request_timeout(monkeypatch):
    monkeypatch.setenv("CONNECTOR_REQUEST_TIMEOUT_SECONDS", "1.5")
    get_settings.cache_clear()

    _FakeClient.configure(["success"])
    monkeypatch.setattr(mcp_adapter, "Client", _FakeClient)

    result = asyncio.run(
        mcp_adapter._call("fixture", "http://example.invalid/mcp", "tools/list", {}, _return_ok)
    )

    assert result == "ok"
    assert _FakeClient.received_timeouts == [1.5]


def test_transient_failure_then_success_retries_once(monkeypatch):
    _FakeClient.configure([httpx.ConnectError("boom"), "success"])
    monkeypatch.setattr(mcp_adapter, "Client", _FakeClient)

    result = asyncio.run(
        mcp_adapter._call("fixture", "http://example.invalid/mcp", "tools/list", {}, _return_ok)
    )

    assert result == "ok"
    assert _FakeClient.enter_count == 2


def test_retries_exhausted_raises_connector_tool_error_naming_method(monkeypatch):
    monkeypatch.setenv("CONNECTOR_CALL_RETRIES", "2")
    get_settings.cache_clear()

    _FakeClient.configure([httpx.ReadTimeout("slow")] * 3)
    monkeypatch.setattr(mcp_adapter, "Client", _FakeClient)

    with pytest.raises(ConnectorToolError, match="tools/call") as error_info:
        asyncio.run(
            mcp_adapter._call("fixture", "http://example.invalid/mcp", "tools/call", {}, _return_ok)
        )

    assert _FakeClient.enter_count == 3
    assert "ReadTimeout" in str(error_info.value)


def test_any_exception_is_retried_once(monkeypatch):
    _FakeClient.configure([ValueError("bad request"), "success"])
    monkeypatch.setattr(mcp_adapter, "Client", _FakeClient)

    result = asyncio.run(
        mcp_adapter._call("fixture", "http://example.invalid/mcp", "tools/list", {}, _return_ok)
    )

    assert result == "ok"
    assert _FakeClient.enter_count == 2


def test_zero_retries_setting_attempts_only_once_on_transient_failure(monkeypatch):
    monkeypatch.setenv("CONNECTOR_CALL_RETRIES", "0")
    get_settings.cache_clear()

    _FakeClient.configure([httpx.ConnectError("boom")])
    monkeypatch.setattr(mcp_adapter, "Client", _FakeClient)

    with pytest.raises(ConnectorToolError):
        asyncio.run(
            mcp_adapter._call("fixture", "http://example.invalid/mcp", "tools/list", {}, _return_ok)
        )

    assert _FakeClient.enter_count == 1


def test_negative_retries_setting_is_treated_as_zero(monkeypatch):
    monkeypatch.setenv("CONNECTOR_CALL_RETRIES", "-1")
    get_settings.cache_clear()

    _FakeClient.configure([httpx.ConnectError("boom")])
    monkeypatch.setattr(mcp_adapter, "Client", _FakeClient)

    with pytest.raises(ConnectorToolError):
        asyncio.run(
            mcp_adapter._call("fixture", "http://example.invalid/mcp", "tools/list", {}, _return_ok)
        )

    assert _FakeClient.enter_count == 1


def test_read_skills_retries_whole_batch_on_transient_failure(monkeypatch):
    async def fake_list_skills(client: object) -> list[object]:
        class _SkillSummary:
            name = "usage"

        return [_SkillSummary()]

    async def fake_download_skill(
        client: object, skill_name: str, temp_root_path: typing.Any
    ) -> object:
        skill_dir = temp_root_path / skill_name
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# usage skill", encoding="utf-8")
        return skill_dir

    _FakeClient.configure([httpx.ConnectError("boom"), "success"])
    monkeypatch.setattr(mcp_adapter, "Client", _FakeClient)
    monkeypatch.setattr(mcp_adapter, "list_skills", fake_list_skills)
    monkeypatch.setattr(mcp_adapter, "download_skill", fake_download_skill)

    identity_tokens = set_request_identity("user-1", "session-1", "tok", "https://sso.test/auth")
    try:
        skills = asyncio.run(
            mcp_adapter._read_skills("http://example.invalid/mcp", "fixture", None)
        )
    finally:
        reset_request_identity(identity_tokens)

    assert skills == {"usage": {"SKILL.md": "# usage skill"}}
    assert _FakeClient.enter_count == 2


def _make_connect_error_with_refused_cause() -> httpx.ConnectError:
    """組一個 cause 是 ConnectionRefusedError 的 httpx.ConnectError, 用來驗證
    log 的 traceback 會帶出兩層 cause."""
    try:
        raise ConnectionRefusedError("[Errno 61] Connection refused")
    except ConnectionRefusedError as cause:
        wrapped = httpx.ConnectError("All connection attempts failed")
        wrapped.__cause__ = cause
        return wrapped


def test_final_failure_logs_cause_chain_and_identifiers(monkeypatch, caplog):
    monkeypatch.setenv("CONNECTOR_CALL_RETRIES", "0")
    get_settings.cache_clear()

    _FakeClient.configure([_make_connect_error_with_refused_cause()])
    monkeypatch.setattr(mcp_adapter, "Client", _FakeClient)

    with (
        caplog.at_level("WARNING"),
        pytest.raises(ConnectorToolError) as error_info,
    ):
        asyncio.run(
            mcp_adapter._call(
                "fixture-connector", "http://example.invalid/mcp", "tools/list", {}, _return_ok
            )
        )

    message = str(error_info.value)
    assert "fixture-connector" in message
    assert "tools/list" in message
    assert "http://example.invalid/mcp" in message

    warning_records = [record for record in caplog.records if "MCP call failed" in record.message]
    assert warning_records, "final give-up should log a warning naming the failure"
    failure_record = warning_records[-1]
    assert "fixture-connector" in failure_record.message
    assert failure_record.exc_info is not None
    traceback_text = caplog.text
    assert "ConnectError" in traceback_text
    assert "ConnectionRefusedError" in traceback_text


def _http_status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://example.invalid/mcp")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError(f"HTTP {status_code}", request=request, response=response)


def test_http_401_is_not_retried_and_classified_as_http(monkeypatch):
    _FakeClient.configure([_http_status_error(401), "success"])
    monkeypatch.setattr(mcp_adapter, "Client", _FakeClient)

    with pytest.raises(ConnectorToolError) as error_info:
        asyncio.run(
            mcp_adapter._call("fixture", "http://example.invalid/mcp", "tools/call", {}, _return_ok)
        )

    assert _FakeClient.enter_count == 1
    assert error_info.value.kind == "http"
    assert error_info.value.status == 401
    assert error_info.value.attempts == 1


def test_http_403_is_not_retried(monkeypatch):
    _FakeClient.configure([_http_status_error(403), "success"])
    monkeypatch.setattr(mcp_adapter, "Client", _FakeClient)

    with pytest.raises(ConnectorToolError) as error_info:
        asyncio.run(
            mcp_adapter._call("fixture", "http://example.invalid/mcp", "tools/call", {}, _return_ok)
        )

    assert _FakeClient.enter_count == 1
    assert error_info.value.kind == "http"
    assert error_info.value.status == 403
    assert error_info.value.attempts == 1


def test_http_503_is_retried_and_classified_as_http(monkeypatch):
    _FakeClient.configure([_http_status_error(503), _http_status_error(503)])
    monkeypatch.setattr(mcp_adapter, "Client", _FakeClient)

    with pytest.raises(ConnectorToolError) as error_info:
        asyncio.run(
            mcp_adapter._call("fixture", "http://example.invalid/mcp", "tools/call", {}, _return_ok)
        )

    assert _FakeClient.enter_count == 2
    assert error_info.value.kind == "http"
    assert error_info.value.status == 503
    assert error_info.value.attempts == 2


def test_wrapped_connect_error_is_classified_transport_with_cause_name(monkeypatch):
    wrapped = RuntimeError("Client failed to connect: boom")
    wrapped.__cause__ = httpx.ConnectError("boom")
    _FakeClient.configure([wrapped, wrapped])
    monkeypatch.setattr(mcp_adapter, "Client", _FakeClient)

    with pytest.raises(ConnectorToolError) as error_info:
        asyncio.run(
            mcp_adapter._call("fixture", "http://example.invalid/mcp", "tools/call", {}, _return_ok)
        )

    assert error_info.value.kind == "transport"
    assert error_info.value.cause_name == "ConnectError"


def test_exception_group_cause_is_classified_transport(monkeypatch):
    grouped = ExceptionGroup("task group", [httpx.ReadTimeout("slow")])
    _FakeClient.configure([grouped, grouped])
    monkeypatch.setattr(mcp_adapter, "Client", _FakeClient)

    with pytest.raises(ConnectorToolError) as error_info:
        asyncio.run(
            mcp_adapter._call("fixture", "http://example.invalid/mcp", "tools/call", {}, _return_ok)
        )

    assert error_info.value.kind == "transport"
    assert error_info.value.cause_name == "ReadTimeout"


def test_unknown_exception_is_classified_transport_with_its_own_class_name(monkeypatch):
    _FakeClient.configure([ValueError("odd"), ValueError("odd")])
    monkeypatch.setattr(mcp_adapter, "Client", _FakeClient)

    with pytest.raises(ConnectorToolError) as error_info:
        asyncio.run(
            mcp_adapter._call("fixture", "http://example.invalid/mcp", "tools/call", {}, _return_ok)
        )

    assert error_info.value.kind == "transport"
    assert error_info.value.cause_name == "ValueError"


def test_classify_cause_skips_suppressed_context():
    try:
        try:
            raise httpx.HTTPStatusError(
                "HTTP 401",
                request=httpx.Request("POST", "http://example.invalid/mcp"),
                response=httpx.Response(
                    401, request=httpx.Request("POST", "http://example.invalid/mcp")
                ),
            )
        except httpx.HTTPStatusError:
            raise ValueError("deliberately unlinked") from None
    except ValueError as raised:
        kind, status, cause_name = mcp_adapter._classify_cause(raised)

    assert kind == "transport"
    assert status is None
    assert cause_name == "ValueError"


def test_connector_tool_error_kind_defaults_keep_chat_mode_unchanged():
    error = ConnectorToolError("plain")
    assert (error.kind, error.status, error.attempts, error.cause_name, error.detail) == (
        "transport",
        None,
        None,
        None,
        None,
    )
    assert str(error) == "plain"


def test_final_failure_log_does_not_leak_header_values(monkeypatch, caplog):
    """headers 本身從不進 log 呼叫的參數清單, 這裡用可辨識的假值確認就算 headers 裡帶著
    token 也不會出現在 log 文字裡。"""
    monkeypatch.setenv("CONNECTOR_CALL_RETRIES", "0")
    get_settings.cache_clear()

    _FakeClient.configure([httpx.ConnectError("boom")])
    monkeypatch.setattr(mcp_adapter, "Client", _FakeClient)
    leaking_headers = {
        "X-SSO-Token": "must-not-leak-fake-sso-token",
        "X-SSO-Url": "https://sso.test.example/auth",
    }

    with caplog.at_level("DEBUG"), pytest.raises(ConnectorToolError):
        asyncio.run(
            mcp_adapter._call(
                "fixture", "http://example.invalid/mcp", "tools/list", leaking_headers, _return_ok
            )
        )

    assert "must-not-leak-fake-sso-token" not in caplog.text
