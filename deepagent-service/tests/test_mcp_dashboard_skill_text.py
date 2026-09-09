"""mcp-data-dashboard SKILL.md 與工具回饋文字的一致性——skill 講的讀列路徑來源與 session 定義
必須和 wrapper 回饋、check_dashboard 的紀錄一致, 否則模型會二選一."""

from pathlib import Path

_SKILL_PATH = Path(__file__).resolve().parents[1] / "skills" / "mcp-data-dashboard" / "SKILL.md"


def _skill_text() -> str:
    return _SKILL_PATH.read_text(encoding="utf-8")


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
