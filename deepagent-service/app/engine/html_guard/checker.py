"""`check_dashboard_html` entry point——依序跑結構/體積/CDN/查詢引用/JS 語法/sandbox
smoke/tab 規則,最後套用 erd 主題改寫。"""

from collections import Counter

from . import js_lexer, js_syntax
from .report import GuardReport, check_size, check_structure
from .rules import (
    _check_data_binding,
    _check_no_erd_results_overwrite,
    _check_no_register_theme,
    _check_referenced_query_ids,
    _check_script_src_whitelist,
)
from .rules_tab import _check_tab_conventions
from .sandbox import execute_scripts_smoke
from .sandbox.context import _extract_known_element_ids
from .theme_rewrite import _apply_erd_theme


def check_dashboard_html(
    html: str, available_query_ids: set[str], results: dict[str, dict] | None = None
) -> GuardReport:
    """依序執行結構、體積、CDN 白名單、查詢結果引用、erd 主題、inline JS 語法(Level 1)、
    sandbox 執行 smoke(Level 2,只在 Level 1 乾淨時跑)、tab 規範等檢查——規則
    之間互不 fail-fast,全部違規一次收集,供模型一輪修完。`results` 提供時 Level 2 用真實
    欄名灌 sandbox;`echarts.init(X)` 單參數呼叫會被確定性改寫為帶 `'erd'` 主題。
    """
    errors: list[str] = []
    unconditional_errors: list[str] = []

    check_structure(html, errors, unconditional_errors)
    check_size(html, errors)
    _check_script_src_whitelist(html, errors, unconditional_errors)
    _check_no_erd_results_overwrite(html, errors, unconditional_errors)
    _check_no_register_theme(html, errors)
    _check_referenced_query_ids(html, available_query_ids, errors)

    errors_before_syntax_check = len(errors)
    js_syntax.check_js_syntax(html, errors)
    if len(errors) == errors_before_syntax_check:
        # Level 2 只在語法乾淨時跑——語法已錯就不必(也不該)真的執行那段壞掉的 script。
        known_element_ids = frozenset(_extract_known_element_ids(html))
        errors.extend(
            execute_scripts_smoke(
                js_lexer.extract_inline_scripts_with_lines(html),
                available_query_ids,
                results,
                known_element_ids,
                html=html,
            )
        )

    _check_data_binding(html, errors)
    errors.extend(_check_tab_conventions(html))
    rewritten_html = _apply_erd_theme(html, errors)
    if rewritten_html != html:
        # _apply_erd_theme is the only rule that mutates the document -- re-validate its output
        # so a rewrite that happens to break JS syntax can't ship with ok=True. Skipped when
        # nothing changed: no new syntax risk, and it would just duplicate the check above.
        # The rewrite is in-line (same script index/line), so a syntax error that already
        # existed pre-rewrite reappears byte-identical in this second pass -- only append
        # errors genuinely new to the rewritten output, or the repair prompt shows the same
        # bullet twice. Counted (not set-based) so a rewrite that duplicates an *already*
        # duplicated pre-existing error still surfaces the extra copy.
        errors_before_syntax_recheck = Counter(errors)
        rewritten_syntax_errors: list[str] = []
        js_syntax.check_js_syntax(rewritten_html, rewritten_syntax_errors)
        already_seen: Counter[str] = Counter()
        for syntax_error in rewritten_syntax_errors:
            already_seen[syntax_error] += 1
            if already_seen[syntax_error] > errors_before_syntax_recheck[syntax_error]:
                errors.append(syntax_error)

    return GuardReport(
        ok=not errors,
        errors=errors,
        unconditional_errors=unconditional_errors,
        html=rewritten_html,
    )
