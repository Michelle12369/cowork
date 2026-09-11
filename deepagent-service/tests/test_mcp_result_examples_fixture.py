"""contract fixture `mcp_result_examples.json` 的同步測試——確保 Java 與前端載入的範例
與 `error_codes` 的訊息模板函式逐字一致,不會各自漂移。`INVALID_CALL` 例外:自 09-11 起
deepagent 不再產生這個 code(row 3 已改由 request schema 擋在 422),範例改記 Java 代理自己
的情境(connector 不在 session 的 allow-list 內),不從任何 deepagent 模板重建,只驗證它仍能
通過 `ToolCallFailure` schema。"""

import json
from pathlib import Path

from app.agent.connectors import error_codes
from app.api.schemas import ToolCallFailure

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "mcp_result_examples.json"
_FIVE_CODES = {"AUTH", "RETRYABLE", "TOOL_ERROR", "INVALID_CALL", "CONNECTOR_UNAVAILABLE"}


def _load_fixture() -> dict:
    with _FIXTURE_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def test_fixture_has_one_example_per_code_and_one_success() -> None:
    fixture = _load_fixture()
    assert set(fixture) == _FIVE_CODES | {"success"}


def test_fixture_examples_have_exactly_one_of_data_or_error() -> None:
    fixture = _load_fixture()
    for example_key, example in fixture.items():
        assert set(example) in ({"data"}, {"error"}), example_key


def test_fixture_error_messages_are_what_the_templates_produce() -> None:
    """Four of the five codes are rebuilt from deepagent's own templates. `INVALID_CALL` is not:
    deepagent no longer produces it (row 3 moved to the request schema, HTTP 422), so that example
    documents the Java proxy's own case instead — checked separately below."""
    fixture = _load_fixture()
    expected_messages = {
        "AUTH": error_codes.credentials_rejected("sales", 401).message,
        "RETRYABLE": error_codes.no_response("sales", "ConnectError", 2).message,
        "TOOL_ERROR": error_codes.tool_reported_error(
            "get_quality",
            "unknown fab 'FAB_Z'; valid fab ids: FAB_A, FAB_B, FAB_C (call list_fabs)",
        ).message,
        "CONNECTOR_UNAVAILABLE": error_codes.no_structured_data("sales", "list_orders").message,
    }
    for code, expected_message in expected_messages.items():
        assert fixture[code]["error"]["message"] == expected_message

    invalid_call_message = fixture["INVALID_CALL"]["error"]["message"]
    assert "not enabled for this session" in invalid_call_message
    assert "allowed" in invalid_call_message


def test_fixture_codes_match_schema_literal() -> None:
    fixture = _load_fixture()
    for code in _FIVE_CODES:
        ToolCallFailure.model_validate(fixture[code])
