"""mcp_adapter 重試層測試, 涵蓋 _run_with_retry 與 _is_transient_failure.

monkeypatch 模組層的 Client 驗證逾時值, 立刻重試與不重試的分野, 重試耗盡拋錯,
cause 鏈包裝過的例外辨識, 以及 skill 讀取整組重試的語意.
"""

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


def _make_wrapped_transient_exception() -> RuntimeError:
    """組一個 cause 是 httpx.ConnectTimeout 的 RuntimeError, 模擬 fastmcp 或 mcp
    把底層例外再包一層的情況."""
    try:
        raise httpx.ConnectTimeout("connect timed out")
    except httpx.ConnectTimeout as cause:
        wrapped = RuntimeError("wrapped transport failure")
        wrapped.__cause__ = cause
        return wrapped


def _make_http_status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "http://example.invalid/mcp")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError(f"status {status_code}", request=request, response=response)


def test_call_uses_configured_request_timeout(monkeypatch):
    monkeypatch.setenv("CONNECTOR_REQUEST_TIMEOUT_SECONDS", "1.5")
    get_settings.cache_clear()

    _FakeClient.configure(["success"])
    monkeypatch.setattr(mcp_adapter, "Client", _FakeClient)

    result = asyncio.run(
        mcp_adapter._call("http://example.invalid/mcp", "tools/list", {}, _return_ok)
    )

    assert result == "ok"
    assert _FakeClient.received_timeouts == [1.5]


def test_transient_failure_then_success_retries_once(monkeypatch):
    _FakeClient.configure([httpx.ConnectError("boom"), "success"])
    monkeypatch.setattr(mcp_adapter, "Client", _FakeClient)

    result = asyncio.run(
        mcp_adapter._call("http://example.invalid/mcp", "tools/list", {}, _return_ok)
    )

    assert result == "ok"
    assert _FakeClient.enter_count == 2


def test_retries_exhausted_raises_connector_tool_error_naming_method(monkeypatch):
    monkeypatch.setenv("CONNECTOR_CALL_RETRIES", "2")
    get_settings.cache_clear()

    _FakeClient.configure([httpx.ReadTimeout("slow")] * 3)
    monkeypatch.setattr(mcp_adapter, "Client", _FakeClient)

    with pytest.raises(ConnectorToolError, match="tools/call") as error_info:
        asyncio.run(mcp_adapter._call("http://example.invalid/mcp", "tools/call", {}, _return_ok))

    assert _FakeClient.enter_count == 3
    assert "ReadTimeout" in str(error_info.value)


@pytest.mark.parametrize(
    "make_non_transient_exception",
    [lambda: _make_http_status_error(401), lambda: ValueError("bad request")],
)
def test_non_transient_failure_does_not_retry(monkeypatch, make_non_transient_exception):
    _FakeClient.configure([make_non_transient_exception()])
    monkeypatch.setattr(mcp_adapter, "Client", _FakeClient)

    with pytest.raises(ConnectorToolError):
        asyncio.run(mcp_adapter._call("http://example.invalid/mcp", "tools/list", {}, _return_ok))

    assert _FakeClient.enter_count == 1


def test_wrapped_transient_cause_is_still_recognized_and_retried(monkeypatch):
    _FakeClient.configure([_make_wrapped_transient_exception(), "success"])
    monkeypatch.setattr(mcp_adapter, "Client", _FakeClient)

    result = asyncio.run(
        mcp_adapter._call("http://example.invalid/mcp", "tools/list", {}, _return_ok)
    )

    assert result == "ok"
    assert _FakeClient.enter_count == 2


def test_zero_retries_setting_attempts_only_once_on_transient_failure(monkeypatch):
    monkeypatch.setenv("CONNECTOR_CALL_RETRIES", "0")
    get_settings.cache_clear()

    _FakeClient.configure([httpx.ConnectError("boom")])
    monkeypatch.setattr(mcp_adapter, "Client", _FakeClient)

    with pytest.raises(ConnectorToolError):
        asyncio.run(mcp_adapter._call("http://example.invalid/mcp", "tools/list", {}, _return_ok))

    assert _FakeClient.enter_count == 1


def test_negative_retries_setting_is_treated_as_zero(monkeypatch):
    monkeypatch.setenv("CONNECTOR_CALL_RETRIES", "-1")
    get_settings.cache_clear()

    _FakeClient.configure([httpx.ConnectError("boom")])
    monkeypatch.setattr(mcp_adapter, "Client", _FakeClient)

    with pytest.raises(ConnectorToolError):
        asyncio.run(mcp_adapter._call("http://example.invalid/mcp", "tools/list", {}, _return_ok))

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
