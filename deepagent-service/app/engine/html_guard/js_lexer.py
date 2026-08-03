"""inline `<script>` 內文抽取與字串／註解遮罩的狀態機。

`find_script_end` 逐字 port 自 backend `JsSyntaxValidator.java` 的 `findScriptEnd`,
兩邊 MUST 同步修改。

狀態機認得 regex literal(`_JS_STATE_REGEX`),否則 `/` 後接的引號(例如
`name.replace(/'/g, '')`)會被誤判成開了一個永不閉合的字串,讓 `find_script_end` 找不到
真正的 `</script>`、`extract_inline_script_spans` 之後所有 script block 全部消失。`/` 到底
是除法還是 regex 開頭用 `_is_regex_context` 判斷——見該函式 docstring 的完整規則。
"""

import re
import unicodedata

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
_JS_STATE_REGEX = 6

# 前一個「期待運算子接續」的關鍵字結尾——這些關鍵字本身以識別字字元結尾,但語法上後面
# 一定接表達式,`/` 在它們後面 MUST 判成 regex 開頭,不能套用「識別字結尾→除法」的預設值。
_REGEX_CONTEXT_KEYWORDS = frozenset(
    {
        "return",
        "typeof",
        "instanceof",
        "in",
        "of",
        "new",
        "delete",
        "void",
        "throw",
        "case",
        "do",
        "else",
        "yield",
        "await",
        "extends",
        "default",
    }
)


def _is_word_character(character: str) -> bool:
    """對齊 Java `Character.isLetterOrDigit`——`str.isalnum()` 對某些字元(如½、①、上標數字
    這類 Unicode "No"/Other Number 類別)回傳 True,但它們既非字母也非十進位數字,Java 那邊
    會回傳 False。用 Unicode category 直接對齊:字母類(`L*`)或十進位數字(`Nd`)。"""
    category = unicodedata.category(character)
    return category.startswith("L") or category == "Nd"


def _is_decimal_digit(character: str) -> bool:
    """對齊 Java `Character.isDigit`(十進位數字,Unicode category `Nd`)——`str.isdigit()`
    對同一批 "No" 類字元也會回傳 True,範圍比 Java 那邊寬。"""
    return unicodedata.category(character) == "Nd"


