import json
import re
import time
from pathlib import Path

from app.engine.html_guard import ALLOWED_SCRIPT_SRC_PREFIXES, GuardReport, check_dashboard_html
from app.engine.results import referenced_query_ids, strip_injected_blocks

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

# `build_results_script`'s exact injected shape (`app/engine/results.py`): a single
# `<script id="erd-results-data">window.__ERD_RESULTS__ = {json};</script>` block, with `</`
# inside the JSON escaped to `<\/` -- so `;</script>` only ever appears once, as the real
# terminator, making a non-greedy match safe here.
_INJECTED_RESULTS_PATTERN = re.compile(r"window\.__ERD_RESULTS__ = (\{.*?\});</script>", re.DOTALL)


def _parse_injected_results(html: str) -> dict[str, dict]:
    """Recover the real `{query_id: {columns, rows, truncated, ...}}` payload a shipped
    fixture was built with, straight out of its own injected `erd-results-data` script -- same
    shape `load_all_results` returns in production, so it can be handed to
    `check_dashboard_html`'s `results` parameter exactly like `app/main.py` does."""
    match = _INJECTED_RESULTS_PATTERN.search(html)
    assert match is not None, "fixture is missing its injected erd-results-data script"
    return json.loads(match.group(1))


VALID_HTML = (
    '<html><head><script src="' + ALLOWED_SCRIPT_SRC_PREFIXES[0] + '"></script></head>'
    '<body><div id="chart"></div>'
    '<script>const data = window.__ERD_RESULTS__["q1"]; '
    "const chart = echarts.init(document.getElementById(\"chart\"), 'erd'); "
    'chart.setOption({ tooltip: { trigger: "axis" }, series: [] });</script>'
    "</body></html>"
)


def test_valid_html_passes() -> None:
    report = check_dashboard_html(VALID_HTML, {"q1"})
    assert report.ok and report.errors == []


def test_empty_html_fails() -> None:
    assert not check_dashboard_html("", set()).ok


def test_html_missing_closing_html_tag_fails() -> None:
    """真實 dashboard 最多量到 62855 bytes(約 18K tokens),對比模型輸出 budget 約 24K tokens
    ——輸出被腰斬是活生生的風險。PR #6 之後每次 dashboard 修改都是單次完整 `write_file`,
    若輸出剛好在最後一個 `</script>` 之後被截斷,舊版 `_check_structure`(只查 `<div`)完全
    看不出來、會照樣出貨一份不完整的文件。要求 `</html>` 收尾標籤是最低成本的截斷偵測。"""
    html = VALID_HTML.replace("</html>", "")
    assert "</html>" not in html
    report = check_dashboard_html(html, {"q1"})
    assert not report.ok
    assert any("</html>" in error for error in report.errors), report.errors


def test_html_missing_closing_html_tag_is_unconditional() -> None:
    """截斷的文件 MUST 永不出貨,即便 `ERD_GUARD_BLOCKING=false` 把其他規則降成建議性
    ——`ChatTurn.finalize()` 只讀 `unconditional_errors` 決定要不要無條件擋下。"""
    html = VALID_HTML.replace("</html>", "")
    report = check_dashboard_html(html, {"q1"})
    assert any("</html>" in error for error in report.unconditional_errors), (
        report.unconditional_errors
    )


def test_foreign_script_src_fails() -> None:
    html = VALID_HTML.replace(ALLOWED_SCRIPT_SRC_PREFIXES[0], "https://evil.example.com/x.js")
    report = check_dashboard_html(html, {"q1"})
    assert not report.ok
    assert any("evil.example.com" in error for error in report.errors)


def test_foreign_script_src_is_unconditional() -> None:
    """`<script src>` 白名單是唯一的遠端腳本邊界(repo 內沒有 CSP,artifact iframe 的
    sandbox 也沒有限制網路出口)——這條違規 MUST 進 `unconditional_errors`,`ERD_GUARD_
    BLOCKING=false` 也不能讓它變成建議性。"""
    html = VALID_HTML.replace(ALLOWED_SCRIPT_SRC_PREFIXES[0], "https://evil.example.com/x.js")
    report = check_dashboard_html(html, {"q1"})
    assert any("evil.example.com" in error for error in report.unconditional_errors)


# -- script-src whitelist: host-boundary bypass regressions -----------------------------


def test_lookalike_host_script_src_fails() -> None:
    """`https://cdn.tailwindcss.com.evil.example/x.js` starts with the allowed prefix as a
    raw string, but the real host is `cdn.tailwindcss.com.evil.example` -- fully
    attacker-controlled. A naive `src.startswith(prefix)` check lets this through; the
    host-boundary check must not."""
    html = VALID_HTML.replace(
        ALLOWED_SCRIPT_SRC_PREFIXES[0], ALLOWED_SCRIPT_SRC_PREFIXES[0] + ".evil.example/x.js"
    )
    report = check_dashboard_html(html, {"q1"})
    assert not report.ok
    assert any("evil.example" in error for error in report.errors)


def test_unquoted_script_src_fails() -> None:
    """`<script src=https://evil.example/x.js>` is valid HTML that a browser will load, but
    a pattern requiring a quote after `=` never matches it -- silently skipping the check
    entirely. The tokenizer-based check must catch unquoted src values too."""
    html = VALID_HTML.replace(
        '<script src="' + ALLOWED_SCRIPT_SRC_PREFIXES[0] + '"></script>',
        "<script src=https://evil.example/x.js></script>",
    )
    report = check_dashboard_html(html, {"q1"})
    assert not report.ok
    assert any("evil.example" in error for error in report.errors)


def test_slash_separated_script_src_fails() -> None:
    """`<script/src="https://evil.example/x.js">` -- the HTML5 tokenizer treats `/` between
    the tag name and an attribute as an attribute separator (same as whitespace), so a
    browser loads it. A pattern requiring whitespace after `<script` never matches it."""
    html = VALID_HTML.replace(
        '<script src="' + ALLOWED_SCRIPT_SRC_PREFIXES[0] + '"></script>',
        '<script/src="https://evil.example/x.js"></script>',
    )
    report = check_dashboard_html(html, {"q1"})
    assert not report.ok
    assert any("evil.example" in error for error in report.errors)


def test_jsdelivr_full_echarts_path_passes() -> None:
    """A realistic full jsdelivr echarts asset path (not just the bare prefix) must still
    pass -- regression guard against over-tightening the host+path check."""
    html = VALID_HTML.replace(
        ALLOWED_SCRIPT_SRC_PREFIXES[0],
        "https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js",
    )
    report = check_dashboard_html(html, {"q1"})
    assert report.ok


def test_jsdelivr_non_echarts_package_fails() -> None:
    """jsdelivr is only allowlisted for the echarts npm package, not the whole CDN -- any
    other package on the same host must still be rejected."""
    html = VALID_HTML.replace(ALLOWED_SCRIPT_SRC_PREFIXES[0], "https://cdn.jsdelivr.net/npm/evil@1")
    report = check_dashboard_html(html, {"q1"})
    assert not report.ok
    assert any("evil@1" in error for error in report.errors)


def test_dangling_result_reference_fails() -> None:
    report = check_dashboard_html(VALID_HTML, set())
    assert not report.ok
    assert any("q1" in error for error in report.errors)


def test_dangling_result_reference_is_not_unconditional() -> None:
    """對照組:非安全性、非截斷的違規(缺查詢結果引用)仍只是普通失敗,`ERD_GUARD_BLOCKING=
    false` 時應維持建議性——只有 script src 白名單與截斷偵測才進 `unconditional_errors`。"""
    report = check_dashboard_html(VALID_HTML, set())
    assert not report.ok
    assert report.unconditional_errors == []


def test_single_arg_init_rewritten_to_erd() -> None:
    html = VALID_HTML.replace(
        "echarts.init(document.getElementById(\"chart\"), 'erd')",
        'echarts.init(document.getElementById("chart"))',
    )
    report = check_dashboard_html(html, {"q1"})
    assert report.ok
    assert "echarts.init(document.getElementById(\"chart\"), 'erd')" in report.html


def test_wrong_theme_arg_fails() -> None:
    html = VALID_HTML.replace("'erd'", "'dark'")
    report = check_dashboard_html(html, {"q1"})
    assert not report.ok


def test_theme_rewrite_does_not_touch_body_text_or_string_literals(monkeypatch) -> None:
    """C1: `_apply_erd_theme` only scans inside `<script>` blocks, and masks strings/comments
    within them -- an `echarts.init(` mention in visible HTML body text or inside a JS string
    literal must survive the guard byte-for-byte, only the real call site changes."""
    html = (
        '<html><head><script src="' + ALLOWED_SCRIPT_SRC_PREFIXES[0] + '"></script></head>'
        '<body><div id="chart"></div>'
        "<p>使用 echarts.init(el) 建立圖表</p>"
        '<script>const data = window.__ERD_RESULTS__["q1"]; '
        "const hint = 'call echarts.init(el) to create a chart'; "
        'const chart = echarts.init(document.getElementById("chart")); '
        'chart.setOption({ tooltip: { trigger: "axis" }, series: [] });</script>'
        "</body></html>"
    )
    report = check_dashboard_html(html, {"q1"})
    assert report.ok, report.errors
    assert "<p>使用 echarts.init(el) 建立圖表</p>" in report.html
    assert "'call echarts.init(el) to create a chart'" in report.html
    assert "echarts.init(document.getElementById(\"chart\"), 'erd')" in report.html


def test_theme_rewrite_output_is_resynced_when_it_changes_syntax(monkeypatch) -> None:
    """Second half of C1: `_apply_erd_theme` is the only rule that mutates the document, and it
    runs after every other check, so nothing re-validates its output. Simulate a rewrite that
    happens to break JS syntax and confirm `check_dashboard_html` no longer ships it with
    ok=True -- the guard must re-run the syntax check on its own output when it changed
    something."""
    import app.engine.html_guard.checker as checker_module

    def _corrupt_rewrite(html: str, errors: list[str]) -> str:
        return html.replace(
            "echarts.init(document.getElementById(\"chart\"), 'erd')", "echarts.init("
        )

    monkeypatch.setattr(checker_module, "_apply_erd_theme", _corrupt_rewrite)

    report = check_dashboard_html(VALID_HTML, {"q1"})

    assert not report.ok
    assert any("JS syntax error" in error for error in report.errors), report.errors


