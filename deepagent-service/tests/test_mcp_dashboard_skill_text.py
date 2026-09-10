"""mcp-data-dashboard SKILL.md 與工具回饋文字的一致性——skill 講的讀列路徑來源與 session 定義
必須和 wrapper 回饋、check_dashboard 的紀錄一致, 否則模型會二選一."""

import re
from pathlib import Path

from app.engine.workspace import prepare_local_layout
from tests.test_check_dashboard import _build_dashboard_html, _check_report, _sales_connector

_SKILL_PATH = Path(__file__).resolve().parents[1] / "skills" / "mcp-data-dashboard" / "SKILL.md"

_JS_FENCE_PATTERN = re.compile(r"```js\n(.*?)```", re.DOTALL)


def _skill_text() -> str:
    return _SKILL_PATH.read_text(encoding="utf-8")


def _frontmatter_block(text: str) -> str:
    return text.split("---", 2)[1]


def _section_text(text: str, heading: str) -> str:
    """Slice from `heading` up to (not including) the next heading of the same or a
    higher level (fewer leading '#')."""
    start_index = text.index(heading)
    level = len(heading) - len(heading.lstrip("#"))
    next_heading_pattern = re.compile(rf"^#{{1,{level}}} ", re.MULTILINE)
    match = next_heading_pattern.search(text, start_index + len(heading))
    end_index = match.start() if match else len(text)
    return text[start_index:end_index]


def _js_fence_blocks(section_text: str) -> list[str]:
    return _JS_FENCE_PATTERN.findall(section_text)


def test_skill_has_no_land_as() -> None:
    assert "land_as" not in _skill_text()


def test_skill_points_r_data_path_at_raw_response_shape_feedback() -> None:
    text = _skill_text()
    assert "Raw response shape" in text
    assert "byte-for-byte" not in text
    assert "`r.data` is the raw response" in text


def test_skill_defines_this_session_as_recorded_calls_in_any_turn() -> None:
    text = _skill_text()
    assert "any turn of this conversation" in text
    assert "the tool feedback in this conversation is the record" in text


def test_skill_reading_the_response_shows_three_paths() -> None:
    text = _skill_text()
    assert "const rows = r.data;" in text
    assert "const rows = r.data.result;" in text
    assert "const rows = r.data.data;" in text


def test_skill_description_mentions_error_codes() -> None:
    frontmatter = _frontmatter_block(_skill_text())
    assert "r.error.code" in frontmatter


def test_skill_names_the_five_error_codes() -> None:
    text = _skill_text()
    for code in ("AUTH", "RETRYABLE", "TOOL_ERROR", "INVALID_CALL", "CONNECTOR_UNAVAILABLE"):
        assert code in text
    assert "r.error.code" in text


def test_skill_teaches_retry_only_for_retryable_and_banner_for_auth() -> None:
    text = _skill_text()
    assert "r.error.code === 'RETRYABLE'" in text
    assert "r.error.code === 'AUTH'" in text
    assert "showCardError(" in text
    assert "showAuthBanner(" in text


def test_skill_states_handler_async_and_runtime_does_not_swallow_and_args_json() -> None:
    text = _skill_text()
    assert "always called **asynchronously**" in text
    assert "never before `mcp()` itself has returned" in text
    assert "does **not** catch exceptions thrown inside the handler" in text
    assert "window.onerror" in text
    assert "drives the repair flow" in text
    assert "JSON-serializable" in text
    assert "`undefined` values are" in text
    assert "dropped from the object" in text
    assert "`NaN` becomes `null`" in text


def test_skill_complete_example_uses_auth_and_retryable_branching() -> None:
    complete_example_section = _section_text(_skill_text(), "### complete example")
    assert "showAuthBanner(" in complete_example_section
    assert "showCardError(" in complete_example_section


def test_skill_snippets_pass_check_dashboard_contract_lint(tmp_path) -> None:
    text = _skill_text()
    card_states_section = _section_text(text, "## Card states -- loading / error / empty / content")
    reading_response_section = _section_text(text, "### Reading the response")
    combined_js = "\n\n".join(
        [*_js_fence_blocks(card_states_section), *_js_fence_blocks(reading_response_section)]
    )

    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    workspace.dashboard_path.write_text(_build_dashboard_html(combined_js), encoding="utf-8")

    report = _check_report(workspace, (_sales_connector(),))

    contract_findings = [line for line in report.splitlines() if line.startswith("- [contract]")]
    assert contract_findings == []
    assert "forbidden token" not in report
