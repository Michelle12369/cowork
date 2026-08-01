"""dashboard.html 確定性檢查——DASHBOARD_HTML 發送前的最後一道確定性關卡。

engine 層——stdlib only,禁止 import 任何 LLM 框架(ruff TID251 會擋)。

檢查涵蓋:結構完整性、體積上限、CDN 白名單、`__ERD_RESULTS__` 引用一致性、erd ECharts
主題強制、inline JS 語法(quickjs parse-only,Level 1)與 sandbox 執行 smoke(quickjs 真的
eval,Level 2——抓 Level 1 看不到的 runtime ReferenceError/TypeError,也抓被 chart 自己的
try/catch 吞掉的執行期錯誤,見 `_check_swallowed_chart_errors`)。錯誤訊息具體可操作,
設計成可直接餵回模型做下一輪修復。
"""

import contextlib
import json
import logging
import re
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
# 固定前綴(見 _resolve_html_error_line)。
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


def _check_size(html: str, errors: list[str]) -> None:
    byte_length = len(html.encode("utf-8"))
    if byte_length > HTML_MAX_BYTES:
        errors.append(
            f"dashboard.html is too large: {byte_length} bytes, exceeding the {HTML_MAX_BYTES} byte limit. "
            "Please trim the content (e.g. remove redundant comments, embedded data, or duplicate style definitions)."
        )