def test_theme_rewrite_skips_resync_when_output_is_unchanged(monkeypatch) -> None:
    """The re-check is gated on the rewrite actually changing something -- HTML with no
    `echarts.init(` call at all must not pay for (or trigger) a second syntax pass."""
    import app.engine.html_guard.checker as checker_module

    call_count = 0
    original_check_js_syntax = checker_module.js_syntax.check_js_syntax

    def _counting_check(html: str, errors: list[str]) -> None:
        nonlocal call_count
        call_count += 1
        original_check_js_syntax(html, errors)

    monkeypatch.setattr(checker_module.js_syntax, "check_js_syntax", _counting_check)

    html = (
        '<html><head><script src="' + ALLOWED_SCRIPT_SRC_PREFIXES[1] + '5"></script></head>'
        "<body><div>no charts here</div></body></html>"
    )
    report = check_dashboard_html(html, set())

    assert report.ok
    assert call_count == 1  # only the Level 1 pass -- no second pass since nothing changed


def test_oversized_html_fails() -> None:
    report = check_dashboard_html(VALID_HTML + "x" * 2_000_001, {"q1"})
    assert not report.ok


def test_guard_report_is_dataclass_with_defaults() -> None:
    report = GuardReport(ok=True)
    assert report.errors == []
    assert report.unconditional_errors == []
    assert report.html == ""


def test_multiple_violations_all_collected() -> None:
    html = VALID_HTML.replace(ALLOWED_SCRIPT_SRC_PREFIXES[0], "https://evil.example.com/x.js")
    report = check_dashboard_html(html, set())
    assert not report.ok
    assert any("evil.example.com" in error for error in report.errors)
    assert any("q1" in error for error in report.errors)


def test_register_theme_call_fails() -> None:
    html = VALID_HTML.replace(
        "<script>",
        "<script>echarts.registerTheme('erd', {color: ['#000']});",
        1,
    )
    report = check_dashboard_html(html, {"q1"})
    assert not report.ok
    assert any("registerTheme" in error for error in report.errors)


def test_no_echarts_init_call_still_ok() -> None:
    html = (
        '<html><head><script src="' + ALLOWED_SCRIPT_SRC_PREFIXES[1] + '5"></script></head>'
        "<body><div>no charts here</div></body></html>"
    )
    report = check_dashboard_html(html, set())
    assert report.ok
    assert report.html == html


# -- H1: model must never overwrite the injected window.__ERD_RESULTS__ -----------------
#
# `inject_results` (app/engine/results.py) writes the real query results into
# `<script id="erd-results-data">window.__ERD_RESULTS__ = {...}</script>` right before
# `</head>`, AFTER check_dashboard_html has already approved the model's HTML. If the model's
# own <body> script also assigns to window.__ERD_RESULTS__ (or declares that exact injector
# script id), DOM order lets the model's version win -- the user sees fabricated numbers, and
# `_check_data_binding` (which only checks the binding is *referenced*) never catches it.


def test_erd_results_assignment_fails() -> None:
    html = VALID_HTML.replace(
        "<script>",
        "<script>window.__ERD_RESULTS__ = { q1: { columns: [], rows: [] } };",
        1,
    )
    report = check_dashboard_html(html, {"q1"})
    assert not report.ok
    assert any("__ERD_RESULTS__" in error for error in report.errors)


def test_erd_results_assignment_is_unconditional() -> None:
    """Shipping fabricated numbers is the same class of violation as the truncation check
    (see report.py) -- it must ship-block even when ERD_GUARD_BLOCKING=false makes other
    rules advisory-only."""
    html = VALID_HTML.replace(
        "<script>",
        "<script>window.__ERD_RESULTS__ = { q1: { columns: [], rows: [] } };",
        1,
    )
    report = check_dashboard_html(html, {"q1"})
    assert any("__ERD_RESULTS__" in error for error in report.unconditional_errors)


def test_erd_results_bare_assignment_without_window_prefix_fails() -> None:
    html = VALID_HTML.replace(
        "<script>",
        "<script>__ERD_RESULTS__ = { q1: { columns: [], rows: [] } };",
        1,
    )
    report = check_dashboard_html(html, {"q1"})
    assert not report.ok
    assert any("__ERD_RESULTS__" in error for error in report.errors)


def test_erd_results_injector_script_id_spoof_fails() -> None:
    """The model must never emit `id="erd-results-data"` itself -- that id belongs
    exclusively to the injector, and `strip_injected_blocks`/downstream tooling identify the
    real results block by it."""
    html = VALID_HTML.replace(
        "<head>", '<head><script id="erd-results-data">window.x = 1;</script>', 1
    )
    report = check_dashboard_html(html, {"q1"})
    assert not report.ok
    assert any("erd-results-data" in error for error in report.errors)
    assert any("erd-results-data" in error for error in report.unconditional_errors)


def test_erd_results_read_comparisons_do_not_false_positive() -> None:
    """`===`, `==`, `!=`, `>=` all contain a bare `=` next to the identifier but are reads,
    not assignments -- none of them may trip the rule."""
    html = VALID_HTML.replace(
        "<script>",
        "<script>"
        "if (window.__ERD_RESULTS__ === undefined) { console.log('missing'); } "
        "if (window.__ERD_RESULTS__ == null) {} "
        "if (window.__ERD_RESULTS__ != null) {} "
        "if (Object.keys(window.__ERD_RESULTS__).length >= 1) {} ",
        1,
    )
    report = check_dashboard_html(html, {"q1"})
    assert report.ok, report.errors


def test_erd_results_assignment_mention_in_prose_or_string_does_not_false_positive() -> None:
    """Same class of bug as C1 (theme rewrite): scanning raw HTML instead of masked
    in-script content would fire on visible body text or on a JS string/comment that merely
    mentions the assignment -- neither is an actual overwrite."""
    html = (
        '<html><head><script src="' + ALLOWED_SCRIPT_SRC_PREFIXES[0] + '"></script></head>'
        '<body><div id="chart"></div>'
        "<p>不要自己寫 __ERD_RESULTS__ = ... 這種賦值</p>"
        '<script>const data = window.__ERD_RESULTS__["q1"]; '
        "// example: window.__ERD_RESULTS__ = {} is forbidden\n"
        "const warning = 'never write window.__ERD_RESULTS__ = yourself'; "
        "const chart = echarts.init(document.getElementById(\"chart\"), 'erd'); "
        'chart.setOption({ tooltip: { trigger: "axis" }, series: [] });</script>'
        "</body></html>"
    )
    report = check_dashboard_html(html, {"q1"})
    assert report.ok, report.errors


# -- quickjs syntax check (Level 1) -----------------------------------------------------


def test_js_valid_syntax_passes() -> None:
    report = check_dashboard_html(VALID_HTML, {"q1"})
    assert report.ok
    assert not any("JS syntax error" in error for error in report.errors)


def test_js_unclosed_brace_syntax_error_detected() -> None:
    html = (
        '<html><head></head><body><div id="chart"></div>'
        "<script>function broken() { const a = 1;</script>"
        "</body></html>"
    )
    report = check_dashboard_html(html, set())
    assert not report.ok
    assert any("script#0" in error and "JS syntax error" in error for error in report.errors)


def test_js_syntax_error_reports_html_absolute_line_number() -> None:
    """語法錯誤訊息的行號是 HTML 絕對行號,不是 script 內相對行號——`<script>` 前面有
    兩行 `<html>`/`<head>` 把它推到 HTML 第 3 行,壞的 token 又是 script 內容自己的第 3
    行(`const b = ;`),兩者相加減一,答案落在 HTML 實際第 5 行(逐行數過驗證)。"""
    html = (
        "<html>\n"
        "<head></head>\n"
        '<body><div id="chart"></div><script>\n'
        "const a = 1;\n"
        "const b = ;\n"
        "const c = 3;\n"
        "</script></body></html>"
    )
    report = check_dashboard_html(html, set())
    assert not report.ok
    assert any(
        "script#0" in error and "line 5" in error and "JS syntax error" in error
        for error in report.errors
    ), report.errors


def test_js_string_containing_close_script_tag_not_misparsed() -> None:
    """A JS string literal that embeds the text `</script>` (e.g. an ECharts tooltip
    formatter) must not be mistaken for the real closing tag -- doing so would truncate
    the script content mid-statement and report a bogus syntax error."""
    html = (
        '<html><head></head><body><div id="chart"></div>'
        '<script>const label = "</script>"; const value = 1;</script>'
        "</body></html>"
    )
    report = check_dashboard_html(html, set())
    assert not any("JS syntax error" in error for error in report.errors)


def test_js_template_literal_with_interpolation_valid() -> None:
    html = (
        '<html><head></head><body><div id="chart"></div>'
        "<script>const name = 'x'; const message = `hello ${name} world`; "
        "console.log(message);</script>"
        "</body></html>"
    )
    report = check_dashboard_html(html, set())
    assert not any("JS syntax error" in error for error in report.errors)


def test_js_undeclared_variable_reference_is_not_caught_by_parse_only_check() -> None:
    """quickjs syntax check is parse-only (mirrors the Java GraalVM validator in
    JsSyntaxValidator.java): referencing an undeclared identifier is a *runtime*
    ReferenceError, not a SyntaxError. This is a documented, accepted boundary of a
    Level-1 (syntax only) check -- NOT a bug to fix here."""
    html = (
        '<html><head></head><body><div id="chart"></div>'
        "<script>console.log(Candidates);</script>"
        "</body></html>"
    )
    report = check_dashboard_html(html, set())
    assert not any("JS syntax error" in error for error in report.errors)


def test_js_external_script_with_src_skipped_from_syntax_check() -> None:
    html = (
        '<html><head><script src="'
        + ALLOWED_SCRIPT_SRC_PREFIXES[0]
        + '">this is not even valid js {{{</script></head>'
        '<body><div id="chart"></div></body></html>'
    )
    report = check_dashboard_html(html, set())
    assert not any("JS syntax error" in error for error in report.errors)


