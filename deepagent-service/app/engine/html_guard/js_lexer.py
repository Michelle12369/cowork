"""inline `<script>` 內文抽取與字串／註解遮罩的狀態機。

`find_script_end` 逐字 port 自 backend `JsSyntaxValidator.java` 的 `findScriptEnd`,
兩邊 MUST 同步修改。
"""

import re

# -- <script> 內文抽取(port 自 backend JsSyntaxValidator.java 的 findScriptEnd 狀態機)-----

_SCRIPT_OPEN_TAG_PATTERN = re.compile(r"<script([^>]*)>", re.IGNORECASE)
_SRC_ATTR_PATTERN = re.compile(r"\bsrc\s*=", re.IGNORECASE)

# 抓 src 屬性「值」(quoted 單/雙引號皆可,或 unquoted 到下一個空白為止——HTML5 unquoted
# 屬性值語法本就允許值裡出現 `/`,只有空白或 `>` 會終止它;`_SCRIPT_OPEN_TAG_PATTERN` 抓的
# `attrs` 已經不含 `>`,故 unquoted 分支只需要在空白處停下)。`(?<![\w-])` 取代單純 `\b`
# 是為了不誤吃 `data-src="..."` 這種以連字號結尾的屬性名——`\b` 在 `-` 和 `s` 之間一樣算
# boundary,會誤判。
_SRC_ATTR_VALUE_PATTERN = re.compile(
    r"""(?<![\w-])src\s*=\s*(?:"([^"]*)"|'([^']*)'|(\S+))""", re.IGNORECASE
)

_JS_STATE_NORMAL = 0
_JS_STATE_SINGLE_QUOTE = 1
_JS_STATE_DOUBLE_QUOTE = 2
_JS_STATE_TEMPLATE = 3
_JS_STATE_LINE_COMMENT = 4
_JS_STATE_BLOCK_COMMENT = 5


def find_script_end(html: str, start_index: int) -> int:
    """從 `start_index` 找真正的 `</script` 終止符(不被字串/註解裡的假 `</script>` 騙到)。
    逐字 port 自 backend `JsSyntaxValidator.findScriptEnd`,兩邊 MUST 保持同步;找不到終止符
    時回傳 `len(html)`。
    """
    length = len(html)
    index = start_index
    state = _JS_STATE_NORMAL
    while index < length:
        character = html[index]
        if state == _JS_STATE_NORMAL:
            if character == "'":
                state = _JS_STATE_SINGLE_QUOTE
                index += 1
            elif character == '"':
                state = _JS_STATE_DOUBLE_QUOTE
                index += 1
            elif character == "`":
                state = _JS_STATE_TEMPLATE
                index += 1
            elif character == "/" and index + 1 < length:
                next_character = html[index + 1]
                if next_character == "/":
                    state = _JS_STATE_LINE_COMMENT
                    index += 2
                elif next_character == "*":
                    state = _JS_STATE_BLOCK_COMMENT
                    index += 2
                else:
                    index += 1
            elif character == "<" and html[index : index + 8].lower() == "</script":
                return index
            else:
                index += 1
        elif state == _JS_STATE_SINGLE_QUOTE:
            if character == "\\":
                index += 2
            elif character == "'":
                state = _JS_STATE_NORMAL
                index += 1
            else:
                index += 1
        elif state == _JS_STATE_DOUBLE_QUOTE:
            if character == "\\":
                index += 2
            elif character == '"':
                state = _JS_STATE_NORMAL
                index += 1
            else:
                index += 1
        elif state == _JS_STATE_TEMPLATE:
            if character == "\\":
                index += 2
            elif character == "`":
                state = _JS_STATE_NORMAL
                index += 1
            else:
                index += 1
        elif state == _JS_STATE_LINE_COMMENT:
            if character == "\n":
                state = _JS_STATE_NORMAL
            index += 1
        else:  # _JS_STATE_BLOCK_COMMENT
            if character == "*" and index + 1 < length and html[index + 1] == "/":
                state = _JS_STATE_NORMAL
                index += 2
            else:
                index += 1
    return length


