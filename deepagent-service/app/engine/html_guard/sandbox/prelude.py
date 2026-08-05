"""quickjs sandbox 的 JS 前導原始碼——frozen,一字不改（改一個字元都可能改變 Level 2 偵測
到的錯誤）。`_build_sandbox_context`（見 `context.py`）在每個新 Context 裡 eval 這段字串。
"""

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

// init 對 null/undefined 容器擲錯——真 ECharts 會對 null 容器取 getAttribute 而 TypeError
// (「HTML 忘了放圖表容器 div 但 JS 照樣 init」的真實慘案),absorb stub 若吞掉 null 就是
// 假陰性,id 擬真等於白做。訊息比瀏覽器原生的更可操作,直接指向缺容器的修法。
var echarts = __erdMakeDomLike({
  init: function (containerElement) {
    if (containerElement === null || containerElement === undefined) {
      throw new TypeError(
        "echarts.init: container element is null -- the id passed to " +
        "document.getElementById does not exist in the HTML. Add the missing " +
        "container <div id=\"...\"></div> (or fix the id) before initializing this chart."
      );
    }
    return __erdMakeAbsorb();
  },
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