def test_js_syntax_check_skipped_gracefully_when_quickjs_unavailable(monkeypatch) -> None:
    """比照 Java 端 JsSyntaxValidator 的哲學:驗證器依賴在 runtime 不可用時記錄後跳過,
    不擋主流程——即使腳本內容本身有語法錯誤,quickjs 不可用時該規則不報錯。"""
    from app.engine.html_guard import js_runtime

    monkeypatch.setattr(js_runtime, "QUICKJS_AVAILABLE", False)
    html = (
        '<html><head></head><body><div id="chart"></div>'
        "<script>function broken() { const a = 1;</script>"
        "</body></html>"
    )
    report = check_dashboard_html(html, set())
    assert not any("JS syntax error" in error for error in report.errors)


# -- quickjs sandboxed execution smoke (Level 2) -------------------------------------------
#
# 真實慘案:模型寫 `timeout.columns`(忘了先 `const timeout = window.__ERD_RESULTS__['q12']`
# 宣告)→ ReferenceError 殺掉整頁所有圖表,parse-only 檢查(Level 1)完全看不到,連續多輪
# 迭代都是死頁。這裡驗證 Level 2 真的執行(不只 parse)每段 inline script,能抓到這類
# runtime 錯誤,同時不能對正常 dashboard JS 產生假陽性(examples corpus 是最強哨兵)。


def test_execution_smoke_undeclared_variable_reference_caught() -> None:
    """真陽性:忘了先宣告就取屬性(逐字對應真實慘案的 `timeout.columns` 寫法)。訊息格式為
    `Line N: ReferenceError 'X' is not defined`,N 為 HTML 絕對行號(此例整份 HTML 在同一行,
    script 內容緊接在 `<script>` 之後,故行號為 1)。"""
    html = (
        '<html><head></head><body><div id="chart"></div>'
        "<script>const total = timeout.columns.length;</script>"
        "</body></html>"
    )
    report = check_dashboard_html(html, set())
    assert not report.ok
    assert any(
        error == "Line 1: ReferenceError 'timeout' is not defined" for error in report.errors
    ), report.errors


def test_execution_smoke_reference_to_missing_query_id_caught() -> None:
    """真陽性:引用 available_query_ids 之外的 id,對 undefined 取 `.columns` → TypeError。

    刻意用字串串接組出動態 key(`'q' + '9' + '9'`),避免被 `_check_referenced_query_ids`
    的字面值 regex 一併攔下——確保這個案例是真的在測 Level 2 本身的偵測力,不是撞到既有
    的靜態引用檢查。
    """
    html = (
        '<html><head></head><body><div id="chart"></div>'
        "<script>const key = 'q' + '9' + '9'; "
        "const result = window.__ERD_RESULTS__[key]; "
        "const total = result.columns.length;</script>"
        "</body></html>"
    )
    report = check_dashboard_html(html, {"q1"})
    assert not report.ok
    assert any(error.startswith("Line 1: TypeError") for error in report.errors), report.errors


# -- sandbox seeding must match what production actually injects (referenced_query_ids, not
# available_query_ids) -----------------------------------------------------------------------
#
# 真實慘案:production(`app/main.py`)只把 `referenced_query_ids(report.html)`(字面值 regex,
# 見 `app/engine/results.py`)配到的子集注入最終 HTML,但 sandbox 過去是拿全部
# `available_query_ids` 灌資料——對 dot-access(`__ERD_RESULTS__.q2`)或動態 key
# (`__ERD_RESULTS__[key]`)這兩種 regex 抓不到的寫法,sandbox 灌了資料所以測不出錯,guard
# 放行,但出貨的 HTML 因為只注入字面值比對到的子集,實際上完全沒有那個 query 的資料。


def test_dot_access_result_reference_not_production_injected_reports_runtime_error() -> None:
    """`window.__ERD_RESULTS__.q2`(dot access)不會被 `referenced_query_ids` 的字面值 regex
    匹配到,production 因此不會把 q2 的真實資料注入最終 HTML——即使 q2 確實存在於
    `available_query_ids` 與 `results` 裡。sandbox 灌資料的子集 MUST 和 production 一致,
    這裡才會如實炸出 TypeError,而不是因為 sandbox 多灌了資料而放行一份出貨後其實缺資料的
    dashboard。"""
    html = (
        '<html><head></head><body><div id="chart"></div>'
        "<script>const total = window.__ERD_RESULTS__.q2.columns.length;</script>"
        "</body></html>"
    )
    results = {"q2": {"columns": ["a", "b"], "rows": [["x", 1]], "truncated": False}}
    report = check_dashboard_html(html, {"q2"}, results)
    assert not report.ok
    assert any("TypeError" in error for error in report.errors), report.errors


def test_dynamic_key_result_reference_not_production_injected_reports_runtime_error() -> None:
    """同上,但用動態拼接的 key(`window.__ERD_RESULTS__[key]`)——同樣不會被字面值 regex
    匹配,production 同樣不會注入這個 query 的資料,sandbox 也 MUST 不灌。"""
    html = (
        '<html><head></head><body><div id="chart"></div>'
        "<script>const key = 'q' + '2'; "
        "const total = window.__ERD_RESULTS__[key].columns.length;</script>"
        "</body></html>"
    )
    results = {"q2": {"columns": ["a", "b"], "rows": [["x", 1]], "truncated": False}}
    report = check_dashboard_html(html, {"q2"}, results)
    assert not report.ok
    assert any("TypeError" in error for error in report.errors), report.errors


def test_literal_bracket_access_result_reference_still_seeded_and_passes() -> None:
    """回歸保證:一般的字面值中括號存取(`window.__ERD_RESULTS__['q2']`)正是
    `referenced_query_ids` 抓得到的寫法,和 production 實際注入的子集一致——sandbox 仍要灌真實
    資料,正常通過,不能被上面兩條修法誤傷。"""
    html = (
        '<html><head></head><body><div id="chart"></div>'
        "<script>const total = window.__ERD_RESULTS__['q2'].columns.length;</script>"
        "</body></html>"
    )
    results = {"q2": {"columns": ["a", "b"], "rows": [["x", 1]], "truncated": False}}
    report = check_dashboard_html(html, {"q2"}, results)
    assert report.ok, report.errors


def test_execution_smoke_multiple_undeclared_variables_in_one_block_all_reported() -> None:
    """multi-mole 掃描:同一個 script block 內 3 個未宣告變數必須一次全報,不是打一隻就
    停——修法前的舊行為遇到第一個 ReferenceError 就不再往下執行,後面兩隻地鼠永遠看不到。
    行號逐行數過:script 內容緊接在 `<script>` 之後(HTML 第 1 行),故 script 內第 N 行
    就是 HTML 第 N 行。"""
    html = (
        '<html><head></head><body><div id="chart"></div><script>\n'
        "const a = AAA.foo;\n"
        "const b = BBB.bar;\n"
        "const c = CCC.baz;\n"
        "</script></body></html>"
    )
    report = check_dashboard_html(html, set())
    assert not report.ok
    assert report.errors == [
        "Line 2: ReferenceError 'AAA' is not defined",
        "Line 3: ReferenceError 'BBB' is not defined",
        "Line 4: ReferenceError 'CCC' is not defined",
    ]


def test_execution_smoke_reference_error_followed_by_type_error_both_reported() -> None:
    """一個 block 內先撞到 ReferenceError(未宣告變數,會被 stub 後重試)、重試後又撞到
    TypeError(非 ReferenceError,不重試)——兩者都要各自報一條,行號各自正確。"""
    html = (
        '<html><head></head><body><div id="chart"></div><script>\n'
        "const a = UNDECLARED_VAR.foo;\n"
        "const nothing = null;\n"
        "const b = nothing.bar;\n"
        "</script></body></html>"
    )
    report = check_dashboard_html(html, set())
    assert not report.ok
    assert any(
        error == "Line 2: ReferenceError 'UNDECLARED_VAR' is not defined" for error in report.errors
    ), report.errors
    assert any(error.startswith("Line 4: TypeError") for error in report.errors), report.errors


# -- shared helper call site attribution ---------------------------------------------------
#
# 真實慘案:guard 只報拋出點,而 getCol 是 skill 強制每份 dashboard 都要有的共用 helper
# ——全檔任何一次欄位解析失敗都塌縮到 helper 內同一行,模型拿不到「哪個綁定錯了」的資訊,
# 只能在 qN 之間亂猜。這裡驗證錯誤訊息改報呼叫點,並列出該 helper 的全部呼叫點,讓模型
# 一輪修完。


def test_error_inside_shared_helper_reports_call_site_and_all_call_sites() -> None:
    """共用 helper 內拋的例外 MUST 報呼叫點行號,並列出該 helper 的全部呼叫點——否則全檔的
    欄位解析失敗都塌縮到 helper 那一行,模型只能猜。"""
    html = (
        '<html><head><script src="' + ALLOWED_SCRIPT_SRC_PREFIXES[0] + '"></script></head>'
        '<body><div id="chart"></div>\n'
        "<script>\n"
        "function getCol(columns, candidate) {\n"
        "  return columns.indexOf(candidate);\n"
        "}\n"
        "const first = window.__ERD_RESULTS__['q1'].rows;\n"
        "const firstIndex = getCol(first.columns, 'a');\n"
        "const secondIndex = getCol(first.columns, 'b');\n"
        "const chart = echarts.init(document.getElementById('chart'), 'erd');\n"
        "chart.setOption({ tooltip: {}, series: [] });\n"
        "</script></body></html>"
    )
    results = {"q1": {"columns": ["a", "b"], "rows": [["x", 1]], "truncated": False}}
    report = check_dashboard_html(html, {"q1"}, results)

    assert not report.ok, report.errors
    type_errors = [error for error in report.errors if "TypeError" in error]
    assert type_errors, report.errors
    # 呼叫點是第 7 行(`const firstIndex = getCol(...)`,第一個 getCol 呼叫),不是 helper
    # 內第 4 行(`return columns.indexOf(candidate);`)——已用 quickjs 實際跑過核對。
    assert "Line 7:" in type_errors[0], type_errors
    assert "getCol" in type_errors[0], type_errors
    # 同一個 helper 的另一個呼叫點(第 8 行)也要一併列出,讓模型一輪修完。
    assert "8" in type_errors[0], type_errors