def _find_script_end(html: str, start_index: int) -> int:
    """回傳從 `start_index`(緊接在 `<script...>` 開始標籤之後)起算,真正 `</script`
    終止符的位置——不被字串字面值/註解裡的 `</script>` 誤判(例如 ECharts tooltip
    formatter 內含這段文字)。逐字 port 自 `JsSyntaxValidator.findScriptEnd`(Java)。

    找不到終止符時回傳 `len(html)`(視剩餘全部內容為 script 內文)。
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
    """quickjs 不可用(import 失敗)時記 warning、整條規則跳過——比照 backend
    `JsSyntaxValidator` 的哲學:驗證器本身掛掉不能擋 dashboard 送出。

    每段 script 內文包進 `(function(){ ... })` 再丟給 quickjs `eval`——這只是「定義」
    一個函式表達式,quickjs 仍會對函式本體做完整語法解析但不執行內容,等同 parse-only
    (不會有 `console.log(...)` 之類的副作用被真的跑出來)。這是 Level 1 檢查:只抓
    SyntaxError,抓不到(也不該抓)未宣告變數這類 runtime ReferenceError。
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
    """建構 sandbox 要灌入 `window.__ERD_RESULTS__` 的假資料 JSON literal。

    有提供真實 `results` 時,對每個 `available_query_id` 用真實 `columns` 與前
    `_SANDBOX_SEED_ROW_LIMIT` 列真實 `rows`——這樣「按真實欄名查找」的程式碼閘門才會
    真的打開，閘門後面的圖表初始化程式碼才會被執行到。某個 query_id 不在 `results` 裡
    或沒提供 `results` 時，該 query_id 退回泛用假資料。
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
    """建一個全新 quickjs Context,灌入 prelude、`__ERD_RESULTS__`(見
    `_results_literal_for_sandbox`)、`__erd_known_element_ids__`(見
    `_extract_known_element_ids`;預設空集合是刻意的 fail-toward-null 選擇),以及目前
    已收集到的 stub 變數(每個都指到一個 absorb-all proxy,不再對這些名稱拋 ReferenceError)。"""
    context = quickjs.Context()
    context.set_time_limit(_SANDBOX_TIME_LIMIT_SECONDS)
    context.eval(_SANDBOX_PRELUDE)
    context.eval(
        f"window.__ERD_RESULTS__ = {_results_literal_for_sandbox(available_query_ids, results)};"
    )
    context.eval(f"__erd_known_element_ids__ = new Set({json.dumps(sorted(known_element_ids))});")
    for variable_name in stub_variable_names:
        context.eval(f"globalThis.{variable_name} = __erdMakeAbsorb();")
    return context


def _resolve_html_error_line(message: str, html_start_line: int) -> int | None:
    """把 quickjs 執行期錯誤訊息裡的 script 內行號換算成 HTML 絕對行號——script 內容
    是直接 eval(不像語法檢查包了一層 `(function(){...})`),故行號無需再扣包裝行偏移。"""
    location_match = _QUICKJS_ERROR_LOCATION_PATTERN.search(message)
    if location_match is None:
        return None
    script_relative_line = int(location_match.group(1))
    return html_start_line + script_relative_line - 1


def _format_execution_error(html_line: int | None, script_index: int, first_line: str) -> str:
    """Error message fed straight into the repair prompt: when an HTML line number is
    available, ReferenceError uses `Line N: ReferenceError 'X' is not defined`, other
    exceptions use `Line N: <message truncated to 150 chars>`; when no line number is
    available (shouldn't happen in theory, defensive fallback) it falls back to the
    `script#N execution error: ...` format."""
    if html_line is None:
        truncated_message = first_line[:_SANDBOX_ERROR_MESSAGE_MAX_LENGTH]
        return f"script#{script_index} execution error: {truncated_message}"

    variable_match = _REFERENCE_ERROR_VAR_PATTERN.search(first_line)
    if variable_match:
        return f"Line {html_line}: ReferenceError '{variable_match.group(1)}' is not defined"

    truncated_message = first_line[:_SANDBOX_ERROR_MESSAGE_MAX_LENGTH]
    return f"Line {html_line}: {truncated_message}"


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


def _stack_frame_lines(stack_text: str, skip_function_names: frozenset[str]) -> list[int]:
    """回傳 stack 由深到淺、帶行號的 frame 行號列表,略過指定的函式名(sandbox 自己的
    stub frame)。quickjs 對部分 frame 不給行號,那些一律略過。"""
    frame_lines: list[int] = []
    for frame_match in _STACK_FRAME_PATTERN.finditer(stack_text):
        if frame_match.group(1) in skip_function_names:
            continue
        if frame_match.group(2) is None:
            continue
        frame_lines.append(int(frame_match.group(2)))
    return frame_lines


def _resolve_stack_call_site_line(stack_text: str, block_start_line: int) -> int | None:
    """把 `(new Error()).stack` 換算成呼叫點的 HTML 絕對行號。

    quickjs 的 stack 第一筆有行號的 frame 是 getCol 內 `console.warn` 那行,第二筆才是
    真正綁錯的呼叫點(見模組上方對 `_SANDBOX_PRELUDE` 的實測格式說明)。只有一筆帶行號的
    frame 時(呼叫點與 warn 同一行,理論上不會發生,防禦性 fallback)退回用那一筆。
    """
    frame_lines = _stack_frame_lines(stack_text, frozenset({"warn"}))
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
    """Level 2 檢查:在 quickjs sandbox 內真的執行(不只 parse)每段 inline script。quickjs
    不可用時比照 `_check_js_syntax` 的降級策略,記 warning、跳過。

    `results` 有提供時,sandbox 的 `__ERD_RESULTS__` 灌真實欄名/資料,讓按真實欄名查找的
    閘門真的打開;沒提供則退回泛用假資料。`known_element_ids` 灌進 sandbox 的
    `getElementById`/`querySelector('#id')`——引用不存在的 id 時回 `null`,對 `null` 取
    屬性會拋 TypeError,交給下面的未捕捉例外偵測處理。

    multi-mole 掃描(見上方模組註解):單一 block 內每個未宣告變數都個別記錄一條錯誤,
    記完就 stub 起來、重建全新 Context、把該 block 之前的所有 block 靜默重放、重跑這個
    block,直到不再拋新的 ReferenceError 或達 `_MAX_REFERENCE_ERROR_RETRIES_PER_BLOCK` 次。
    非 ReferenceError 的例外一樣記錄但不重試,沿用現有 context 繼續掃下一個 block。

    所有 block 跑完後,額外讀一次 sandbox 的 `console.error` 收集器,把被 chart 自己的
    try/catch 擋下的執行期錯誤也轉成 guard error(見 `_check_swallowed_chart_errors`);
    以及 `console.warn` 收集器,把 getCol 找不到欄位的訊號轉成 guard error(見
    `_check_column_not_found_warnings`)——只在整份 `results` 都是真實欄名時才做這件事,
    否則 sandbox 灌的泛用假欄名(`__c0`/`__c1`)會讓每個 getCol 呼叫都變成誤報。
    """
    if not _QUICKJS_AVAILABLE:
        logger.warning("html_guard: quickjs 未安裝，跳過 JS 執行檢查")
        return []

    errors: list[str] = []
    stub_variable_names: set[str] = set()

    try:
        context = _build_sandbox_context(
            available_query_ids, stub_variable_names, results, known_element_ids
        )
    except Exception as sandbox_init_error:  # noqa: BLE001 -- 驗證器掛掉不擋主流程
        logger.warning("html_guard: sandbox 初始化失敗，跳過 JS 執行檢查: %s", sandbox_init_error)
        return []

    for script_index, (script_content, html_start_line) in enumerate(script_blocks_with_lines):
        retry_count = 0
        while True:
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

                html_line = _resolve_html_error_line(message, html_start_line)
                errors.append(_format_execution_error(html_line, script_index, first_line))

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

                stub_variable_names.add(undeclared_variable)
                retry_count += 1
                try:
                    context = _build_sandbox_context(
                        available_query_ids, stub_variable_names, results, known_element_ids
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


_TAB_STRUCTURE_MARKERS: tuple[str, ...] = ("showTab(", 'id="panel-0"', 'role="tab"')
_RESIZE_DISPATCH_SNIPPET = "dispatchEvent(new Event('resize'))"
_TABLER_STYLE_MARKER = "border-b-2"


def _check_tab_conventions(html: str) -> list[str]:
    """HTML 含 tab 結構(`showTab(`／`id="panel-0"`／`role="tab"` 任一命中)時,強制兩條
    確定性規則:

    1. resize 防護:切 tab 沒有 dispatch resize event,hidden panel 裡的 ECharts 量不到
       容器尺寸,圖表會是空白。
    2. Tabler 底線式樣式標記:skill 規定的 tabs 範本一律用 `border-b-2` 做 active 底線;
       缺這個 class 代表偏離規範(藥丸/segmented 樣式)。

    無 tab 結構的一般 dashboard 零檢查、零誤報。
    """
    if not any(marker in html for marker in _TAB_STRUCTURE_MARKERS):
        return []

    errors: list[str] = []
    if _RESIZE_DISPATCH_SNIPPET not in html:
        errors.append(
            "Tab switching is missing window.dispatchEvent(new Event('resize')) -- ECharts "
            "in a hidden panel will render blank. Use the skill's showTab template verbatim."
        )
    if _TABLER_STYLE_MARKER not in html:
        errors.append(
            "Tab styling deviates from spec: MUST use the Tabler underline-style template "
            "(active state is a border-b-2 underline, not a pill/segmented style). Use the "
            "skill's tabs template verbatim."
        )
    return errors


def _is_allowed_script_src(src: str) -> bool:
    """Host 邊界比對,不是字串前綴比對:parse 出 scheme/host/path,要求 scheme 為
    `https` 且 host **精確等於**允許清單中的一個。`src.startswith(prefix)` 對原始字串
    比對會被 `https://cdn.tailwindcss.com.evil.example/x.js` 這類 lookalike host 繞過
    (它以合法前綴開頭,但那個 host 是攻擊者完全控制的網域);`urlsplit(...).hostname`
    只取真正的 host 部分,不受這招影響。jsdelivr 額外要求 path 落在 echarts npm package
    底下,不放行該網域下任意套件。"""
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
    """掃出**任何** `<script` 開始標籤(不管 src 有無引號、標籤名後接空白還是 `/`)帶
    src 屬性者,解析其 URL 做 host 白名單比對。沿用 `_SCRIPT_OPEN_TAG_PATTERN`(與
    `_extract_inline_scripts_with_lines` 共用同一顆 tokenizer 常數)——它天生就對
    `<script/src="...">` 有效,因為 `/src="..."` 整段落在 `[^>]*` 裡,不需要標籤名後
    有空白這個(錯誤的)假設。這個 guard 跑在生成期(模型剛寫完 HTML、serve 期的
    ArtifactCdnRewriter 還沒把 CDN URL 換成 /vendor/ 之前),角色是確保模型寫的 script
    src 是 rewriter 認得、能成功換寫的網址——render 正確性 + defense-in-depth,不是唯一
    安全邊界(真正邊界在 serve 層的 CSP),但仍必須用不可繞過的比對邏輯,不能靠字串
    startswith。"""
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
    """掃描每個 `echarts.init(...)` 呼叫：單參數改寫為帶 `'erd'` 主題；

    雙參數且第二參數非 `'erd'`/`"erd"` → 記錄 error、原樣保留（不改寫）。
    對括號做深度平衡掃描，能正確處理如 `document.getElementById("chart")` 這類
    引數本身含括號的呼叫（brief 給的單純字元類 regex 無法處理此情形）。
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
    """依序檢查 dashboard.html 的結構、體積、CDN 白名單、查詢結果引用、erd 主題、
    inline JS 語法(quickjs parse-only,Level 1)、sandbox 執行 smoke(quickjs 真的 eval,
    Level 2,只在 Level 1 乾淨時跑)、tooltip、tab 規範(僅在 HTML 含 tab 結構時觸發)
    (quickjs 不可用時兩層 JS 檢查都記 warning 跳過)。

    `results`(選填,形狀同 `load_all_results` 的回傳值)有提供時,Level 2 的 sandbox 會用
    真實欄名/資料灌 `window.__ERD_RESULTS__`,讓按真實欄名查找的閘門真的打開,閘門後面的
    圖表初始化程式碼才會被執行到;未提供時退回泛用假資料 fallback。

    Level 2 同時用 `_extract_known_element_ids(html)` 從整份 HTML 掃出所有實際存在的
    element id,灌進 `getElementById`/`querySelector('#id')` 做 id 擬真——引用不存在的
    id 會如實回傳 `null`,後續對 `null` 取屬性的 TypeError 會被執行期例外偵測抓到。這是
    全自動的,每次呼叫都從傳入的 `html` 自己算。

    規則之間互不 fail-fast，全部違規一次收集，供模型一輪修完。
    單參數 `echarts.init(X)` 會被確定性改寫為 `echarts.init(X, 'erd')`；
    已帶第二參數但非 'erd' 則報錯、不改寫。
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
    errors.extend(_check_tab_conventions(html))
    rewritten_html = _apply_erd_theme(html, errors)

    return GuardReport(ok=not errors, errors=errors, html=rewritten_html)
