"""dashboard.html 確定性檢查——送出前最後一道關卡。engine 層 stdlib only(禁止 import LLM
框架,ruff TID251 會擋);JS 檢查分兩層:Level 1 語法 parse-only、Level 2 sandbox 執行
smoke,錯誤訊息設計成可直接餵回模型修復。
"""

import contextlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from app.engine.results import referenced_query_ids

# quickjs 是選配的語法檢查依賴(見 pyproject.toml)——runtime import 失敗時只記
# warning、跳過該檢查,不擋主流程(比照 backend JsSyntaxValidator.java 的哲學:驗證器
# 掛掉不能連累 dashboard 送出)。
try:
    import quickjs

    _QUICKJS_AVAILABLE = True
except ImportError:  # pragma: no cover -- exercised via monkeypatch in tests, not a real uninstall
    quickjs = None
    _QUICKJS_AVAILABLE = False

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

HTML_MAX_BYTES = 2_000_000

_ECHARTS_INIT_CALL_PREFIX = "echarts.init("
_REGISTER_THEME_CALL_PREFIX = "registerTheme("

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

# quickjs 的錯誤訊息帶行號,但格式依「是否有呼叫堆疊」而不同:純語法錯誤(parse 階段)無堆疊,
# 執行期錯誤有堆疊、可能多層——多層時「最深」那筆排最前面,故用 `search` 找第一筆而非要求
# 固定前綴(見 _resolve_error_frames)。
_QUICKJS_ERROR_LOCATION_PATTERN = re.compile(r"<input>:(\d+)")
# _check_js_syntax 把每段 script 內容包進 `(function(){\n<content>\n})` 再丟給 quickjs
# eval——只是「定義」這個函式表達式(不呼叫),JS 引擎仍會對函式本體做完整語法解析、但
# 不執行內容,等同 parse-only。包裝多出的這一行前綴要從回報的行號扣掉。
_JS_SYNTAX_CHECK_WRAPPER_LINE_OFFSET = 1


@dataclass
class GuardReport:
    """`check_dashboard_html` 的檢查結果。"""

    ok: bool
    errors: list[str] = field(default_factory=list)
    html: str = ""


def _check_structure(html: str, errors: list[str]) -> None:
    if not html or "<div" not in html:
        errors.append(
            "dashboard.html content is incomplete: missing HTML content or at least one <div> element."
        )
    # 每次 dashboard 修改都是單次完整 write_file(見本檔案 module docstring),真實 dashboard
    # 量到 62855 bytes(約 18K tokens),對比模型輸出 budget 約 24K tokens——輸出在收尾前被
    # 腰斬是活生生的風險,而且腰斬點若剛好落在最後一個 </script> 之後,前面所有檢查都測不出
    # 異狀。要求 </html> 收尾標籤是最低成本的截斷偵測。
    if html and "</html>" not in html:
        errors.append(
            "dashboard.html content is incomplete: missing the closing </html> tag -- the "
            "output was likely truncated mid-generation. Please write the ENTIRE dashboard.html "
            "again in one write_file call, all the way through the closing </html> tag."
        )


def _check_size(html: str, errors: list[str]) -> None:
    byte_length = len(html.encode("utf-8"))
    if byte_length > HTML_MAX_BYTES:
        errors.append(
            f"dashboard.html is too large: {byte_length} bytes, exceeding the {HTML_MAX_BYTES} byte limit. "
            "Please trim the content (e.g. remove redundant comments, embedded data, or duplicate style definitions)."
        )


def _find_script_end(html: str, start_index: int) -> int:
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


def _mask_strings_and_comments(text: str) -> str:
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


def _extract_inline_scripts_with_lines(html: str) -> list[tuple[str, int]]:
    """依文件順序回傳所有內嵌(無 `src=`)`<script>` 區塊的內文,配對該區塊在原始 HTML
    中的起始行號(1-based;以「內容起點之前的 `\\n` 數 + 1」計算——內容起點緊接在
    `<script...>` 開始標籤結尾之後,故該行號就是內容第一行對應的 HTML 行號)。有
    `src=` 的外部 script(CDN 引入)一律跳過——那些內容不是這份 HTML 自己寫的 JS。"""
    scripts: list[tuple[str, int]] = []
    search_from = 0
    while True:
        open_tag_match = _SCRIPT_OPEN_TAG_PATTERN.search(html, search_from)
        if open_tag_match is None:
            break

        attrs = open_tag_match.group(1) or ""
        content_start = open_tag_match.end()
        content_end = _find_script_end(html, content_start)

        close_gt_index = html.find(">", content_end) if content_end < len(html) else -1
        search_from = close_gt_index + 1 if close_gt_index >= 0 else len(html)

        if _SRC_ATTR_PATTERN.search(attrs):
            continue

        content = html[content_start:content_end]
        if content.strip():
            html_start_line = html.count("\n", 0, content_start) + 1
            scripts.append((content, html_start_line))

    return scripts


