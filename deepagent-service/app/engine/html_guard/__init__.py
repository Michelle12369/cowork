"""dashboard.html 確定性檢查——送出前最後一道關卡。engine 層 stdlib only(禁止 import LLM
框架,ruff TID251 會擋);JS 檢查分兩層:Level 1 語法 parse-only、Level 2 sandbox 執行
smoke,錯誤訊息設計成可直接餵回模型修復。
"""

import logging
import re
from urllib.parse import urlsplit

from app.engine.results import referenced_query_ids

from . import js_lexer, js_syntax
from .js_lexer import mask_strings_and_comments
from .report import HTML_MAX_BYTES as HTML_MAX_BYTES
from .report import GuardReport, check_size, check_structure
from .sandbox import execute_scripts_smoke
from .sandbox.context import _extract_known_element_ids

logger = logging.getLogger(__name__)

# 逐字複製自 backend/src/main/resources/templates/openai/system-prompt.vm 的
# Tailwind CDN 與 ECharts CDN 強制寫法。只用於錯誤訊息文字(給模型看的 allowlist 提示)——
# 實際比對邏輯是 `_is_allowed_script_src` 的 host 邊界檢查,NEVER 對這個 tuple 做
# `src.startswith(prefix)`(那正是被 lookalike host 繞過的漏洞寫法)。
ALLOWED_SCRIPT_SRC_PREFIXES: tuple[str, ...] = (
    "https://cdn.tailwindcss.com",
    "https://cdn.jsdelivr.net/npm/echarts@",
)

_ALLOWED_TAILWIND_HOST = "cdn.tailwindcss.com"
_ALLOWED_JSDELIVR_HOST = "cdn.jsdelivr.net"
_ALLOWED_JSDELIVR_ECHARTS_PATH_PREFIX = "/npm/echarts@"

_ECHARTS_INIT_CALL_PREFIX = "echarts.init("
_REGISTER_THEME_CALL_PREFIX = "registerTheme("


def _check_tooltip(html: str, errors: list[str]) -> None:
    """粗粒度規則:HTML 含至少一個 `echarts.init(` 呼叫,就整份 HTML 必須出現過
    `tooltip` 字樣——正確做法(依圖表類型設對 trigger)交給 skill 教,guard 只擋
    「整份沒有任何 tooltip 設定」這種全缺的情況。"""
    if _ECHARTS_INIT_CALL_PREFIX in html and "tooltip" not in html:
        errors.append("Every chart must set a tooltip.")


def _check_data_binding(html: str, errors: list[str]) -> None:
    """有圖表就一定要從 `window.__ERD_RESULTS__` 取資料。全檔零次引用代表數字被硬編進
    HTML——不會拋例外、順利過其他檢查,但交付的每個數字都可能是過期的。"""
    if _ECHARTS_INIT_CALL_PREFIX in html and "__ERD_RESULTS__" not in html:
        errors.append(
            "The dashboard initializes ECharts but never reads window.__ERD_RESULTS__ -- the "
            "numbers are hard-coded. Every chart, KPI and table MUST read its data from "
            "window.__ERD_RESULTS__['<query id>'] (see the dashboard skill)."
        )


# tab 結構的辨識訊號:skill 範本的 `showTab(`／`id="panel-0"`／`role="tab"`,再加上模型自己
# 命名的切換函式(`onclick="...Tab("`,例如模型自訂的 `switchTab`)與多個 `panel-N` 容器
# ——兩者任一命中都代表這是一份 tab dashboard,即使沒有用 skill 的固定命名。
_TAB_STRUCTURE_MARKERS: tuple[str, ...] = ("showTab(", 'id="panel-0"', 'role="tab"')
_TAB_ONCLICK_PATTERN = re.compile(r"""onclick\s*=\s*["'][^"']*Tab\s*\(""", re.IGNORECASE)
_PANEL_CONTAINER_PATTERN = re.compile(r"""id\s*=\s*["']panel-\d+["']""", re.IGNORECASE)
# 切換函式命名慣例的 fallback 訊號(見 `_tab_switch_function_bodies`):名稱以 Tab 結尾的
# 具名函式宣告(`showTab`/`switchTab`/...)。
_TAB_SWITCH_FUNCTION_PATTERN = re.compile(r"function\s+(\w*Tab)\s*\(")