def test_execution_smoke_cross_block_declaration_not_false_positive() -> None:
    """跨 block:block1 宣告的變數,block2 正常使用時不該被誤判成未宣告——重建 context
    時必須把 block1 靜默重放進去,不能只 stub 當下 block 自己拋出的那個變數名。"""
    html = (
        '<html><head></head><body><div id="chart"></div>'
        "<script>const sharedValue = 42;</script>"
        "<script>const total = sharedValue + 1;</script>"
        "</body></html>"
    )
    report = check_dashboard_html(html, {"q1"})
    assert report.ok, report.errors


def test_execution_smoke_cross_block_own_undeclared_variable_reported() -> None:
    """跨 block 的另一半:block2 讀 block1 宣告的變數沒問題,但 block2 自己另外引用的
    未宣告變數依然要照報,不能因為重放了 block1 就整段被吞掉。"""
    html = (
        '<html><head></head><body><div id="chart"></div>'
        "<script>const sharedValue = 42;</script>"
        "<script>\n"
        "const total = sharedValue + 1;\n"
        "const other = YUNDECLARED.qux;\n"
        "</script>"
        "</body></html>"
    )
    report = check_dashboard_html(html, set())
    assert not report.ok
    assert any(
        error == "Line 3: ReferenceError 'YUNDECLARED' is not defined" for error in report.errors
    ), report.errors


def test_execution_smoke_normal_dashboard_js_has_zero_false_positives() -> None:
    """假陰性防護:一段涵蓋常見 dashboard JS 手法(getElementById、echarts.init/setOption、
    DOMContentLoaded 包裹、resize listener、正常讀 __ERD_RESULTS__、getCol/indexOf、
    innerHTML 賦值、Number()/toFixed())的「正常」腳本必須零錯誤。"""
    html = (
        '<html><head></head><body><div id="chart"></div><div id="total"></div>'
        "<script>"
        "function getCol(columns, name) { return columns.indexOf(name); }"
        "document.addEventListener('DOMContentLoaded', function () {"
        "  const result = window.__ERD_RESULTS__['q1'];"
        "  const valueIndex = getCol(result.columns, '__c1');"
        "  const values = result.rows.map(function (row) { return row[valueIndex]; });"
        "  const total = values.reduce(function (a, b) { return a + b; }, 0);"
        "  document.getElementById('total').innerHTML = Number(total).toFixed(2);"
        "  const chart = echarts.init(document.getElementById('chart'), 'erd');"
        "  chart.setOption({ tooltip: { trigger: 'axis' }, series: [{ type: 'bar', data: values }] });"
        "  window.addEventListener('resize', function () { chart.resize(); });"
        "});"
        "</script>"
        "</body></html>"
    )
    report = check_dashboard_html(html, {"q1"})
    assert report.ok, report.errors


def test_execution_smoke_infinite_loop_times_out_without_hanging() -> None:
    html = (
        '<html><head></head><body><div id="chart"></div>'
        "<script>while (true) {}</script>"
        "</body></html>"
    )
    report = check_dashboard_html(html, set())
    assert not report.ok
    assert any("script#0" in error and "timed out" in error for error in report.errors)


# -- sandbox memory limit and global wall-clock deadline (Level 2 hardening) ----------------
#
# 真實慘案:`const a=[]; for(;;){ a.push(new Array(100000).fill(7)); }` 這種無界配置迴圈,只靠
# `set_time_limit` 撐不住——實測跑到 4025MB peak RSS、8.39s 才被時間上限攔下。在有記憶體限制
# 的容器裡,這會直接 OOM-kill 整個 process,拖垮所有並發 session。另外,ReferenceError 重試
# 迴圈每次都重建 context、重放前面所有 block(見模組上方對 `_execute_scripts_smoke` 的說明),
# 單一 block 內反覆觸發最多 `_MAX_REFERENCE_ERROR_RETRIES_PER_BLOCK` 次重建,總耗時沒有上限
# ——實測一份病態輸入跑到 56.93s。這裡驗證兩道防線:sandbox context 有 memory limit(讓失控
# 配置快速丟 out-of-memory,而不是撐爆記憶體),以及跨整個 `_execute_scripts_smoke` 呼叫的
# 全域 wall-clock deadline(超時就記 warning、優雅降級回傳目前已收集到的結果,不擋主流程,
# 也不 raise)。


def test_sandbox_memory_limit_aborts_unbounded_allocation_quickly() -> None:
    """沒有 memory limit 時,這段無窮迴圈只能靠 `set_time_limit` 攔,實測仍會先撐爆記憶體、
    花好幾秒才被攔下。加了 memory limit 後,quickjs 應該在遠低於這個量級的時間內自己丟出
    out-of-memory 例外,guard 把它當一般執行期錯誤處理、不阻塞主流程。用「總耗時遠低於已知的
    無記憶體上限耗時」當斷言,避免測試依賴精確的例外訊息文字。"""
    html = (
        '<html><head></head><body><div id="chart"></div>'
        "<script>const buffer = []; for (;;) { buffer.push(new Array(100000).fill(7)); }</script>"
        "</body></html>"
    )
    start_time = time.monotonic()
    report = check_dashboard_html(html, set())
    elapsed_seconds = time.monotonic() - start_time

    assert not report.ok, report.errors
    assert elapsed_seconds < 1.5, elapsed_seconds


def test_global_deadline_stops_further_execution_gracefully_without_raising(monkeypatch) -> None:
    """把全域 deadline 常數 monkeypatch 成一個保證「呼叫當下就已經算超時」的負值,模擬「時間已經
    用完」的狀態——即使腳本本身完全正常、幾乎瞬間就能跑完,guard MUST 直接放棄後續 Level 2
    執行(不 raise、不掛住),優雅降級。用一段正常情況下會被 Level 2 抓到的裸 ReferenceError
    當探針:對照組(不 monkeypatch)必須抓到;deadline 生效組必須抓不到(因為根本沒被執行到)
    ——證明 deadline 真的有在守門,不是裝飾用的常數。"""
    from app.engine.html_guard.sandbox import context as sandbox_context

    html = (
        '<html><head></head><body><div id="chart"></div>'
        "<script>const total = timeout.columns.length;</script>"
        "</body></html>"
    )

    baseline_report = check_dashboard_html(html, set())
    assert any(
        "ReferenceError 'timeout' is not defined" in error for error in baseline_report.errors
    ), baseline_report.errors

    monkeypatch.setattr(sandbox_context, "_SANDBOX_GLOBAL_DEADLINE_SECONDS", -1.0)
    degraded_report = check_dashboard_html(html, set())
    assert not any(
        "ReferenceError 'timeout' is not defined" in error for error in degraded_report.errors
    ), degraded_report.errors


def test_execution_smoke_skipped_when_syntax_check_already_failed() -> None:
    """Level 1 語法已錯時,Level 2 不該再對同一段壞掉的 script 硬執行。"""
    html = (
        '<html><head></head><body><div id="chart"></div>'
        "<script>function broken() { const a = 1;</script>"
        "</body></html>"
    )
    report = check_dashboard_html(html, set())
    assert not report.ok
    assert not any(
        "execution error" in error or "execution timed out" in error for error in report.errors
    )


def test_execution_smoke_skipped_gracefully_when_quickjs_unavailable(monkeypatch) -> None:
    from app.engine.html_guard import js_runtime

    monkeypatch.setattr(js_runtime, "QUICKJS_AVAILABLE", False)
    html = (
        '<html><head></head><body><div id="chart"></div>'
        "<script>const total = timeout.columns.length;</script>"
        "</body></html>"
    )
    report = check_dashboard_html(html, set())
    assert not any(
        "execution error" in error or "execution timed out" in error for error in report.errors
    )


# -- real results seeding (production-equivalent sandbox data) ----------------------------
#
# Level 2's sandbox used to always seed `window.__ERD_RESULTS__` with generic fake columns
# (`__c0`/`__c1`). Any dashboard code that gates chart initialization behind a real-column-name
# lookup (`getCol(columns, 'department', ...)`, the skill's own documented pattern) then never
# finds a match, the `if (idx >= 0) { ... }` gate never opens, and whatever bug lives behind it
# -- including a swallowed chart error -- is never reached. `check_dashboard_html`'s optional
# `results` parameter (same shape as `load_all_results`) lets the guard seed real columns/rows
# instead, so these gates open the same way they would in the browser.


def test_results_seeding_opens_real_column_name_gate() -> None:
    """Without `results`, `getCol` never matches a real column name against the generic fake
    columns, the gate stays closed, and the ReferenceError inside never fires. With `results`
    supplying the real column name, the gate opens and the bug is caught."""
    html = (
        '<html><head></head><body><div id="chart"></div>'
        "<script>"
        "function getCol(columns, name) { return columns.indexOf(name); }"
        "const result = window.__ERD_RESULTS__['q1'];"
        "const nameIdx = getCol(result.columns, 'department');"
        "if (nameIdx >= 0) {"
        "  const total = leakedScopeVar.foo;"
        "}"
        "</script>"
        "</body></html>"
    )
    results = {
        "q1": {
            "columns": ["department", "sessions"],
            "rows": [["Engineering", 10]],
            "truncated": False,
        }
    }

    report_without_results = check_dashboard_html(html, {"q1"})
    assert report_without_results.ok, report_without_results.errors

    report_with_results = check_dashboard_html(html, {"q1"}, results)
    assert not report_with_results.ok
    assert any("leakedScopeVar" in error for error in report_with_results.errors), (
        report_with_results.errors
    )


def test_results_seeding_missing_query_id_falls_back_to_generic_fake_data() -> None:
    """Defensive path: `results` provided but a particular `available_query_id` isn't in it
    (shouldn't normally happen) -- that query_id falls back to the old generic fake columns
    instead of crashing the sandbox build."""
    html = (
        '<html><head></head><body><div id="chart"></div>'
        "<script>const result = window.__ERD_RESULTS__['q1']; "
        "const total = result.rows.length;</script>"
        "</body></html>"
    )
    report = check_dashboard_html(html, {"q1"}, results={})
    assert report.ok, report.errors