def _check_js_syntax(html: str, errors: list[str]) -> None:
    """Level 1:每段 script 包進 `(function(){...})` 丟給 quickjs eval,只解析不執行,
    只抓 SyntaxError。quickjs 不可用時記 warning、跳過此規則(驗證器掛掉不擋主流程)。
    """
    if not _QUICKJS_AVAILABLE:
        logger.warning("html_guard: quickjs 未安裝，跳過 JS 語法檢查")
        return

    for script_index, (script_content, html_start_line) in enumerate(
        _extract_inline_scripts_with_lines(html)
    ):
        wrapped_source = f"(function(){{\n{script_content}\n}})"
        try:
            quickjs.Context().eval(wrapped_source)
        except quickjs.JSException as syntax_error:
            message = str(syntax_error)
            location_match = _QUICKJS_ERROR_LOCATION_PATTERN.search(message)
            if location_match:
                script_relative_line = max(
                    int(location_match.group(1)) - _JS_SYNTAX_CHECK_WRAPPER_LINE_OFFSET, 1
                )
                html_line = html_start_line + script_relative_line - 1
            else:
                html_line = html_start_line
            first_line = message.splitlines()[0] if message else message
            errors.append(f"script#{script_index} line {html_line} JS syntax error: {first_line}")
        except Exception as unexpected_error:  # noqa: BLE001 -- 驗證器掛掉不擋主流程
            logger.warning(
                "html_guard: quickjs 檢查 script#%d 時發生非預期例外，跳過該段檢查: %s",
                script_index,
                unexpected_error,
            )


# -- Level 2: sandboxed execution smoke ------------------------------------------------
#
# 真的 eval 每段 inline script(不只 parse),在一個 absorb-all 的假 DOM/ECharts sandbox
# 裡跑,抓「未宣告變數」「對 undefined 取屬性」這類 Level 1(parse-only)看不到的 runtime
# 錯誤。
#
# multi-mole 掃描:單一 block 內若有多個未宣告變數,逐一執行只會在第一個就 throw、停在
# 那裡。做法:每次 block 拋 ReferenceError 就記錄(含行號)、把該變數名加入 stub 集合、
# 用新 stub 集合重建一個全新 Context(同一 context 對含 `const` 宣告的 block 重跑會撞
# redeclaration SyntaxError)、把該 block 之前的所有 block 在新 context 裡靜默重放、
# 再重跑這個 block,直到不再拋新的 ReferenceError 或達重試上限(見
# `_MAX_REFERENCE_ERROR_RETRIES_PER_BLOCK`)。非 ReferenceError 的例外一樣記錄但不重試,
# 沿用現有 context 繼續掃下一個 block。

