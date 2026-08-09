import re
from pathlib import Path

from app.engine.html_guard import check_dashboard_html

SKILL_DIR = Path(__file__).resolve().parents[1] / "skills" / "dashboard"


def test_skill_frontmatter_has_name_and_description() -> None:
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    frontmatter = text.split("---")[1]
    assert "name: dashboard" in frontmatter
    assert "description:" in frontmatter


def _example_htmls() -> list[str]:
    # 範例現與規則合併在單一 SKILL.md;只取完整可渲染的 dashboard 文件,排除 banner/KPI/tabs
    # 等片段模板(它們也是 ```html 區塊,但不是完整頁面、不該送進 guard)。
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```html\n(.*?)```", text, flags=re.DOTALL)
    return [block for block in blocks if "<!DOCTYPE html" in block]


def test_examples_exist_and_pass_html_guard() -> None:
    examples = _example_htmls()
    assert len(examples) >= 1
    for example in examples:
        report = check_dashboard_html(example, {"q1", "q2", "q3", "q4", "q5"})
        assert report.ok, report.errors


def test_examples_never_embed_data_arrays() -> None:
    for example in _example_htmls():
        assert "__ERD_RESULTS__" in example