# -- DOM id fidelity (getElementById/querySelector null-for-missing-id) --------------------
#
# 真實慘案(3-tab 穩定性戰役 Phase 2,mod-m1.html):修改輪重排 KPI 卡片時刪掉了
# `kpi-failures` 的 `<div>`,但留著 `document.getElementById('kpi-failures').textContent =
# ...`。真瀏覽器裡 `getElementById` 對不存在的 id 回 `null`,對 `null.textContent` 賦值直接
# TypeError,整個 DOMContentLoaded handler 死掉、全頁圖表零蛋——但 sandbox 舊版的
# `getElementById` 是 absorb-all stub,永遠回一個吸收一切的 Proxy,不可能回傳 `null`,這類
# 「引用不存在的 DOM id」錯誤在舊 sandbox 裡物理上不可能重現,guard 連續多輪都放行。


def test_get_element_by_id_returns_absorb_for_known_id() -> None:
    """id 確實存在於 markup 裡時,行為維持原樣(absorb 元素,`.textContent = ...` 等賦值
    安全通過)——這是既有 corpus 假陽性哨兵仰賴的行為,不能被 id 擬真改壞。"""
    html = (
        '<html><head></head><body><div id="marker"></div>'
        "<script>document.getElementById('marker').textContent = 'ok';</script>"
        "</body></html>"
    )
    report = check_dashboard_html(html, set())
    assert report.ok, report.errors


def test_get_element_by_id_missing_id_returns_null_and_throws() -> None:
    """id 在整份 HTML 裡完全不存在(對應真實案例:刪掉卡片但留下殘留引用)時,
    `getElementById` 回 `null`,對 `null.textContent` 賦值必須如實拋出 TypeError,被 Level 2
    的未捕捉例外偵測抓到。"""
    html = (
        '<html><head></head><body><div id="chart"></div>'
        "<script>const noop = 0; document.getElementById('kpi-failures').textContent = '0';</script>"
        "</body></html>"
    )
    report = check_dashboard_html(html, set())
    assert not report.ok
    assert any("TypeError" in error and "Line 1" in error for error in report.errors), report.errors


def test_query_selector_simple_id_form_matches_get_element_by_id_semantics() -> None:
    """`document.querySelector('#id')`(單一 id 選擇器形式)比照 `getElementById` 處理:
    id 不存在時一樣回 `null`,一樣如實 TypeError。"""
    html = (
        '<html><head></head><body><div id="chart"></div>'
        "<script>document.querySelector('#kpi-failures').textContent = '0';</script>"
        "</body></html>"
    )
    report = check_dashboard_html(html, set())
    assert not report.ok
    assert any("TypeError" in error for error in report.errors), report.errors


def test_query_selector_complex_selector_falls_back_to_absorb() -> None:
    """`querySelector` 不是完整的 CSS selector engine——只有單一 `#id` 形式才比照
    `getElementById` 做 id 擬真;複雜選擇器(這裡用 `[role=tab]`,屬性選擇器)維持回傳
    absorb 元素,不該被誤判成「找不到元素」而 TypeError(這是既有 tab 範本
    `document.querySelectorAll('[role=tab]')`/單數版本的合理使用情境,不該被誤傷)。"""
    html = (
        '<html><head></head><body><div id="chart"></div>'
        "<script>document.querySelector('[role=tab]').textContent = 'ok';</script>"
        "</body></html>"
    )
    report = check_dashboard_html(html, set())
    assert report.ok, report.errors


def test_get_element_by_id_dynamic_id_string_still_matches_when_literal_exists() -> None:
    """動態拼接的 id 字串(`'row-' + index`,skill 的 tab 範本用同樣手法拼 `'panel-' +
    index`,這裡換個不會誤觸 tab 結構規則的 id 前綴)不需要 Python 端靜態解析——只要拼接後
    的字串字面值本身作為某個真實元素的 `id="row-0"` 存在於 markup,sandbox 執行期的
    `Set.has(拼好的字串)` 就會命中,不會被誤判成「id 不存在」。"""
    html = (
        '<html><head></head><body><div id="chart"></div><div id="row-0"></div>'
        "<script>"
        "const index = 0;"
        "document.getElementById('row-' + index).classList.add('active');"
        "</script>"
        "</body></html>"
    )
    report = check_dashboard_html(html, set())
    assert report.ok, report.errors


def test_campaign_fixture_m1_missing_dom_id_caught() -> None:
    """Production-equivalent regression fixture from 3-tab stability campaign Phase 2
    (mod-m1.html): a repair round reshuffled the KPI cards, deleted the `kpi-failures`
    `<div>`, but left `document.getElementById('kpi-failures').textContent = ...` unprotected
    (no try/catch -- this is top-level KPI card code, not a chart's try/catch-wrapped
    init+setOption). Fed to the guard the same way `app/main.py` does: pre-injection HTML
    (`strip_injected_blocks`) with real `results` parsed straight out of the fixture's own
    injected `erd-results-data` script. Must be caught as a TypeError with a line number --
    before this fix, 5 consecutive guard rounds passed this HTML because `getElementById`
    could never return `null` in the old absorb-all sandbox."""
    shipped_html = (FIXTURES_DIR / "campaign-m1-missing-dom-id.html").read_text(encoding="utf-8")
    real_results = _parse_injected_results(shipped_html)
    clean_html = strip_injected_blocks(shipped_html)
    available_query_ids = referenced_query_ids(clean_html)

    report = check_dashboard_html(clean_html, available_query_ids, real_results)

    assert not report.ok
    assert any("TypeError" in error and "Line " in error for error in report.errors), report.errors


# -- tab convention checks ----------------------------------------------------------------
#
# 真實慘案:模型產出用藥丸/segmented 樣式的 tab(灰底圓角＋白色 active 藥丸),偏離 skill
# 規定的 Tabler 底線式範本;另外歷史上也出過「自寫 showTab 忘了 resize dispatch → 切 tab
# 後圖表空白直到視窗 resize」的案例。兩者都沒有既有 guard 攔住。


def test_no_tab_structure_triggers_no_tab_rules() -> None:
    """VALID_HTML 沒有任何 tab 結構——兩條 tab 規則都不該觸發(回歸保證,避免誤殺無 tab
    的一般 dashboard)。"""
    report = check_dashboard_html(VALID_HTML, {"q1"})
    assert report.ok
    assert not any("resize" in error or "Tab styling" in error for error in report.errors)


def test_tab_structure_with_all_conventions_passes() -> None:
    html = (
        "<html><head></head><body>"
        '<nav role="tablist"><button onclick="showTab(0)" id="tab-0" role="tab" '
        'class="border-b-2 border-blue-600">Tab 1</button></nav>'
        '<div id="panel-0"></div>'
        "<script>"
        "function showTab(idx) { window.dispatchEvent(new Event('resize')); }"
        "</script>"
        "</body></html>"
    )
    report = check_dashboard_html(html, set())
    assert report.ok, report.errors


def test_tab_structure_missing_resize_dispatch_fails() -> None:
    html = (
        "<html><head></head><body>"
        '<nav role="tablist"><button onclick="showTab(0)" id="tab-0" role="tab" '
        'class="border-b-2 border-blue-600">Tab 1</button></nav>'
        '<div id="panel-0"></div>'
        "<script>"
        "function showTab(idx) { document.getElementById('panel-0').classList.remove('hidden'); }"
        "</script>"
        "</body></html>"
    )
    report = check_dashboard_html(html, set())
    assert not report.ok
    assert any("dispatchEvent(new Event('resize'))" in error for error in report.errors)


# -- broadened tab detection: model-named switchers, resize dispatch inside the function ---
#
# 真實慘案:模型自寫的 `switchTab()`(不叫 `showTab`)完全躲過既有的三個 marker
# (`showTab(`/`id="panel-0"`/`role="tab"`),而且就算 marker 命中,resize 片語只要整份
# HTML 任一處出現就算過——只在別處的 `window.resize` listener 裡呼叫 `chart.resize()`
# 也會誤放,救不了 hidden panel 的 0 寬容器。下面每條測試刻意避開既有三個 marker(不用
# `panel-0`/`showTab`/`role="tab"`),確保驗證的是**新**訊號本身,不是意外撞到舊的。


def test_onclick_named_tab_switcher_is_detected_even_without_old_markers() -> None:
    """`onclick="...Tab("` 命名慣例本身就要能觸發 tab 結構偵測——這裡刻意用 `view-N`
    (不是 `panel-N`)當容器 id,resize 派發正確寫在切換函式體內,MUST 直接放行(證明偵測
    到了,而不是誤殺)。"""
    html = (
        '<html><head><script src="' + ALLOWED_SCRIPT_SRC_PREFIXES[0] + '"></script></head>'
        '<body><button onclick="switchTab(1)" class="border-b-2">Tab 2</button>'
        '<div id="view-0"></div><div id="view-1"></div><div id="chart"></div>'
        "<script>const data = window.__ERD_RESULTS__['q1'];\n"
        "const chart = echarts.init(document.getElementById('chart'), 'erd');\n"
        "chart.setOption({ tooltip: {}, series: [] });\n"
        "function switchTab(index) {\n"
        "  document.getElementById('view-' + index).classList.remove('hidden');\n"
        "  chart.resize();\n"
        "}\n"
        "</script></body></html>"
    )
    report = check_dashboard_html(html, {"q1"})

    assert report.ok, report.errors