# `onclick="...NAME("` 裡的呼叫識別字——「這個函式真的被拿來切換」的直接訊號,不管函式
# 怎麼命名。整段屬性值先抓出來,再從裡面找呼叫模式,因為一個 onclick 可能有多條敘述。
_ONCLICK_HANDLER_PATTERN = re.compile(r"""onclick\s*=\s*["']([^"']*)["']""", re.IGNORECASE)
_FUNCTION_CALL_NAME_PATTERN = re.compile(r"([A-Za-z_$][A-Za-z0-9_$]*)\s*\(")

# 函式宣告的幾種常見寫法——模型常把切換函式寫成箭頭函式賦值,不一定用 `function` 關鍵字。
_FUNCTION_KEYWORD_DECLARATION_TEMPLATE = r"function\s+{name}\s*\("
_FUNCTION_EXPRESSION_ASSIGNMENT_TEMPLATE = (
    r"(?:const|let|var)\s+{name}\s*=\s*(?:async\s*)?function\s*\("
)
_ARROW_ASSIGNMENT_TEMPLATES: tuple[str, ...] = (
    r"(?:const|let|var)\s+{name}\s*=\s*(?:async\s*)?\([^)]*\)\s*=>\s*",
    r"(?:const|let|var)\s+{name}\s*=\s*(?:async\s*)?[A-Za-z_$][A-Za-z0-9_$]*\s*=>\s*",
)

_RESIZE_DISPATCH_PATTERN = re.compile(
    r"""dispatchEvent\s*\(\s*new\s+Event\s*\(\s*['"]resize['"]\s*\)\s*\)""", re.IGNORECASE
)
_RESIZE_METHOD_SNIPPET = ".resize()"
_TABLER_STYLE_MARKER = "border-b-2"


def _has_tab_structure(html: str) -> bool:
    if any(marker in html for marker in _TAB_STRUCTURE_MARKERS):
        return True
    if _TAB_ONCLICK_PATTERN.search(html):
        return True
    return len(_PANEL_CONTAINER_PATTERN.findall(html)) >= 2


def _find_matching_close_brace(text: str, open_brace_index: int) -> int | None:
    """回傳 `text[open_brace_index]`(必為 `{`)對應的閉大括號 index;不平衡則回 None。

    對字串字面值中的大括號免疫(引號內的 `{`/`}` 不計入深度),邏輯與
    `_find_matching_close_paren` 對稱,只是換成配對大括號。
    """
    depth = 0
    quote_char: str | None = None
    index = open_brace_index
    text_length = len(text)
    while index < text_length:
        character = text[index]
        if quote_char is not None:
            if character == "\\":
                index += 2
                continue
            if character == quote_char:
                quote_char = None
            index += 1
            continue
        if character in ("'", '"', "`"):
            quote_char = character
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def _onclick_wired_function_names(html: str) -> list[str]:
    """依文件順序回傳所有出現在 `onclick="..."` 屬性值裡、看起來像函式呼叫的識別字(去重)。"""
    names: list[str] = []
    seen: set[str] = set()
    for handler_match in _ONCLICK_HANDLER_PATTERN.finditer(html):
        for call_match in _FUNCTION_CALL_NAME_PATTERN.finditer(handler_match.group(1)):
            name = call_match.group(1)
            if name not in seen:
                seen.add(name)
                names.append(name)
    return names