# absorb-all sandbox：`window`/`document`/`echarts` 用 Proxy 吸收任何屬性存取與呼叫鏈；
# `document.getElementById`／`echarts.init` 回傳同款 absorb 物件、`setOption`/`resize`
# 一律 no-op；`querySelectorAll` 回真正的空陣列；`addEventListener` 對
# 'DOMContentLoaded'/'load' 同步立即呼叫 callback（多數 dashboard 邏輯都包在這裡面）；
# `console.*`／`setTimeout`(立即呼叫)／`Event`/`CustomEvent` 也一併補上，覆蓋 skill
# examples 實際用到的 DOM API 面。
#
# `set` trap 把值寫回閉包的 `table`（不能只回傳 `true` 卻不存，否則
# `window.__ERD_RESULTS__ = {...}` 會被靜默吞掉，後續讀取拿到空殼）。
#
# `console.error` 不是純 no-op：skill 規定每張圖的 init+setOption 包 try/catch,catch 裡
# 呼叫 `console.error('[ERD] chart <名稱> failed:', error)`——這條防線會把 Level 2 想抓的
# 未捕捉例外整個吞掉。`console.error` 因此改為收集器：把參數字串化推進
# `__erd_console_errors__`；`_execute_scripts_smoke` 跑完所有 block 後讀出這個陣列，把
# `[ERD] chart ` 開頭的訊息轉成 guard error，讓「被 try/catch 擋下的執行期錯誤」也能被
# 確定性攔下。
#
# skill 的 catch 範本會在 catch 內做 `setTimeout(() => { throw error; }, 0)`（下一輪
# tick 才重拋，讓錯誤浮上真實瀏覽器的 `window.onerror` 又不中斷同一 block 後續初始化）。
# 但 sandbox 的 `setTimeout` 是同步立即呼叫，若不處理會把這個重拋當場變成未捕捉例外、
# 誤判正常的 catch 範例為壞掉。故 `setTimeout` stub 用 try/catch 吞掉 callback 例外——
# 真正的訊號來源是上面的 `console.error` 收集器。
#
# `document.getElementById`/`querySelector('#id')` 不是無條件 absorb-all：真瀏覽器裡
# `getElementById` 對不存在的 id 回傳 `null`,對 `null` 取屬性/賦值會拋 TypeError；
# absorb-all stub 永遠回一個吸收一切的 Proxy,這類「引用不存在的 DOM id」錯誤物理上不可能
# 重現。`check_dashboard_html` 用 `_extract_known_element_ids` 從整份 HTML 掃出所有
# 實際出現過的 `id="..."` 字面值,序列化成 JS `Set` 灌進 sandbox；`getElementById(id)`
# 對集合內的 id 回 absorb 元素、集合外回 `null`,與真瀏覽器同語意，對動態拼接的 id 字串
# 同樣有效（只要拼出來的字面值在 HTML 某處真的存在）。`querySelector` 只在引數是簡單的
# `#id` 形式時比照處理；其餘複雜選擇器維持回傳 absorb 元素——不是完整的 CSS selector
# engine，是刻意保留的界線。
_SANDBOX_PRELUDE = r"""
function __erdMakeAbsorb() {
  var handler = {
    get: function (target, prop) {
      if (typeof prop === "symbol") {
        return undefined;
      }
      if (prop === "toString" || prop === "valueOf") {
        return function () { return ""; };
      }
      if (prop === "length") {
        return 0;
      }
      return __erdMakeAbsorb();
    },
    apply: function () {
      return __erdMakeAbsorb();
    },
    construct: function () {
      return __erdMakeAbsorb();
    },
    set: function () {
      return true;
    },
    has: function () {
      return true;
    },
  };
  return new Proxy(function () {}, handler);
}

function __erdMakeDomLike(overrides) {
  var table = overrides || {};
  var handler = {
    get: function (target, prop) {
      if (typeof prop === "symbol") {
        return undefined;
      }
      if (Object.prototype.hasOwnProperty.call(table, prop)) {
        return table[prop];
      }
      return __erdMakeAbsorb();
    },
    set: function (target, prop, value) {
      table[prop] = value;
      return true;
    },
    has: function () {
      return true;
    },
  };
  return new Proxy(function () {}, handler);
}

function __erdAddEventListenerSync(eventName, callback) {
  if (eventName === "DOMContentLoaded" || eventName === "load") {
    if (typeof callback === "function") {
      callback();
    }
  }
}

// Populated per-context by `_build_sandbox_context` from `_extract_known_element_ids(html)` --
// starts empty here only as a safe (fail-toward-null, not fail-toward-absorb) default.
var __erd_known_element_ids__ = new Set();

function __erdGetElementById(elementId) {
  if (__erd_known_element_ids__.has(elementId)) {
    return __erdMakeAbsorb();
  }
  return null;
}

// Only the simple `#id` selector form (safe identifier chars only) is recognized -- see module
// comment above `_SANDBOX_PRELUDE` for why. Anything else falls through to absorb-all.
var __erdSimpleIdSelectorPattern = /^#([A-Za-z0-9_-]+)$/;

function __erdQuerySelector(selector) {
  if (typeof selector === "string") {
    var match = __erdSimpleIdSelectorPattern.exec(selector);
    if (match) {
      return __erdGetElementById(match[1]);
    }
  }
  return __erdMakeAbsorb();
}

var document = __erdMakeDomLike({
  getElementById: __erdGetElementById,
  querySelector: __erdQuerySelector,
  querySelectorAll: function () { return []; },
  getElementsByClassName: function () { return []; },
  getElementsByTagName: function () { return []; },
  createElement: function () { return __erdMakeAbsorb(); },
  addEventListener: __erdAddEventListenerSync,
});

var window = __erdMakeDomLike({
  document: document,
  addEventListener: __erdAddEventListenerSync,
  __ERD_RESULTS__: {},
  location: __erdMakeAbsorb(),
  navigator: __erdMakeAbsorb(),
});

var echarts = __erdMakeDomLike({
  init: function () { return __erdMakeAbsorb(); },
});

var __erd_console_errors__ = [];
// getCol 樣板找不到欄位時只 console.warn 回 -1(skill 規定的防禦式契約)——不收集就永遠
// 攔不到綁錯欄位。stack 讓 Python 端算出呼叫點行號,base 是本段 script 在 HTML 的起始行。
var __erd_console_warnings__ = [];
var __erd_block_start_line__ = 1;
var console = {
  log: function () {},
  warn: function () {
    var stringifiedArguments = [];
    for (var argumentIndex = 0; argumentIndex < arguments.length; argumentIndex++) {
      stringifiedArguments.push(String(arguments[argumentIndex]));
    }
    __erd_console_warnings__.push({
      message: stringifiedArguments.join(" "),
      stack: String((new Error()).stack || ""),
      base: __erd_block_start_line__,
    });
  },
  error: function () {
    var stringifiedArguments = [];
    for (var argumentIndex = 0; argumentIndex < arguments.length; argumentIndex++) {
      stringifiedArguments.push(String(arguments[argumentIndex]));
    }
    __erd_console_errors__.push(stringifiedArguments.join(" "));
  },
};

function setTimeout(callback) {
  if (typeof callback === "function") {
    try {
      callback();
    } catch (deferredCallbackError) {
      // Swallowed on purpose: a real browser runs this on a future tick, so it must not
      // abort the synchronous script that scheduled it. The skill's chart catch blocks use
      // `setTimeout(() => { throw error; }, 0)` for async rethrow (see module comment above);
      // the guard's actual detection signal for that case is the console.error collector, not
      // this callback's exception.
    }
  }
  return 0;
}
function clearTimeout() {}
function setInterval() { return 0; }
function clearInterval() {}
function Event(eventType) { this.type = eventType; }
function CustomEvent(eventType, options) { this.type = eventType; this.detail = options && options.detail; }
"""

# quickjs Context 每次 eval() 的 CPU time budget（每段 script 各自重新計時）。
_SANDBOX_TIME_LIMIT_SECONDS = 2.0
_SANDBOX_ERROR_MESSAGE_MAX_LENGTH = 150