def _is_regex_context(text: str, slash_index: int) -> bool:
    """判斷 `text[slash_index]`(`/`)是 regex literal 的開頭還是除法運算子——不追蹤完整語法
    樹,只看前一個有意義的字元:標準啟發式是「regex 只能出現在期待表達式的位置」。

    前一個字元結尾若是識別字/數字/`)`/`]`(代表前面是一個完整的值:變數、字面值、分組或索引
    運算式的結尾),語法上這裡期待接運算子 → 除法;`return`/`typeof`/`case`/`else` 這類雖然
    以識別字字元結尾、但接下來一定是表達式的關鍵字是例外,仍判為 regex。其餘情況(運算子、
    `(`/`[`/`{`/`,`/`;`/`:`,或檔頭/區塊開頭)期待表達式 → regex。

    `}` 沒有專屬分支,落在預設的「regex」——區塊結尾後接一個陳述式起頭的 regex
    (`if(x){}\n/re/.test(y)`)比物件字面值結尾後直接接除法常見得多,且物件字面值多半接
    在賦值或呼叫參數裡、後面通常不會直接跟一個裸的 `/`。這是刻意的取捨,不是完整判斷。

    `<` 也刻意排除在「期待表達式」之外,回傳 False(除法/惰性字元,不進 regex 狀態)——這兩個
    函式除了純 JS 片段,也被 `rules_tab.py`/`sandbox/errors.py` 直接套用在**整份 HTML**（含
    標籤）上,`</script>`、`</div>` 這類收尾標籤裡的 `/` 前一個字元就是 `<`,若當表達式位置
    處理,regex 狀態會一路吃掉後面的標籤與程式碼,重現一次 C3 本身要修的那種「後面全部消失」。
    犧牲的是`x < /re/.test(y)`這種比較運算子後緊接 regex 的寫法（極罕見）。

    `>` 同樣排除,但有一個例外:箭頭函式 `=>`。前一個字元是 `>` 時再往前看一格——若是 `=`,
    這是箭頭函式的收尾,後面一定接表達式,MUST 判成 regex context;否則(純比較運算子 `>`,
    或 `</script>`/`</div>` 這類收尾標籤的 `>`)維持除法/惰性字元。注意:與 `<` 不同,`>`
    誤判的後果**不是**安全的那種失敗——`=> /'/.test(x)` 這種寫法一旦沒被辨識成 regex,
    regex 內文的引號會被當成開了一個永不閉合的字串,把後面的內容整段吃掉(包括真正的
    `</script>`),不是「那段沒被遮罩」而已。箭頭函式不罕見,這個例外因此是必要的,不是可選
    的取捨。

    與 Java `JsSyntaxValidator.isRegexContext` MUST 保持結構平行,兩個 predicate 逐字對齊
    Java 的語意(而非 Python 字串方法的預設語意)：空白略過用 `str.isspace()`(對齊
    `Character.isWhitespace`,涵蓋 U+3000 全形空白、U+2028 行分隔符等 Java 也算空白的
    字元)；識別字字元用 `_is_word_character`/`_is_decimal_digit`(對齊
    `Character.isLetterOrDigit`/`isDigit`,不能直接用 `str.isalnum()`/`str.isdigit()`——
    後兩者把 ½、①這類 Unicode "No"(Other Number)字元也算進去,Java 不算)。
    """
    position = slash_index - 1
    while position >= 0 and text[position].isspace():
        position -= 1
    if position < 0:
        return True

    previous_character = text[position]
    if previous_character == ">":
        before_arrow = position - 1
        return before_arrow >= 0 and text[before_arrow] == "="
    if previous_character in ")]<":
        return False

    if _is_word_character(previous_character) or previous_character in "_$":
        word_end = position + 1
        word_start = word_end
        while word_start > 0 and (
            _is_word_character(text[word_start - 1]) or text[word_start - 1] in "_$"
        ):
            word_start -= 1
        word = text[word_start:word_end]
        if _is_decimal_digit(word[0]):
            return False  # 數字字面值結尾。
        return word in _REGEX_CONTEXT_KEYWORDS

    return True


def find_script_end(html: str, start_index: int) -> int:
    """從 `start_index` 找真正的 `</script` 終止符(不被字串/註解裡的假 `</script>` 騙到)。
    逐字 port 自 backend `JsSyntaxValidator.findScriptEnd`,兩邊 MUST 保持同步;找不到終止符
    時回傳 `len(html)`。
    """
    length = len(html)
    index = start_index
    state = _JS_STATE_NORMAL
    regex_in_character_class = False
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
                elif _is_regex_context(html, index):
                    state = _JS_STATE_REGEX
                    regex_in_character_class = False
                    index += 1
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
        elif state == _JS_STATE_REGEX:
            if character == "\\":
                index += 2
            elif character == "[":
                regex_in_character_class = True
                index += 1
            elif character == "]":
                regex_in_character_class = False
                index += 1
            elif character == "/" and not regex_in_character_class:
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
    regex_in_character_class = False

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
                elif _is_regex_context(text, index):
                    state = _JS_STATE_REGEX
                    regex_in_character_class = False
                    index += 1
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
        elif state == _JS_STATE_REGEX:
            if character == "\\":
                _blank(index)
                if index + 1 < length:
                    _blank(index + 1)
                index += 2
            elif character == "[":
                regex_in_character_class = True
                index += 1
            elif character == "]":
                regex_in_character_class = False
                index += 1
            elif character == "/" and not regex_in_character_class:
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