def mask_strings_and_comments(text: str) -> str:
    """把字串字面值與註解的內文字元換成空白,分隔符與換行不動——遮罩後每個字元 index 與行號
    與原文一比一對應,呼叫端因此不需要再做行號校正。供 brace 配對與 helper 呼叫點掃描共用。
    """
    length = len(text)
    masked_characters = list(text)
    index = 0
    state = _JS_STATE_NORMAL

    def _blank(position: int) -> None:
        if masked_characters[position] != "\n":
            masked_characters[position] = " "

    while index < length:
        character = text[index]
        if state == _JS_STATE_NORMAL:
            if character == "'":
                state = _JS_STATE_SINGLE_QUOTE
                index += 1
            elif character == '"':
                state = _JS_STATE_DOUBLE_QUOTE
                index += 1
            elif character == "`":
                state = _JS_STATE_TEMPLATE
                index += 1
            elif character == "/" and index + 1 < length:
                next_character = text[index + 1]
                if next_character == "/":
                    state = _JS_STATE_LINE_COMMENT
                    index += 2
                elif next_character == "*":
                    state = _JS_STATE_BLOCK_COMMENT
                    index += 2
                else:
                    index += 1
            else:
                index += 1
        elif state in (_JS_STATE_SINGLE_QUOTE, _JS_STATE_DOUBLE_QUOTE, _JS_STATE_TEMPLATE):
            closing_character = {
                _JS_STATE_SINGLE_QUOTE: "'",
                _JS_STATE_DOUBLE_QUOTE: '"',
                _JS_STATE_TEMPLATE: "`",
            }[state]
            if character == "\\":
                _blank(index)
                if index + 1 < length:
                    _blank(index + 1)
                index += 2
            elif character == closing_character:
                state = _JS_STATE_NORMAL
                index += 1
            else:
                _blank(index)
                index += 1
        elif state == _JS_STATE_LINE_COMMENT:
            if character == "\n":
                state = _JS_STATE_NORMAL
            else:
                _blank(index)
            index += 1
        else:  # _JS_STATE_BLOCK_COMMENT
            if character == "*" and index + 1 < length and text[index + 1] == "/":
                state = _JS_STATE_NORMAL
                index += 2
            else:
                _blank(index)
                index += 1

    return "".join(masked_characters)


def extract_inline_script_spans(html: str) -> list[tuple[int, int]]:
    """依文件順序回傳所有內嵌(無 `src=`)`<script>` 區塊在原始 HTML 中的
    `(content_start, content_end)` 字元 offset。`extract_inline_scripts_with_lines` 拿這份轉
    文字＋行號；`_apply_erd_theme`（`theme_rewrite.py`）拿這份把改寫範圍精確限制在 script
    區塊內,HTML body 的可見文字不受影響。有 `src=` 的外部 script(CDN 引入)一律跳過——
    那些內容不是這份 HTML 自己寫的 JS。"""
    spans: list[tuple[int, int]] = []
    search_from = 0
    while True:
        open_tag_match = _SCRIPT_OPEN_TAG_PATTERN.search(html, search_from)
        if open_tag_match is None:
            break

        attrs = open_tag_match.group(1) or ""
        content_start = open_tag_match.end()
        content_end = find_script_end(html, content_start)

        close_gt_index = html.find(">", content_end) if content_end < len(html) else -1
        search_from = close_gt_index + 1 if close_gt_index >= 0 else len(html)

        if _SRC_ATTR_PATTERN.search(attrs):
            continue

        spans.append((content_start, content_end))

    return spans


def extract_inline_scripts_with_lines(html: str) -> list[tuple[str, int]]:
    """依文件順序回傳所有內嵌(無 `src=`)`<script>` 區塊的內文,配對該區塊在原始 HTML
    中的起始行號(1-based;以「內容起點之前的 `\\n` 數 + 1」計算——內容起點緊接在
    `<script...>` 開始標籤結尾之後,故該行號就是內容第一行對應的 HTML 行號)。空白(去除
    前後空白後為空)區塊不回傳——沒有內容可檢查。"""
    scripts: list[tuple[str, int]] = []
    for content_start, content_end in extract_inline_script_spans(html):
        content = html[content_start:content_end]
        if content.strip():
            html_start_line = html.count("\n", 0, content_start) + 1
            scripts.append((content, html_start_line))

    return scripts