# 沒有這道上限時,一個不斷配置陣列的無窮迴圈可以在被 `_SANDBOX_TIME_LIMIT_SECONDS` 攔下前
# 撐爆行程記憶體(實測 4025MB peak RSS、8.39s)——在有記憶體限制的容器裡會 OOM-kill 整個
# process,拖垮所有並發 session。64MB 對真實 dashboard script(灌了截斷後的 seed 資料,見
# `_SANDBOX_SEED_ROW_LIMIT`)綽綽有餘,對失控配置則會快速丟 out-of-memory 例外。
_SANDBOX_MEMORY_LIMIT_BYTES = 64 * 1024 * 1024
# quickjs 預設 256kB;維持預設值即可攔住失控遞迴,這裡明確設定只是讓上限成為看得到的常數。
_SANDBOX_MAX_STACK_SIZE_BYTES = 256 * 1024

# 跨整個 `_execute_scripts_smoke` 呼叫(所有 script block、所有 ReferenceError 重試與 context
# 重建)的全域 wall-clock 上限。單一 block 的 `_SANDBOX_TIME_LIMIT_SECONDS` 各自重新計時,
# 無法擋住「多個 block 各自安全,但 ReferenceError 重試迴圈反覆重建 context、重放前面所有
# block」這種總時間不設限的情況(實測病態輸入跑到 56.93s)。超過此上限時優雅降級——記
# warning、回傳目前已收集到的結果,不 raise——比照本檔案開頭的哲學:驗證器失敗不能擋
# dashboard 送出。
_SANDBOX_GLOBAL_DEADLINE_SECONDS = 10.0

# 對每個 available_query_id 灌一份「真實形狀」的假資料：欄位/列都齊全，讓正常存取
# `.columns`/`.rows`/`.truncated` 的程式碼安全跑過，未宣告變數等錯誤依然如實炸出來。
# 這是沒有真實 `results` 時的 fallback（見 `_results_literal_for_sandbox`）。
_FAKE_RESULT_COLUMNS: tuple[str, ...] = ("__c0", "__c1")
_FAKE_RESULT_ROWS: tuple[tuple[object, ...], ...] = (("x", 1),)

# 真實 `results` 灌進 sandbox 時只取前幾列——夠讓欄位存在的閘門打開、`.rows[0]` 這類存取
# 有東西可讀，不需要整份資料拖慢每次 `_build_sandbox_context` 重建。
_SANDBOX_SEED_ROW_LIMIT = 3


def _results_literal_for_sandbox(
    available_query_ids: set[str], results: dict[str, dict] | None
) -> str:
    """建構灌進 sandbox `window.__ERD_RESULTS__` 的假資料 JSON。有真實 `results` 時用真實
    欄名與前幾列真實資料,讓按欄名查找的程式碼閘門真的打開;缺資料的 query_id 退回泛用假資料。
    """
    fake_results: dict[str, dict] = {}
    for query_id in available_query_ids:
        real_result = results.get(query_id) if results else None
        if real_result is not None and "columns" in real_result and "rows" in real_result:
            fake_results[query_id] = {
                "columns": real_result["columns"],
                "rows": real_result["rows"][:_SANDBOX_SEED_ROW_LIMIT],
                "truncated": bool(real_result.get("truncated", False)),
            }
        else:
            fake_results[query_id] = {
                "columns": list(_FAKE_RESULT_COLUMNS),
                "rows": [list(row) for row in _FAKE_RESULT_ROWS],
                "truncated": False,
            }
    return json.dumps(fake_results)


# 掃整份 HTML(markup 與 script 皆含)裡所有 `id="..."` 屬性字面值,餵給 sandbox 的
# `getElementById`/`querySelector('#id')` 做 id 擬真(見 `_SANDBOX_PRELUDE`)。不限定
# 標籤種類,多抓無害。動態拼接的 id 字串不需要 Python 端理解拼接邏輯——只要拼出來的字面值
# 本身在 HTML 某處真的存在,sandbox 執行期 `Set.has(...)` 就會命中。
_ELEMENT_ID_ATTRIBUTE_PATTERN = re.compile(r"""\bid\s*=\s*(["'])([^"']*)\1""")


def _extract_known_element_ids(html: str) -> set[str]:
    return {match.group(2) for match in _ELEMENT_ID_ATTRIBUTE_PATTERN.finditer(html)}


# ReferenceError 訊息長這樣:"ReferenceError: 'timeout' is not defined"——抓變數名出來
# 加進 stub 集合。白名單校驗(_JS_IDENTIFIER_PATTERN)是防禦性的:抓到的變數名要拼進
# `globalThis.<name> = ...` 源碼字串,驗證失敗就退化成一般例外處理、不重試。
_REFERENCE_ERROR_VAR_PATTERN = re.compile(r"ReferenceError: '([^']+)' is not defined")
_JS_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")

# 單一 block 內「發現新未宣告變數→重建 context→重試」的次數上限,防止 stub 機制失靈時
# 陷入無窮重試迴圈。
_MAX_REFERENCE_ERROR_RETRIES_PER_BLOCK = 8