def test_onclick_named_tab_switcher_without_resize_dispatch_fails() -> None:
    """同一個 `onclick="...Tab("` 命名慣例,但切換函式只切 CSS class、完全不派發
    resize——hidden panel 裡的 ECharts 會永遠停在 100px fallback。"""
    html = (
        '<html><head><script src="' + ALLOWED_SCRIPT_SRC_PREFIXES[0] + '"></script></head>'
        '<body><button onclick="switchTab(1)" class="border-b-2">Tab 2</button>'
        '<div id="view-0"></div><div id="view-1"></div><div id="chart"></div>'
        "<script>const data = window.__ERD_RESULTS__['q1'];\n"
        "function switchTab(index) {\n"
        "  document.getElementById('view-' + index).classList.remove('hidden');\n"
        "}\n"
        "const chart = echarts.init(document.getElementById('chart'), 'erd');\n"
        "chart.setOption({ tooltip: {}, series: [] });\n"
        "window.addEventListener('resize', function () { chart.resize(); });\n"
        "</script></body></html>"
    )
    report = check_dashboard_html(html, {"q1"})

    assert not report.ok
    assert any("resize" in error for error in report.errors), report.errors


def test_resize_dispatch_outside_the_switch_function_body_still_fails() -> None:
    """真實慘案的確切情境:resize 片語確實出現在檔案裡,但寫在切換函式體外(這裡是模組層級
    的 `window.addEventListener`)——舊檢查只做「整份 HTML 有沒有這個子字串」,救不了
    hidden panel 的 0 寬容器,MUST 被新規則(resize 須在函式體內)攔下。"""
    html = (
        '<html><head><script src="' + ALLOWED_SCRIPT_SRC_PREFIXES[0] + '"></script></head>'
        '<body><button onclick="switchTab(1)" class="border-b-2">Tab 2</button>'
        '<div id="view-0"></div><div id="view-1"></div><div id="chart"></div>'
        "<script>const data = window.__ERD_RESULTS__['q1'];\n"
        "function switchTab(index) {\n"
        "  document.getElementById('view-' + index).classList.remove('hidden');\n"
        "}\n"
        "const chart = echarts.init(document.getElementById('chart'), 'erd');\n"
        "chart.setOption({ tooltip: {}, series: [] });\n"
        "window.dispatchEvent(new Event('resize'));\n"
        "</script></body></html>"
    )
    report = check_dashboard_html(html, {"q1"})

    assert not report.ok
    assert any("resize" in error for error in report.errors), report.errors


def test_multiple_panel_number_containers_alone_trigger_tab_detection() -> None:
    """兩個以上 `id="panel-N"`(N >= 1,不含舊 marker 精確比對的 `panel-0`)容器本身就要
    觸發 tab 結構偵測——切換函式刻意不叫 `*Tab`(`toggleView`),所以 resize 檢查會退回
    整份 HTML 掃描;完全沒有 resize 片語時 MUST 退貨。"""
    html = (
        '<html><head><script src="' + ALLOWED_SCRIPT_SRC_PREFIXES[0] + '"></script></head>'
        '<body><button onclick="toggleView(1)" class="border-b-2">View 2</button>'
        '<div id="panel-1"></div><div id="panel-2"></div><div id="chart"></div>'
        "<script>const data = window.__ERD_RESULTS__['q1'];\n"
        "function toggleView(index) {\n"
        "  document.getElementById('panel-' + index).classList.remove('hidden');\n"
        "}\n"
        "const chart = echarts.init(document.getElementById('chart'), 'erd');\n"
        "chart.setOption({ tooltip: {}, series: [] });\n"
        "</script></body></html>"
    )
    report = check_dashboard_html(html, {"q1"})

    assert not report.ok
    assert any("resize" in error for error in report.errors), report.errors


def test_single_panel_id_without_other_tab_signals_is_not_tab_structure() -> None:
    """單一個非 `panel-0` 的 `panel-N` id(這裡是 `panel-7`,一般 dashboard 拿來當某張卡片
    的 id,和 tab 切換無關)不該被 2+ panel 容器規則誤判——負向控制,避免廣化偵測誤殺
    無 tab 的一般 dashboard。"""
    html = (
        '<html><head><script src="' + ALLOWED_SCRIPT_SRC_PREFIXES[0] + '"></script></head>'
        '<body><div id="panel-7"></div><div id="chart"></div>'
        "<script>const data = window.__ERD_RESULTS__['q1'];\n"
        "const chart = echarts.init(document.getElementById('chart'), 'erd');\n"
        "chart.setOption({ tooltip: {}, series: [] });\n"
        "</script></body></html>"
    )
    report = check_dashboard_html(html, {"q1"})

    assert report.ok, report.errors


# -- tab switcher candidate selection: prefer onclick-wired functions over name matching ----
#
# 真實慘案:一個完全無關、恰好也叫 `*Tab` 的 helper(例如 `renderTab`)存在時,舊邏輯只要
# 命中 `function \w*Tab(` 就把「函式體掃描」的整個 fallback 鎖死在那個無關函式上,連真正的
# 切換函式(可能叫別的名字、也可能寫成箭頭函式賦值)都不會被掃到——即使真正的切換函式本身
# 完全正確(resize 有派發),整份 dashboard 還是被誤判退貨。


def test_unrelated_tab_named_helper_does_not_block_the_real_onclick_wired_switcher() -> None:
    """`renderTab` 是完全無關的具名函式(不含 resize、也沒被 onclick 呼叫);真正的切換器是
    箭頭函式賦值的 `showTab`,由 `onclick="showTab(0)"` 呼叫、函式體內正確派發 resize。
    onclick 綁定訊號 MUST 優先於單純的 `*Tab` 命名比對,MUST 放行。"""
    html = (
        '<html><head><script src="' + ALLOWED_SCRIPT_SRC_PREFIXES[0] + '"></script></head>'
        '<body><button onclick="showTab(0)" class="border-b-2">Tab 1</button>'
        '<div id="panel-0"></div><div id="panel-1"></div>'
        "<script>\n"
        "function renderTab() { return 1; }\n"
        "const showTab = (index) => {\n"
        "  document.getElementById('panel-' + index).classList.remove('hidden');\n"
        "  window.dispatchEvent(new Event('resize'));\n"
        "};\n"
        "</script></body></html>"
    )
    report = check_dashboard_html(html, set())

    assert report.ok, report.errors


def test_onclick_wired_switcher_without_resize_still_fails_despite_unrelated_tab_helper_with_resize() -> (
    None
):
    """真正的切換器 `switchTab` 由 onclick 呼叫、函式體內完全沒有 resize;旁邊有個無關的
    `helperTab`,函式體內恰好含 resize 片語但從未被 onclick 呼叫。onclick 綁定訊號 MUST
    決定用哪個函式體做檢查,不能被無關函式的 resize 片語矇混過關。"""
    html = (
        '<html><head><script src="' + ALLOWED_SCRIPT_SRC_PREFIXES[0] + '"></script></head>'
        '<body><button onclick="switchTab(1)" class="border-b-2">Tab 2</button>'
        '<div id="view-0"></div><div id="view-1"></div>'
        "<script>\n"
        "function switchTab(index) {\n"
        "  document.getElementById('view-' + index).classList.remove('hidden');\n"
        "}\n"
        "function helperTab() {\n"
        "  window.dispatchEvent(new Event('resize'));\n"
        "}\n"
        "</script></body></html>"
    )
    report = check_dashboard_html(html, set())

    assert not report.ok
    assert any("resize" in error for error in report.errors), report.errors


# -- resize dispatch match tolerates double quotes and incidental whitespace ---------------


def test_resize_dispatch_with_double_quotes_and_whitespace_passes() -> None:
    """`new Event("resize")`(雙引號)與呼叫間的多餘空白都要能被接受——只有比對邏輯是
    死板的單引號字面值比對時才會誤殺這些等價寫法。"""
    html = (
        "<html><head></head><body>"
        '<nav role="tablist"><button onclick="showTab(0)" id="tab-0" role="tab" '
        'class="border-b-2 border-blue-600">Tab 1</button></nav>'
        '<div id="panel-0"></div>'
        "<script>"
        'function showTab(idx) { window.dispatchEvent( new Event( "resize" ) ); }'
        "</script>"
        "</body></html>"
    )
    report = check_dashboard_html(html, set())

    assert report.ok, report.errors


# -- swallowed chart error detection (console.error collection) --------------------------
#
# 真實慘案(3-tab 穩定性戰役實測):skill 規定每張圖的 init+setOption 包 try/catch、catch 裡
# `console.error('[ERD] chart <名稱> failed:', error)`——這條防線隔離了「一張圖壞不能拖垮
# 全頁」,但副作用是把 ReferenceError 整個吞掉,Level 2 的未捕捉例外偵測完全看不到,曾放行過
# 含 3 張空白圖表的 dashboard 出貨。這裡驗證 sandbox 的 console.error 收集器能把這類「被
# try/catch 擋下的執行期錯誤」轉成 guard error。


def test_swallowed_chart_error_caught_via_console_error_collection() -> None:
    """一張圖的 init+setOption 包在 try/catch 裡,catch 裡呼叫
    `console.error('[ERD] chart <名稱> failed:', error)`——這個 ReferenceError 不會冒出
    quickjs.JSException(被 JS 自己的 try/catch 擋下),必須靠 console.error 收集器才抓得到。
    guard error 訊息要同時帶出 chart 名稱與底層錯誤訊息。"""
    html = (
        '<html><head></head><body><div id="chart"></div>'
        "<script>"
        "try { const total = undeclaredVar.foo; } "
        "catch (error) { console.error('[ERD] chart my-chart failed:', error); }"
        "</script>"
        "</body></html>"
    )
    report = check_dashboard_html(html, set())
    assert not report.ok
    assert any(
        "Chart 'my-chart' threw at runtime (caught by its try/catch)" in error
        and "undeclaredVar" in error
        and "is not defined" in error
        for error in report.errors
    ), report.errors


def test_swallowed_chart_error_message_tells_model_try_catch_is_not_a_fix() -> None:
    """guard error 訊息必須明講「try/catch 是損害管制,不是修法」,不然模型下一輪修復容易
    誤以為訊息本身就是要它「補一個 try/catch」(這裡本來就有)。"""
    html = (
        '<html><head></head><body><div id="chart"></div>'
        "<script>"
        "try { const total = undeclaredVar.foo; } "
        "catch (error) { console.error('[ERD] chart my-chart failed:', error); }"
        "</script>"
        "</body></html>"
    )
    report = check_dashboard_html(html, set())
    assert any("the try/catch is damage control, not a fix" in error for error in report.errors), (
        report.errors
    )


