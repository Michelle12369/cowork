import re
from pathlib import Path

from app.engine.theme_rewrite import apply_erd_theme

SKILL_DIR = Path(__file__).resolve().parents[1] / "skills" / "dashboard"


def test_skill_frontmatter_has_name_and_description() -> None:
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    frontmatter = text.split("---")[1]
    assert "name: dashboard" in frontmatter
    assert "description:" in frontmatter


def _example_htmls() -> list[str]:
    # 範例現與規則合併在單一 SKILL.md;只取完整可渲染的 dashboard 文件,排除 banner/KPI/tabs
    # 等片段模板(它們也是 ```html 區塊,但不是完整頁面)。
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```html\n(.*?)```", text, flags=re.DOTALL)
    return [block for block in blocks if "<!DOCTYPE html" in block]


def test_examples_exist_and_theme_rewrite_is_a_noop() -> None:
    # 範例的 echarts.init 呼叫已一律帶 'erd' 主題——apply_erd_theme 對它們是恆等操作。
    examples = _example_htmls()
    assert len(examples) >= 1
    for example in examples:
        assert apply_erd_theme(example) == example


def test_examples_never_embed_data_arrays() -> None:
    for example in _example_htmls():
        assert "__ERD_RESULTS__" in example