def _build_sandbox_context(
    available_query_ids: set[str],
    stub_variable_names: set[str],
    results: dict[str, dict] | None = None,
    known_element_ids: frozenset[str] = frozenset(),
) -> "quickjs.Context":
    """建一個全新 quickjs Context,灌入 prelude、假 `__ERD_RESULTS__`、已知 element id 與目前
    已收集的 stub 變數(各自指到 absorb-all proxy)。同時設 memory/stack 上限——只靠
    `set_time_limit` 攔不住迴圈在超時前先吃光記憶體。"""
    context = quickjs.Context()
    context.set_time_limit(_SANDBOX_TIME_LIMIT_SECONDS)
    context.set_memory_limit(_SANDBOX_MEMORY_LIMIT_BYTES)
    context.set_max_stack_size(_SANDBOX_MAX_STACK_SIZE_BYTES)
    context.eval(_SANDBOX_PRELUDE)
    context.eval(
        f"window.__ERD_RESULTS__ = {_results_literal_for_sandbox(available_query_ids, results)};"
    )
    context.eval(f"__erd_known_element_ids__ = new Set({json.dumps(sorted(known_element_ids))});")
    for variable_name in stub_variable_names:
        context.eval(f"globalThis.{variable_name} = __erdMakeAbsorb();")
    return context


# `_SANDBOX_PRELUDE` 是獨立 eval 的,它自己內部函式(名稱一律 `__erd` 前綴)以及補上的
# DOM/timer stub 在 stack 裡的行號是相對 prelude 原始碼算的,不是相對 HTML——一旦這類
# frame 混進 `_resolve_error_frames`/`_stack_frame_lines` 的結果,換算出的「HTML 行號」
# 就是憑空捏造的。兩處共用同一份名單,不重複寫字面值。
_SANDBOX_INTERNAL_FRAME_NAME_PREFIX = "__erd"
_SANDBOX_INTERNAL_FRAME_NAMES: frozenset[str] = frozenset(
    {"warn", "setTimeout", "clearTimeout", "setInterval", "clearInterval", "Event", "CustomEvent"}
)


def _is_sandbox_internal_frame_name(function_name: str) -> bool:
    return function_name.startswith(_SANDBOX_INTERNAL_FRAME_NAME_PREFIX) or (
        function_name in _SANDBOX_INTERNAL_FRAME_NAMES
    )


def _resolve_error_frames(
    message: str, html_start_line: int, html_line_count: int
) -> list[tuple[str, int]]:
    """把 quickjs 執行期錯誤的 stack 換算成 [(函式名, HTML 絕對行號)],由深到淺;沒有行號的
    frame 略過,純語法錯誤(無 stack)回空列表。跳過 `_SANDBOX_PRELUDE` 內部 frame 與換算後
    超出 `html_line_count` 的行號——兩者都不對應真實 HTML 位置,寧可少報一層呼叫也不亂報行號。
    """
    frames: list[tuple[str, int]] = []
    for frame_match in _STACK_FRAME_PATTERN.finditer(message):
        if frame_match.group(2) is None:
            continue
        function_name = frame_match.group(1)
        if _is_sandbox_internal_frame_name(function_name):
            continue
        resolved_line = html_start_line + int(frame_match.group(2)) - 1
        if resolved_line > html_line_count:
            continue
        frames.append((function_name, resolved_line))
    return frames


# 共用 helper 的呼叫點:`name(` 出現處,排除 `function name(` 這個定義本身——後者是宣告,
# 不是呼叫,不該被列進「這裡也可能綁錯」的清單。掃描前先用 `_mask_strings_and_comments`
# 遮罩,避免註解或字串裡提到的 helper 名稱(例如 `// call getCol(...)`)被誤判成真呼叫點
# ——那種行號寫進修復 prompt 只會叫模型去改文字說明,幫不上忙。
def _helper_call_site_lines(html: str, helper_name: str) -> list[int]:
    call_pattern = re.compile(rf"(?<![\w$.]){re.escape(helper_name)}\s*\(")
    definition_pattern = re.compile(rf"function\s+{re.escape(helper_name)}\s*\(")
    call_site_lines: list[int] = []
    for line_index, line_text in enumerate(_mask_strings_and_comments(html).splitlines(), start=1):
        if definition_pattern.search(line_text):
            continue
        if call_pattern.search(line_text):
            call_site_lines.append(line_index)
    return call_site_lines


def _format_execution_error(
    frames: list[tuple[str, int]], script_index: int, first_line: str, html: str
) -> str:
    """Builds the error message fed to the repair prompt. Top-level throws report the throw
    line as-is; throws inside a shared helper (e.g. `getCol`, called from every chart block)
    report the call site instead and list every other call site of that helper, since the same
    defect usually hits all of them. No stack at all falls back to the generic
    `script#N execution error` format.
    """
    if not frames:
        truncated_message = first_line[:_SANDBOX_ERROR_MESSAGE_MAX_LENGTH]
        return f"script#{script_index} execution error: {truncated_message}"

    throwing_function_name, throw_line = frames[0]

    variable_match = _REFERENCE_ERROR_VAR_PATTERN.search(first_line)
    headline = (
        f"ReferenceError '{variable_match.group(1)}' is not defined"
        if variable_match
        else first_line[:_SANDBOX_ERROR_MESSAGE_MAX_LENGTH]
    )

    if len(frames) < 2 or throwing_function_name == "<eval>":
        return f"Line {throw_line}: {headline}"

    # Call-site substitution is only correct when the throwing function is genuinely shared --
    # otherwise a function called from exactly one place would have its real bug line replaced
    # by the blameless invocation line. Reuse the same call-site count that gates the hint below.
    call_site_lines = _helper_call_site_lines(html, throwing_function_name)
    if len(call_site_lines) < 2:
        return f"Line {throw_line}: {headline}"

    call_site_line = frames[1][1]
    shared_helper_hint = (
        f" `{throwing_function_name}` is a shared helper called at lines "
        f"{', '.join(str(line) for line in call_site_lines)} -- the same defect very likely "
        "affects every one of them; fix them all in this round."
    )
    return (
        f"Line {call_site_line}: {headline} (thrown inside `{throwing_function_name}` "
        f"at line {throw_line}).{shared_helper_hint}"
    )