def _function_body_by_name(masked_html: str, original_html: str, function_name: str) -> str | None:
    """在遮罩過的 HTML(見 `mask_strings_and_comments`)裡找名為 `function_name` 的函式
    (依序試 `function NAME(...)` 宣告式、`NAME = function(...)` expression 賦值、箭頭函式
    賦值),回傳其函式體切自**原始** HTML 的原文(遮罩後的 index 與原文一一對應,但函式體
    內容要保留真實原文,例如合法的 `new Event('resize')` 字串字面值)。找不到宣告,或找到
    的是無大括號的隱式 return 箭頭函式(沒有可掃描的函式體)一律回傳 None。"""
    escaped_name = re.escape(function_name)

    for template in (
        _FUNCTION_KEYWORD_DECLARATION_TEMPLATE,
        _FUNCTION_EXPRESSION_ASSIGNMENT_TEMPLATE,
    ):
        match = re.search(template.format(name=escaped_name), masked_html)
        if match is None:
            continue
        open_brace_index = masked_html.find("{", match.end())
        if open_brace_index == -1:
            continue
        close_brace_index = _find_matching_close_brace(masked_html, open_brace_index)
        if close_brace_index is not None:
            return original_html[open_brace_index : close_brace_index + 1]

    for template in _ARROW_ASSIGNMENT_TEMPLATES:
        match = re.search(template.format(name=escaped_name), masked_html)
        if match is None:
            continue
        open_brace_index = masked_html.find("{", match.end())
        # 隱式 return(`=> expr`,無大括號)不是可掃描的函式體——要求 `{` 緊接在 `=>` 後面
        # (中間只有空白),避免誤吃後面不相關程式碼裡碰巧出現的第一個 `{`。
        if open_brace_index == -1 or masked_html[match.end() : open_brace_index].strip():
            continue
        close_brace_index = _find_matching_close_brace(masked_html, open_brace_index)
        if close_brace_index is not None:
            return original_html[open_brace_index : close_brace_index + 1]

    return None


def _tab_switch_function_bodies(html: str) -> list[str]:
    """依優先順序找出真正被拿來切換 tab 的函式體(resize 檢查要求 resize 派發在函式體內):
    優先看 `onclick="...NAME("` 綁定到的函式,其次退回名稱以 `Tab` 結尾的具名函式宣告,
    兩者都找不到則回空列表、呼叫端退回檢查整份 HTML。
    """
    masked_html = mask_strings_and_comments(html)

    onclick_wired_bodies = [
        body
        for name in _onclick_wired_function_names(html)
        if (body := _function_body_by_name(masked_html, html, name)) is not None
    ]
    if onclick_wired_bodies:
        return onclick_wired_bodies

    bodies: list[str] = []
    for function_match in _TAB_SWITCH_FUNCTION_PATTERN.finditer(masked_html):
        open_brace_index = masked_html.find("{", function_match.end())
        if open_brace_index == -1:
            continue
        close_brace_index = _find_matching_close_brace(masked_html, open_brace_index)
        if close_brace_index is None:
            continue
        bodies.append(html[open_brace_index : close_brace_index + 1])
    return bodies


def _check_tab_conventions(html: str) -> list[str]:
    """HTML 含 tab 結構時強制兩條規則:切換函式體內必須有 resize 派發/`.resize()` 呼叫
    (否則 hidden panel 裡的 ECharts 量到 0 寬容器,圖表空白);樣式必須用 Tabler 底線式
    `border-b-2`(不是藥丸/segmented 樣式)。無 tab 結構的 dashboard 零檢查、零誤報。
    """
    if not _has_tab_structure(html):
        return []

    errors: list[str] = []
    switch_function_bodies = _tab_switch_function_bodies(html)
    resize_search_targets = switch_function_bodies or [html]
    if not any(
        _RESIZE_DISPATCH_PATTERN.search(target) or _RESIZE_METHOD_SNIPPET in target
        for target in resize_search_targets
    ):
        errors.append(
            "The tab switch function never dispatches a resize -- ECharts instances created in "
            "a hidden panel measured a 0-width container and stay stuck at the 100px fallback. "
            "Add window.dispatchEvent(new Event('resize')) (or call chart.resize()) inside the "
            "switch function body. Use the skill's showTab template verbatim."
        )
    if _TABLER_STYLE_MARKER not in html:
        errors.append(
            "Tab styling deviates from spec: MUST use the Tabler underline-style template "
            "(active state is a border-b-2 underline, not a pill/segmented style). Use the "
            "skill's tabs template verbatim."
        )
    return errors