def test_multiple_swallowed_chart_errors_all_reported() -> None:
    """多張壞圖(各自用自己的 try/catch 吞掉不同的錯誤)必須全部列出,不是抓到第一張就停。"""
    html = (
        '<html><head></head><body><div id="chart"></div>'
        "<script>"
        "try { const a = FIRSTBAD.foo; } "
        "catch (error) { console.error('[ERD] chart first failed:', error); }"
        "try { const b = SECONDBAD.bar; } "
        "catch (error) { console.error('[ERD] chart second failed:', error); }"
        "try { const c = THIRDBAD.baz; } "
        "catch (error) { console.error('[ERD] chart third failed:', error); }"
        "</script>"
        "</body></html>"
    )
    report = check_dashboard_html(html, set())
    assert not report.ok
    for chart_name in ("first", "second", "third"):
        assert any(f"Chart '{chart_name}' threw at runtime" in error for error in report.errors), (
            report.errors
        )


def test_non_erd_console_error_not_reported() -> None:
    """模型自己的除錯用 `console.error`(不是 chart 錯誤範本的固定格式)不該被誤判成壞圖。"""
    html = (
        '<html><head></head><body><div id="chart"></div>'
        "<script>"
        "console.error('debugging value:', 42);"
        "const data = window.__ERD_RESULTS__['q1'];"
        "const chart = echarts.init(document.getElementById('chart'), 'erd');"
        "chart.setOption({ tooltip: { trigger: 'axis' }, series: [] });"
        "</script>"
        "</body></html>"
    )
    report = check_dashboard_html(html, {"q1"})
    assert report.ok, report.errors


def test_swallowed_chart_error_not_duplicated_by_multi_mole_replay() -> None:
    """互動情境:block 0 有個「被自己 try/catch 吞掉」的錯誤(console.error 案例),block 1
    另外有個未包 try/catch 的裸 ReferenceError(觸發 multi-mole 的 stub+重建+重放機制,把
    block 0 重新跑一次)。block 0 的 console.error 訊息只能出現一次——重放不能把它算成兩條
    重複的 guard error。"""
    html = (
        '<html><head></head><body><div id="chart"></div>'
        "<script>"
        "try { const total = leakedScopeVar.foo; } "
        "catch (error) { console.error('[ERD] chart alpha failed:', error); }"
        "</script>"
        "<script>const beta = RAWUNDECLARED.bar;</script>"
        "</body></html>"
    )
    report = check_dashboard_html(html, set())
    assert not report.ok
    alpha_error_count = sum(
        1 for error in report.errors if "Chart 'alpha' threw at runtime" in error
    )
    assert alpha_error_count == 1, report.errors
    assert any("RAWUNDECLARED" in error for error in report.errors), report.errors


def test_campaign_fixture_c1_t3_swallowed_chart_errors_all_detected() -> None:
    """Regression fixture from the 3-tab stability campaign (c1-t3.html, real shipped
    artifact): three charts (dept-success, feature-response, feedback-rating) each declare a
    `const` inside another chart's own `try { ... }` block and reference it later from their
    *own* try/catch -- a block-scoping leak that only throws once the referencing chart's own
    guard branch executes with real column data, gets caught by that chart's own try/catch, and
    used to be swallowed with zero guard signal (the whole point of this fix). `available_query_ids`
    is `referenced_query_ids(html)`'s full set, per how this fixture was actually captured
    (fed the shipped/injected HTML straight to the guard, matching what the repair endpoint's
    error report would have seen if the guard had caught it at ship time)."""
    html = (FIXTURES_DIR / "campaign-c1-t3-swallowed-chart-errors.html").read_text(encoding="utf-8")
    available_query_ids = referenced_query_ids(html)

    report = check_dashboard_html(html, available_query_ids)

    assert not report.ok
    for chart_name in ("dept-success", "feature-response", "feedback-rating"):
        assert any(f"Chart '{chart_name}' threw at runtime" in error for error in report.errors), (
            report.errors
        )


def test_campaign_fixture_c1_t3_production_equivalent_with_real_results_seeded() -> None:
    """Production-equivalent check: `check_dashboard_html` in `app/main.py` always runs on the
    *pre-injection* HTML (`strip_injected_blocks` output), with `results` passed in from
    `load_all_results`/`all_results` -- not on the shipped/post-injection artifact the other
    fixture test above uses. This test reconstructs exactly that production shape: strip the
    fixture's own injected scripts to get back the clean model-authored HTML, recover the real
    `results` payload the fixture was built with straight out of its own injected
    `erd-results-data` script, and feed both to the guard the same way `app/main.py` now does
    (`check_dashboard_html(clean_html, available_query_ids, results)`). Must still catch all 3
    broken charts -- this is the scenario the coordinator flagged as the one that actually
    matters: without real `results` seeded, the sandbox's generic fake columns (`__c0`/`__c1`)
    never match this dashboard's `getCol(columns, 'department', ...)`-style real-name lookups,
    so the `if (idx >= 0) { try { ... } } ` gates guarding every chart never open and the bug
    inside them is never reached (see `test_...results_omitted_known_blind_spot` below for the
    reverse case that documents this failure mode directly)."""
    shipped_html = (FIXTURES_DIR / "campaign-c1-t3-swallowed-chart-errors.html").read_text(
        encoding="utf-8"
    )
    real_results = _parse_injected_results(shipped_html)
    clean_html = strip_injected_blocks(shipped_html)
    available_query_ids = referenced_query_ids(clean_html)

    report = check_dashboard_html(clean_html, available_query_ids, real_results)

    assert not report.ok
    for chart_name in ("dept-success", "feature-response", "feedback-rating"):
        assert any(f"Chart '{chart_name}' threw at runtime" in error for error in report.errors), (
            report.errors
        )


def test_campaign_fixture_c1_t3_results_omitted_known_blind_spot() -> None:
    """Reverse of the test above, same pre-injection input, `results` simply omitted (the old
    call signature/behavior). This must NOT catch the 3 broken charts -- documented on purpose
    as a known, accepted blind spot of the `results=None` fallback path, so nobody mistakes
    "the fallback still sort of works" for a guarantee. Every production call site in
    `app/main.py` now passes `results`; this test exists to pin the boundary, not to describe
    intended behavior for a caller that chooses to omit it."""
    shipped_html = (FIXTURES_DIR / "campaign-c1-t3-swallowed-chart-errors.html").read_text(
        encoding="utf-8"
    )
    clean_html = strip_injected_blocks(shipped_html)
    available_query_ids = referenced_query_ids(clean_html)

    report = check_dashboard_html(clean_html, available_query_ids)

    for chart_name in ("dept-success", "feature-response", "feedback-rating"):
        assert not any(
            f"Chart '{chart_name}' threw at runtime" in error for error in report.errors
        ), report.errors


def test_swallowed_chart_error_skipped_gracefully_when_quickjs_unavailable(monkeypatch) -> None:
    from app.engine.html_guard import js_runtime

    monkeypatch.setattr(js_runtime, "QUICKJS_AVAILABLE", False)
    html = (
        '<html><head></head><body><div id="chart"></div>'
        "<script>"
        "try { const total = undeclaredVar.foo; } "
        "catch (error) { console.error('[ERD] chart my-chart failed:', error); }"
        "</script>"
        "</body></html>"
    )
    report = check_dashboard_html(html, set())
    assert not any("threw at runtime" in error for error in report.errors)


# -- async rethrow in chart catch blocks (setTimeout stub must swallow it) -----------------
#
# skill 的 catch 範本現在會在 catch 裡多做 `setTimeout(() => { throw error; }, 0)`(async
# 重拋,讓錯誤在真實瀏覽器裡浮上 window.onerror,見 chart-rules.md)。sandbox 的 setTimeout
# stub 是同步立即呼叫 callback——如果不特別處理,這個重拋會在呼叫當下就變成未捕捉例外,把一段
# 正常、只是「有 try/catch」的腳本誤判為壞掉。


def test_async_rethrow_in_setTimeout_does_not_crash_sandbox() -> None:
    """catch 區塊裡的 `setTimeout(() => { throw error; }, 0)` 不能讓 sandbox 把整段 script
    判成執行期錯誤——console.error 收集器已經是這個案例的偵測訊號,setTimeout 的立即重拋只是
    為了真實瀏覽器的 window.onerror,不該在 sandbox 裡變成第二個(而且是誤導性的)錯誤來源。"""
    html = (
        '<html><head></head><body><div id="chart"></div>'
        "<script>"
        "try { const total = undeclaredVar.foo; } "
        "catch (error) { "
        "console.error('[ERD] chart my-chart failed:', error); "
        "setTimeout(() => { throw error; }, 0); "
        "}"
        "</script>"
        "</body></html>"
    )
    report = check_dashboard_html(html, set())
    assert not report.ok
    assert not any(
        "execution error" in error or "execution timed out" in error for error in report.errors
    ), report.errors
    assert any("Chart 'my-chart' threw at runtime" in error for error in report.errors), (
        report.errors
    )


def test_setTimeout_without_throw_still_runs_callback_synchronously() -> None:
    """setTimeout stub 的既有行為(立即同步呼叫 callback)必須維持,只是額外用 try/catch 包住
    ——不拋例外的 callback 效果不變(既有 corpus 假陽性哨兵仰賴這點,見
    test_execution_smoke_normal_dashboard_js_has_zero_false_positives)。"""
    html = (
        '<html><head></head><body><div id="chart"></div><div id="marker"></div>'
        "<script>"
        "setTimeout(function () { document.getElementById('marker').textContent = 'ran'; }, 0);"
        "</script>"
        "</body></html>"
    )
    report = check_dashboard_html(html, set())
    assert report.ok, report.errors


# -- getCol miss: console.warn collector -----------------------------------------------------
#
# The skill-mandated getCol helper only console.warn's when a column lookup misses (returns -1,
# never throws) -- a real deployed dashboard emitted 29 of these. The sandbox used to throw
# console.warn away as a no-op; these tests pin the collector that turns that signal into a
# guard error.