# skill 規定的 chart try/catch 範本固定寫法:`console.error('[ERD] chart <名稱> failed:',
# error)`。`.+?` 非貪婪比對名稱(可能含空格/連字號),`re.DOTALL` 讓底層錯誤訊息可跨行。
_CHART_CONSOLE_ERROR_PATTERN = re.compile(r"^\[ERD\] chart (.+?) failed:\s*(.*)$", re.DOTALL)


def _read_collected_console_errors(context: "quickjs.Context") -> list[str]:
    """讀出 sandbox `console.error` 收集器(見 `_SANDBOX_PRELUDE`)目前累積的訊息列表。

    只在所有 block 跑完後呼叫一次,讀的是最終那個 context(重試時重建的舊 context 執行
    紀錄從未被讀取,不會重複計入)。讀取失敗記 warning、回空列表,不擋主流程。
    """
    try:
        serialized_errors = context.eval("JSON.stringify(__erd_console_errors__)")
        return json.loads(serialized_errors)
    except Exception as read_error:  # noqa: BLE001 -- 驗證器掛掉不擋主流程
        logger.warning(
            "html_guard: 讀取 sandbox console.error 收集結果失敗，跳過偵測: %s", read_error
        )
        return []


def _check_swallowed_chart_errors(console_error_messages: list[str]) -> list[str]:
    """把符合 `[ERD] chart <名稱> failed: ...` 格式的 `console.error` 訊息轉成 guard
    error——這些是被 try/catch 擋下、不會冒出 quickjs.JSException 的執行期錯誤,不轉成
    guard error 就永遠不會被攔到。非此格式的 `console.error` 一律忽略,不誤傷。
    """
    errors: list[str] = []
    for message in console_error_messages:
        match = _CHART_CONSOLE_ERROR_PATTERN.match(message)
        if match is None:
            continue
        chart_name = match.group(1)
        underlying_error = match.group(2)[:_SANDBOX_ERROR_MESSAGE_MAX_LENGTH]
        errors.append(
            f"Chart '{chart_name}' threw at runtime (caught by its try/catch): "
            f"{underlying_error}. Fix the underlying error — the try/catch is damage "
            "control, not a fix."
        )
    return errors


# getCol 樣板的固定寫法:`console.warn('[ERD] column not found:', candidates)`;candidates 是
# 陣列,`String(array)` 會變成逗號串接的字串。
_COLUMN_NOT_FOUND_PATTERN = re.compile(r"^\[ERD\] column not found:\s*(.*)$", re.DOTALL)

# 一次退貨最多列幾條 getCol miss——修復 prompt 不能無限長,超出的用一行摘要帶過。
_MAX_REPORTED_COLUMN_MISSES = 8

# `(new Error()).stack` 的單一 frame:`    at <name> (<input>[:line])`。
_STACK_FRAME_PATTERN = re.compile(r"^\s*at\s+(\S+)\s+\(<input>(?::(\d+))?\)", re.MULTILINE)


def _stack_frame_lines(stack_text: str) -> list[int]:
    """回傳 stack 由深到淺、帶行號的 frame 行號列表,略過 sandbox 自己的 frame(見
    `_is_sandbox_internal_frame_name`)。quickjs 對部分 frame 不給行號,那些一律略過。"""
    frame_lines: list[int] = []
    for frame_match in _STACK_FRAME_PATTERN.finditer(stack_text):
        if _is_sandbox_internal_frame_name(frame_match.group(1)):
            continue
        if frame_match.group(2) is None:
            continue
        frame_lines.append(int(frame_match.group(2)))
    return frame_lines


def _resolve_stack_call_site_line(stack_text: str, block_start_line: int) -> int | None:
    """把 `(new Error()).stack` 換算成呼叫點的 HTML 絕對行號——stack 第一筆帶行號的 frame
    是 getCol 內部的 `console.warn`,第二筆才是真正呼叫點;只有一筆時防禦性地退回用那一筆。
    """
    frame_lines = _stack_frame_lines(stack_text)
    if len(frame_lines) >= 2:
        relative_line = frame_lines[1]
    elif frame_lines:
        relative_line = frame_lines[0]
    else:
        return None
    return block_start_line + relative_line - 1


def _owning_query_ids_for_column(column_name: str, results: dict[str, dict]) -> list[str]:
    """哪些 query result 真的有這個欄位——讓退貨訊息能直接寫出「該欄位存在於 qN」。"""
    return sorted(
        query_id
        for query_id, result in results.items()
        if column_name in (result.get("columns") or [])
    )


