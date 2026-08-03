"""tab 結構的辨識與慣例檢查(resize 派發、Tabler 底線樣式)。"""

import re

from .js_lexer import mask_strings_and_comments

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