def _is_allowed_script_src(src: str) -> bool:
    """Host 邊界比對,不是字串前綴比對——`src.startswith(prefix)` 會被
    `https://cdn.tailwindcss.com.evil.example` 這類 lookalike host 繞過,`urlsplit(...).hostname`
    才是安全的精確比對。jsdelivr 額外要求 path 落在 echarts npm package 底下。"""
    try:
        parsed = urlsplit(src)
    except ValueError:
        return False
    if parsed.scheme != "https":
        return False
    host = parsed.hostname
    if host == _ALLOWED_TAILWIND_HOST:
        return True
    if host == _ALLOWED_JSDELIVR_HOST:
        return parsed.path.startswith(_ALLOWED_JSDELIVR_ECHARTS_PATH_PREFIX)
    return False


def _check_script_src_whitelist(html: str, errors: list[str]) -> None:
    """掃出所有帶 src 的 `<script` 標籤,對 URL 做 host 白名單比對。跑在生成期(serve 期的
    ArtifactCdnRewriter 尚未把 CDN URL 換成 /vendor/ 之前),確保模型寫的 src 是 rewriter
    認得的網址;不是唯一安全邊界(真正邊界在 serve 層 CSP),但比對邏輯仍不可靠字串 startswith。
    """
    for tag_match in js_lexer._SCRIPT_OPEN_TAG_PATTERN.finditer(html):
        attrs = tag_match.group(1) or ""
        src_match = js_lexer._SRC_ATTR_VALUE_PATTERN.search(attrs)
        if src_match is None:
            continue
        src = next(group for group in src_match.groups() if group is not None)
        if not _is_allowed_script_src(src):
            errors.append(
                f'<script src="{src}"> is not on the whitelist. Only these prefixes are allowed: '
                f"{', '.join(ALLOWED_SCRIPT_SRC_PREFIXES)}"
            )


def _check_no_register_theme(html: str, errors: list[str]) -> None:
    """dashboard skill 已指示模型 NEVER 自行呼叫 `echarts.registerTheme(...)`（主題由
    `inject_theme` 在 DASHBOARD_HTML 送出前統一注入），但 skill 指示本身不是強制關卡——模型
    仍可能在 HTML 裡自帶一份主題定義，蓋掉注入的 'erd' 主題。這條規則把它變成確定性關卡。"""
    if _REGISTER_THEME_CALL_PREFIX in html:
        errors.append(
            "Detected a registerTheme( call: the theme is injected by the system, so the HTML "
            "must NEVER call registerTheme itself. Please remove that call "
            "(use echarts.init(el, 'erd') instead)."
        )


def _check_referenced_query_ids(
    html: str, available_query_ids: set[str], errors: list[str]
) -> None:
    referenced_ids = referenced_query_ids(html)
    missing_ids = referenced_ids - available_query_ids
    if missing_ids:
        missing_ids_text = ", ".join(sorted(missing_ids))
        errors.append(
            f"The HTML references query result id(s) that don't exist: {missing_ids_text}. "
            "Please confirm window.__ERD_RESULTS__ actually contains the corresponding query "
            "result(s), or correct the referenced id(s)."
        )