def _read_collected_console_warnings(context: "quickjs.Context") -> list[dict]:
    """讀出 sandbox `console.warn` 收集器(見 `_SANDBOX_PRELUDE`)目前累積的紀錄。讀取
    失敗記 warning、回空列表,不擋主流程。"""
    try:
        serialized_warnings = context.eval("JSON.stringify(__erd_console_warnings__)")
        return json.loads(serialized_warnings)
    except Exception as read_error:  # noqa: BLE001 -- 驗證器掛掉不擋主流程
        logger.warning(
            "html_guard: 讀取 sandbox console.warn 收集結果失敗，跳過偵測: %s", read_error
        )
        return []


def _check_column_not_found_warnings(
    collected_warnings: list[dict], results: dict[str, dict], html_lines: list[str]
) -> list[str]:
    """把 `[ERD] column not found: ...` 的 warn 轉成 guard error。

    行號取 stack 的呼叫點 frame(見 `_resolve_stack_call_site_line`);候選欄位再回頭比對
    真實 `results`,算出「該欄位其實在哪個 qN」,讓模型一輪修完而不是猜。
    """
    errors: list[str] = []
    seen_call_sites: set[tuple[int, str]] = set()
    for warning in collected_warnings:
        message_match = _COLUMN_NOT_FOUND_PATTERN.match(str(warning.get("message", "")))
        if message_match is None:
            continue
        candidate_columns = [
            part.strip() for part in message_match.group(1).split(",") if part.strip()
        ]
        if not candidate_columns:
            continue

        block_start_line = int(warning.get("base", 1))
        html_line = _resolve_stack_call_site_line(str(warning.get("stack", "")), block_start_line)

        deduplication_key = (html_line or -1, ",".join(candidate_columns))
        if deduplication_key in seen_call_sites:
            continue
        seen_call_sites.add(deduplication_key)

        location_hint = f"Line {html_line}: " if html_line is not None else ""
        source_line = (
            html_lines[html_line - 1].strip()[:120]
            if html_line is not None and 0 < html_line <= len(html_lines)
            else ""
        )
        owning_hints = []
        for candidate_column in candidate_columns:
            owning_query_ids = _owning_query_ids_for_column(candidate_column, results)
            if owning_query_ids:
                owning_hints.append(f"'{candidate_column}' exists in {', '.join(owning_query_ids)}")
        owning_text = (
            " ".join(owning_hints)
            if owning_hints
            else "None of these columns exist in any query result -- run the query you actually need."
        )
        errors.append(
            f"{location_hint}getCol found none of {candidate_columns} in the columns passed here, "
            f"so it returned -1 and this block renders blank/undefined/NaN. {owning_text}. "
            f"Bind the correct query id here. Source: {source_line}"
        )

    if len(errors) > _MAX_REPORTED_COLUMN_MISSES:
        hidden_count = len(errors) - _MAX_REPORTED_COLUMN_MISSES
        errors = errors[:_MAX_REPORTED_COLUMN_MISSES]
        errors.append(f"... and {hidden_count} more getCol misses with the same root cause.")
    return errors