_GET_COL_HELPER = (
    "function getCol(columns, ...candidates) {\n"
    "  for (const candidate of candidates) {\n"
    "    const index = columns.indexOf(candidate);\n"
    "    if (index >= 0) return index;\n"
    "  }\n"
    "  console.warn('[ERD] column not found:', candidates); return -1;\n"
    "}\n"
)


def _get_col_miss_html() -> str:
    return (
        '<html><head><script src="' + ALLOWED_SCRIPT_SRC_PREFIXES[0] + '"></script></head>'
        '<body><div id="chart"></div>\n'
        "<script>\n" + _GET_COL_HELPER + "const featureRating = window.__ERD_RESULTS__['q2'];\n"
        "const ratingIndex = getCol(featureRating.columns, 'avg_rating');\n"
        "const chart = echarts.init(document.getElementById('chart'), 'erd');\n"
        "chart.setOption({ tooltip: {}, series: [] });\n"
        "</script></body></html>"
    )


def test_get_col_miss_is_rejected_with_call_site_and_owning_query() -> None:
    """getCol 找不到欄位時只 console.warn 回 -1,不拋例外——guard MUST 把這個訊號變成退貨,
    並且指出呼叫點行號與「該欄位其實在哪個 qN」,讓模型一輪修完。"""
    results = {
        "q2": {
            "columns": ["sentiment", "count", "percentage"],
            "rows": [["正面", 3, 0.5]],
            "truncated": False,
        },
        "q5": {
            "columns": ["feature_name", "avg_rating"],
            "rows": [["匯出", 4.2]],
            "truncated": False,
        },
    }
    report = check_dashboard_html(_get_col_miss_html(), {"q2", "q5"}, results)

    assert not report.ok, report.errors
    miss_errors = [error for error in report.errors if "avg_rating" in error]
    assert miss_errors, report.errors
    assert "q5" in miss_errors[0], miss_errors
    # 呼叫點是 getCol(...) 那一行,不是 helper 裡 console.warn 的那一行(算法見
    # _get_col_miss_html:content 從 <script> 標籤結束後的換行起算,helper 佔 8 行,
    # 中間資料行 1 行,故呼叫點落在 html 第 11 行——與 _resolve_stack_call_site_line
    # 的 stack-frame 算法互相印證,不是憑空寫死)。
    assert "Line 11:" in miss_errors[0], miss_errors


def test_get_col_hit_produces_no_warning_error() -> None:
    """欄位真的存在時零誤報。"""
    results = {
        "q2": {"columns": ["sentiment", "avg_rating"], "rows": [["正面", 4.2]], "truncated": False},
    }
    report = check_dashboard_html(_get_col_miss_html(), {"q2"}, results)

    assert not any("column not found" in error for error in report.errors), report.errors


def test_get_col_miss_without_real_results_is_not_reported() -> None:
    """沒有真實 results 時 sandbox 灌的是泛用假欄名(__c0/__c1),每個 getCol 都會 miss——
    這種情況 MUST 整條規則跳過,否則全是誤報。"""
    report = check_dashboard_html(_get_col_miss_html(), {"q2"}, None)

    assert not any("column not found" in error for error in report.errors), report.errors


# -- 6a: charts must read window.__ERD_RESULTS__ (never hard-code numbers) -----------------


def test_charts_without_any_erd_results_reference_fail() -> None:
    """把數字硬編進 HTML、完全不讀 __ERD_RESULTS__ 的 dashboard 目前能順利過 guard——
    這類違規在指標上是隱形的,MUST 退貨。"""
    html = (
        '<html><head><script src="' + ALLOWED_SCRIPT_SRC_PREFIXES[0] + '"></script></head>'
        '<body><div id="chart"></div>'
        "<script>const chart = echarts.init(document.getElementById(\"chart\"), 'erd'); "
        "chart.setOption({ tooltip: {}, series: [{ data: [42, 7] }] });</script>"
        "</body></html>"
    )
    report = check_dashboard_html(html, {"q1"})

    assert not report.ok
    assert any("__ERD_RESULTS__" in error for error in report.errors), report.errors


def test_html_without_charts_is_not_required_to_bind_results() -> None:
    """純文字/表格 dashboard 沒有 echarts.init,零檢查零誤報。"""
    html = "<html><head></head><body><div>純文字結論</div></body></html>"
    report = check_dashboard_html(html, {"q1"})

    assert not any("__ERD_RESULTS__" in error for error in report.errors), report.errors


def test_tab_structure_pill_style_missing_border_b_2_fails() -> None:
    html = (
        "<html><head></head><body>"
        '<nav role="tablist">'
        '<div class="bg-slate-100 rounded-full p-1 flex gap-1">'
        '<button onclick="showTab(0)" id="tab-0" role="tab" '
        'class="px-4 py-1.5 rounded-full bg-white text-slate-900 shadow-sm">Tab 1</button>'
        "</div></nav>"
        '<div id="panel-0"></div>'
        "<script>"
        "function showTab(idx) { window.dispatchEvent(new Event('resize')); }"
        "</script>"
        "</body></html>"
    )
    report = check_dashboard_html(html, set())
    assert not report.ok
    assert any("Tab styling deviates from spec" in error for error in report.errors)


# -- call-site substitution must require an actually-shared helper -------------------------


def test_error_inside_function_called_once_reports_throw_line_not_call_site() -> None:
    """`renderChartA` is called from exactly one place -- the headline MUST stay on the real
    throw line (5), not jump to the blameless call site (7). Substituting the call site is only
    correct when the throwing function is genuinely shared (2+ call sites elsewhere)."""
    html = (
        '<html><head></head><body><div id="chart"></div>\n'
        "<script>\n"
        "function renderChartA() {\n"
        "  const value = undefined;\n"
        "  return value.foo;\n"
        "}\n"
        "renderChartA();\n"
        "</script></body></html>"
    )
    report = check_dashboard_html(html, set())
    assert not report.ok
    assert any("Line 5:" in error for error in report.errors), report.errors
    assert not any("Line 7:" in error for error in report.errors), report.errors


# -- sandbox-internal prelude frames must never leak into the reported line ----------------


def test_call_site_substitution_reports_real_lines_not_a_prelude_frame() -> None:
    """`initAll` is called directly by `window.addEventListener('load', initAll)` -- the frame
    right after its throw site is the sandbox's internal `__erdAddEventListenerSync`, which
    must be filtered out (it belongs to the prelude source, not this HTML) before the next real
    frame (the `<eval>` call site) is picked up. Without that filtering (and the bounds check
    that catches anything the filtering misses), the wrong frame index gets treated as the call
    site and its unrelated line number leaks into the report as a fabricated line far past the
    end of this 9-line document."""
    html = (
        '<html><head></head><body><div id="chart"></div>\n'
        "<script>\n"
        "function initAll(spec) {\n"
        "  return spec.series.length;\n"
        "}\n"
        "window.addEventListener('load', initAll);\n"
        "function bootA() { initAll({series: []}); }\n"
        "function bootB() { initAll({series: []}); }\n"
        "</script></body></html>"
    )
    assert len(html.splitlines()) == 9

    report = check_dashboard_html(html, set())

    assert not report.ok
    assert any(
        error.startswith("Line 6: TypeError") and "thrown inside `initAll` at line 4" in error
        for error in report.errors
    ), report.errors


# -- brace matching must be comment-aware ---------------------------------------------------


def test_brace_in_line_comment_does_not_desync_tab_switch_body_scan() -> None:
    """A `// ... { ...` comment inside `switchTab`'s body must not desync the depth counter --
    otherwise the scanner runs past the function's real closing brace and swallows the
    following top-level `window.dispatchEvent(new Event('resize'))`, which is genuinely
    OUTSIDE the function, as if it were inside it. That must still fail the resize check."""
    html = (
        '<html><head><script src="' + ALLOWED_SCRIPT_SRC_PREFIXES[0] + '"></script></head>'
        '<body><button onclick="switchTab(1)" class="border-b-2">Tab 2</button>'
        '<div id="view-0"></div><div id="view-1"></div><div id="chart"></div>'
        "<script>const data = window.__ERD_RESULTS__['q1'];\n"
        "function switchTab(index) {\n"
        "  // TODO: handle edge case { still unresolved\n"
        "  document.getElementById('view-' + index).classList.remove('hidden');\n"
        "}\n"
        "const chart = echarts.init(document.getElementById('chart'), 'erd');\n"
        "chart.setOption({ tooltip: {}, series: [] });\n"
        "window.dispatchEvent(new Event('resize'));\n"
        "</script></body></html>"
    )
    report = check_dashboard_html(html, {"q1"})
    assert not report.ok
    assert any("resize" in error for error in report.errors), report.errors


# -- helper call-site scan must ignore strings and comments --------------------------------


def test_helper_call_site_scan_ignores_comment_and_string_mentions() -> None:
    """A comment mentioning `getCol(columns, name)` and a string literal mentioning
    `getCol(x, y)` must not be counted as call sites -- only the two real calls (lines 9 and
    10) may appear in the reported call-site list."""
    html = (
        '<html><head></head><body><div id="chart"></div>\n'
        "<script>\n"
        "function getCol(columns, candidate) {\n"
        "  return columns.indexOf(candidate);\n"
        "}\n"
        "// call getCol(columns, name) to resolve a column index\n"
        "const label = 'Use getCol(x, y) when binding a column';\n"
        "const first = window.__ERD_RESULTS__['q1'].rows;\n"
        "const firstIndex = getCol(first.columns, 'a');\n"
        "const secondIndex = getCol(first.columns, 'b');\n"
        "const chart = echarts.init(document.getElementById('chart'), 'erd');\n"
        "chart.setOption({ tooltip: {}, series: [] });\n"
        "</script></body></html>"
    )
    results = {"q1": {"columns": ["a", "b"], "rows": [["x", 1]], "truncated": False}}
    report = check_dashboard_html(html, {"q1"}, results)
    assert not report.ok
    type_errors = [error for error in report.errors if "TypeError" in error]
    assert type_errors, report.errors
    assert "9" in type_errors[0] and "10" in type_errors[0], type_errors
    assert "6" not in type_errors[0], type_errors
    assert "7" not in type_errors[0], type_errors