def _find_matching_close_paren(text: str, open_paren_index: int) -> int | None:
    """回傳 `text[open_paren_index]`（必為 `"("`）對應的閉括號 index；不平衡則回傳 None。

    對字串字面值中的括號免疫（`"("`/`)"` 出現在引號內不計入深度）。
    """
    depth = 0
    quote_char: str | None = None
    index = open_paren_index
    text_length = len(text)
    while index < text_length:
        character = text[index]
        if quote_char is not None:
            if character == "\\":
                index += 2
                continue
            if character == quote_char:
                quote_char = None
            index += 1
            continue
        if character in ("'", '"'):
            quote_char = character
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def _split_top_level_arguments(argument_text: str) -> list[str]:
    """依「最外層逗號」切引數（括號/引號內的逗號不算數）。"""
    if not argument_text.strip():
        return []

    arguments: list[str] = []
    current_argument_characters: list[str] = []
    depth = 0
    quote_char: str | None = None
    for character in argument_text:
        if quote_char is not None:
            current_argument_characters.append(character)
            if character == quote_char:
                quote_char = None
            continue
        if character in ("'", '"'):
            quote_char = character
            current_argument_characters.append(character)
        elif character in "([{":
            depth += 1
            current_argument_characters.append(character)
        elif character in ")]}":
            depth -= 1
            current_argument_characters.append(character)
        elif character == "," and depth == 0:
            arguments.append("".join(current_argument_characters).strip())
            current_argument_characters = []
        else:
            current_argument_characters.append(character)
    arguments.append("".join(current_argument_characters).strip())
    return arguments


def _apply_erd_theme(html: str, errors: list[str]) -> str:
    """掃描每個 `echarts.init(...)` 呼叫:單參數改寫為帶 `'erd'` 主題;雙參數且第二參數
    非 'erd' 則記錄 error、原樣保留。用括號深度平衡掃描,可正確處理引數本身含括號的呼叫。
    """
    output_parts: list[str] = []
    cursor = 0
    while True:
        call_start = html.find(_ECHARTS_INIT_CALL_PREFIX, cursor)
        if call_start == -1:
            output_parts.append(html[cursor:])
            break

        open_paren_index = call_start + len(_ECHARTS_INIT_CALL_PREFIX) - 1
        close_paren_index = _find_matching_close_paren(html, open_paren_index)
        if close_paren_index is None:
            # 括號不平衡（畸形呼叫），原樣保留、跳過此次呼叫繼續掃描。
            output_parts.append(html[cursor : open_paren_index + 1])
            cursor = open_paren_index + 1
            continue

        output_parts.append(html[cursor:call_start])
        inner_text = html[open_paren_index + 1 : close_paren_index]
        arguments = _split_top_level_arguments(inner_text)

        if len(arguments) <= 1:
            element_argument = arguments[0] if arguments else ""
            output_parts.append(f"echarts.init({element_argument}, 'erd')")
        else:
            theme_argument = arguments[1]
            if theme_argument in ("'erd'", '"erd"'):
                output_parts.append(html[call_start : close_paren_index + 1])
            else:
                errors.append(
                    f"echarts.init's second argument must be the 'erd' theme, but is currently "
                    f"{theme_argument}. Please remove the custom theme argument or change it to 'erd'."
                )
                output_parts.append(html[call_start : close_paren_index + 1])

        cursor = close_paren_index + 1

    return "".join(output_parts)


def check_dashboard_html(
    html: str, available_query_ids: set[str], results: dict[str, dict] | None = None
) -> GuardReport:
    """依序執行結構、體積、CDN 白名單、查詢結果引用、erd 主題、inline JS 語法(Level 1)、
    sandbox 執行 smoke(Level 2,只在 Level 1 乾淨時跑)、tooltip、tab 規範等檢查——規則
    之間互不 fail-fast,全部違規一次收集,供模型一輪修完。`results` 提供時 Level 2 用真實
    欄名灌 sandbox;`echarts.init(X)` 單參數呼叫會被確定性改寫為帶 `'erd'` 主題。
    """
    errors: list[str] = []

    check_structure(html, errors)
    check_size(html, errors)
    _check_script_src_whitelist(html, errors)
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

    _check_tooltip(html, errors)
    _check_data_binding(html, errors)
    errors.extend(_check_tab_conventions(html))
    rewritten_html = _apply_erd_theme(html, errors)

    return GuardReport(ok=not errors, errors=errors, html=rewritten_html)