def _execute_scripts_smoke(
    script_blocks_with_lines: list[tuple[str, int]],
    available_query_ids: set[str],
    results: dict[str, dict] | None = None,
    known_element_ids: frozenset[str] = frozenset(),
    html: str = "",
) -> list[str]:
    """Level 2:在 quickjs sandbox 內真的執行(不只 parse)每段 inline script,抓 Level 1
    看不到的 runtime 錯誤;quickjs 不可用時記 warning、跳過。`results` 提供時灌真實欄名/
    資料,讓按欄名查找的閘門真的打開;`known_element_ids` 讓引用不存在的 id 如實回傳 `null`。
    sandbox 只灌 production 實際會注入的子集(`referenced_query_ids(html)`),不是完整的
    `available_query_ids`——否則 regex 抓不到、production 也沒注入資料的寫法(例如 dot
    access 或動態 key)會在 sandbox 裡意外拿到資料、guard 誤判過關。

    單一 block 拋 ReferenceError 時記錄該錯誤、把變數 stub 成 absorb-all proxy、重建全新
    context 並靜默重放之前所有 block,再重跑這個 block,直到不再拋新的 ReferenceError 或達
    `_MAX_REFERENCE_ERROR_RETRIES_PER_BLOCK` 次;非 ReferenceError 的例外記錄但不重試。

    所有 block 跑完後,額外把被 chart try/catch 擋下的執行期錯誤(`console.error` 收集器)
    與 getCol 找不到欄位的訊號(`console.warn` 收集器)轉成 guard error。
    """
    if not _QUICKJS_AVAILABLE:
        logger.warning("html_guard: quickjs 未安裝，跳過 JS 執行檢查")
        return []

    errors: list[str] = []
    stub_variable_names: set[str] = set()
    html_line_count = len(html.splitlines())
    # 只灌 production 實際會注入的子集(見上方函式說明),不是完整的 available_query_ids。
    seeded_query_ids = referenced_query_ids(html)
    deadline_start_time = time.monotonic()
    deadline_exceeded = False

    try:
        context = _build_sandbox_context(
            seeded_query_ids, stub_variable_names, results, known_element_ids
        )
    except Exception as sandbox_init_error:  # noqa: BLE001 -- 驗證器掛掉不擋主流程
        logger.warning("html_guard: sandbox 初始化失敗，跳過 JS 執行檢查: %s", sandbox_init_error)
        return []

    for script_index, (script_content, html_start_line) in enumerate(script_blocks_with_lines):
        if deadline_exceeded:
            break
        retry_count = 0
        while True:
            if time.monotonic() - deadline_start_time > _SANDBOX_GLOBAL_DEADLINE_SECONDS:
                logger.warning(
                    "html_guard: Level 2 sandbox 執行超過全域 deadline(%.0fs)，提前結束、"
                    "回傳目前已收集到的結果（驗證器降級不擋主流程）",
                    _SANDBOX_GLOBAL_DEADLINE_SECONDS,
                )
                deadline_exceeded = True
                break
            try:
                context.eval(f"__erd_block_start_line__ = {html_start_line};")
                context.eval(script_content)
                break
            except quickjs.JSException as runtime_error:
                message = str(runtime_error)
                first_line = message.splitlines()[0] if message else message

                if "interrupted" in first_line.lower():
                    errors.append(
                        f"script#{script_index} execution timed out (possible infinite loop)"
                    )
                    break

                frames = _resolve_error_frames(message, html_start_line, html_line_count)
                errors.append(_format_execution_error(frames, script_index, first_line, html))

                variable_match = _REFERENCE_ERROR_VAR_PATTERN.search(first_line)
                undeclared_variable = variable_match.group(1) if variable_match else None
                can_retry_with_new_stub = (
                    undeclared_variable is not None
                    and undeclared_variable not in stub_variable_names
                    and _JS_IDENTIFIER_PATTERN.fullmatch(undeclared_variable) is not None
                    and retry_count < _MAX_REFERENCE_ERROR_RETRIES_PER_BLOCK
                )
                if not can_retry_with_new_stub:
                    break

                # 重建 context + 重放前面所有 block 是這個迴圈最貴的一步(見模組上方對
                # `_execute_scripts_smoke` 的說明:總耗時沒有上限的病態情況就是這裡)——開始
                # 之前再檢查一次 deadline,不要讓一次重放本身就把 wall clock 燒穿。
                if time.monotonic() - deadline_start_time > _SANDBOX_GLOBAL_DEADLINE_SECONDS:
                    logger.warning(
                        "html_guard: Level 2 sandbox 重試迴圈超過全域 deadline(%.0fs)，"
                        "放棄剩餘重試、回傳目前已收集到的結果（驗證器降級不擋主流程）",
                        _SANDBOX_GLOBAL_DEADLINE_SECONDS,
                    )
                    deadline_exceeded = True
                    break

                stub_variable_names.add(undeclared_variable)
                retry_count += 1
                try:
                    context = _build_sandbox_context(
                        seeded_query_ids, stub_variable_names, results, known_element_ids
                    )
                    for earlier_content, earlier_start_line in script_blocks_with_lines[
                        :script_index
                    ]:
                        # 只求重建到「當前 block 前」該有的宣告狀態——這些 block 自己的
                        # 錯誤已在第一輪掃描時記錄過，重放時如實重現也不重複記錄。base
                        # line 也要重放，否則重放期間觸發的 warn 會帶著錯誤的 base。
                        with contextlib.suppress(Exception):
                            context.eval(f"__erd_block_start_line__ = {earlier_start_line};")
                            context.eval(earlier_content)
                except Exception as rebuild_error:  # noqa: BLE001 -- 驗證器掛掉不擋主流程
                    logger.warning(
                        "html_guard: quickjs 重建 sandbox context 失敗，跳過後續執行檢查: %s",
                        rebuild_error,
                    )
                    return errors
                # continue -- 用重建後的 context 重跑同一個 block
            except Exception as unexpected_error:  # noqa: BLE001 -- 驗證器掛掉不擋主流程
                logger.warning(
                    "html_guard: quickjs 執行檢查 script#%d 時發生非預期例外，跳過該段檢查: %s",
                    script_index,
                    unexpected_error,
                )
                break

    errors.extend(_check_swallowed_chart_errors(_read_collected_console_errors(context)))
    # 只有整份 results 都是真實欄名時才判定 getCol miss——退回泛用假欄名(__c0/__c1)時
    # 每個 getCol 都會 miss，轉成 error 會全是誤報。
    if results is not None and available_query_ids <= set(results):
        errors.extend(
            _check_column_not_found_warnings(
                _read_collected_console_warnings(context), results, html.splitlines()
            )
        )
    return errors


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
    """在遮罩過的 HTML(見 `_mask_strings_and_comments`)裡找名為 `function_name` 的函式
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
    masked_html = _mask_strings_and_comments(html)

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
    for tag_match in _SCRIPT_OPEN_TAG_PATTERN.finditer(html):
        attrs = tag_match.group(1) or ""
        src_match = _SRC_ATTR_VALUE_PATTERN.search(attrs)
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

    _check_structure(html, errors)
    _check_size(html, errors)
    _check_script_src_whitelist(html, errors)
    _check_no_register_theme(html, errors)
    _check_referenced_query_ids(html, available_query_ids, errors)

    errors_before_syntax_check = len(errors)
    _check_js_syntax(html, errors)
    if len(errors) == errors_before_syntax_check:
        # Level 2 只在語法乾淨時跑——語法已錯就不必(也不該)真的執行那段壞掉的 script。
        known_element_ids = frozenset(_extract_known_element_ids(html))
        errors.extend(
            _execute_scripts_smoke(
                _extract_inline_scripts_with_lines(html),
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
