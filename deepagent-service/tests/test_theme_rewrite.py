"""`apply_erd_theme` 的確定性改寫行為——不驗證,只盡力改寫(見 app/engine/theme_rewrite.py)。"""

from app.engine.theme_rewrite import apply_erd_theme

VALID_HTML = (
    '<html><head><script src="https://cdn.tailwindcss.com"></script></head>'
    '<body><div id="chart"></div>'
    '<script>const data = window.__ERD_RESULTS__["q1"]; '
    "const chart = echarts.init(document.getElementById(\"chart\"), 'erd'); "
    'chart.setOption({ tooltip: { trigger: "axis" }, series: [] });</script>'
    "</body></html>"
)


def test_single_arg_init_rewritten_to_erd() -> None:
    """單參數呼叫(引數本身含括號/字串——`document.getElementById("chart")`——正是括號深度
    平衡掃描要正確處理的案例)補上 `'erd'` 主題。"""
    html = VALID_HTML.replace(
        "echarts.init(document.getElementById(\"chart\"), 'erd')",
        'echarts.init(document.getElementById("chart"))',
    )
    result = apply_erd_theme(html)
    assert "echarts.init(document.getElementById(\"chart\"), 'erd')" in result


def test_existing_erd_theme_second_arg_untouched() -> None:
    result = apply_erd_theme(VALID_HTML)
    assert result == VALID_HTML


def test_non_erd_two_arg_theme_passes_through_unchanged() -> None:
    """guard 移除後不再記錯誤——非 'erd' 的既有第二參數原樣保留,不改寫、不報錯。"""
    html = VALID_HTML.replace("'erd'", "'dark'")
    result = apply_erd_theme(html)
    assert result == html


def test_no_echarts_init_call_returns_html_unchanged() -> None:
    html = "<html><head></head><body><div>no charts here</div></body></html>"
    assert apply_erd_theme(html) == html


def test_multiple_init_calls_all_rewritten() -> None:
    html = "echarts.init(document.getElementById('a'));echarts.init(document.getElementById('b'));"
    result = apply_erd_theme(html)
    assert "echarts.init(document.getElementById('a'), 'erd')" in result
    assert "echarts.init(document.getElementById('b'), 'erd')" in result


def test_unbalanced_parens_left_untouched() -> None:
    """畸形呼叫(括號不平衡)沒有匹配的閉括號可算——原樣保留、不拋例外,繼續掃描其餘內容。"""
    html = "echarts.init(document.getElementById('a')"
    assert apply_erd_theme(html) == html
