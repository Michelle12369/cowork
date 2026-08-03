"""單一規則檢查:資料綁定、CDN script src 白名單、registerTheme 禁令、
查詢結果 id 引用完整性。每條規則只認 errors list,不 fail-fast。
"""

import re
from urllib.parse import urlsplit

from app.engine.results import referenced_query_ids

from . import js_lexer
from .error_cap import cap_reported_messages

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

# `=` 後面緊跟另一個 `=` 一律不算(排除 `===`/`==`)。`!=`/`!==`/`>=`/`<=` 不需要額外處理:
# 這些運算子在 `__ERD_RESULTS__` 與 `=` 之間都夾了 `!`/`>`/`<` 其中一個字元,pattern 要求
# `=` 緊接在 `\s*` 後面,原文一比對就已經不匹配,不會走到 lookahead 那一步。
_ERD_RESULTS_ASSIGNMENT_PATTERN = re.compile(r"__ERD_RESULTS__\s*=(?!=)")

# 對照 `_SRC_ATTR_VALUE_PATTERN`(同檔 js_lexer.py)的寫法,泛化成任意屬性名——這裡只用來抓
# `<script>` 開始標籤的 `id` 屬性值。
_ID_ATTR_VALUE_PATTERN = re.compile(
    r"""(?<![\w-])id\s*=\s*(?:"([^"]*)"|'([^']*)'|(\S+))""", re.IGNORECASE
)
_INJECTED_RESULTS_SCRIPT_ID = "erd-results-data"

# M3: 一次退貨最多列幾條白名單違規——修復 prompt 不能無限長(實測 40 個壞 tag = 6670 字元)。
# 跟 `console.py` 的 `_MAX_REPORTED_COLUMN_MISSES` 用同一個數量級,讓每條被截斷的規則對模型
# 呈現一致的形狀。
_MAX_REPORTED_SCRIPT_SRC_VIOLATIONS = 8


def _check_data_binding(html: str, errors: list[str]) -> None:
    """有圖表就一定要從 `window.__ERD_RESULTS__` 取資料。全檔零次引用代表數字被硬編進
    HTML——不會拋例外、順利過其他檢查,但交付的每個數字都可能是過期的。"""
    if _ECHARTS_INIT_CALL_PREFIX in html and "__ERD_RESULTS__" not in html:
        errors.append(
            "The dashboard initializes ECharts but never reads window.__ERD_RESULTS__ -- the "
            "numbers are hard-coded. Every chart, KPI and table MUST read its data from "
            "window.__ERD_RESULTS__['<query id>'] (see the dashboard skill)."
        )


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


def _check_script_src_whitelist(
    html: str, errors: list[str], unconditional_errors: list[str]
) -> None:
    """掃出所有帶 src 的 `<script` 標籤,對 URL 做 host 白名單比對。跑在生成期(serve 期的
    ArtifactCdnRewriter 尚未把 CDN URL 換成 /vendor/ 之前),確保模型寫的 src 是 rewriter
    認得的網址。這其實是**唯一**的遠端腳本邊界——repo 內沒有任何 CSP,artifact iframe 只有
    `sandbox="allow-scripts"`(無 `allow-same-origin`,但網路出口不受限)——所以違規 MUST 進
    `unconditional_errors`,不受 `ERD_GUARD_BLOCKING=false` 影響。
    沿用 `js_lexer._SCRIPT_OPEN_TAG_PATTERN` 而非自訂 `<script\\s` regex,因為它對
    `<script/src="...">` 這種邊界寫法仍有效(`/src=` 落在該 pattern 的 `[^>]*` 裡);換成
    土砲 regex 會重新打開白名單繞過的破口。

    M3:`errors`(餵給修復 prompt 的那份)每個壞 tag 各出一條會無限累加,`unconditional_errors`
    (只用來判斷「要不要無條件擋下」的旗標,見 `ChatTurn.finalize()`)則保留每一筆——截斷
    `errors` 的呈現方式,不能連帶弄丟「這裡有違規」這個訊號本身。
    """
    violations: list[str] = []
    for tag_match in js_lexer._SCRIPT_OPEN_TAG_PATTERN.finditer(html):
        attrs = tag_match.group(1) or ""
        src_match = js_lexer._SRC_ATTR_VALUE_PATTERN.search(attrs)
        if src_match is None:
            continue
        src = next(group for group in src_match.groups() if group is not None)
        if not _is_allowed_script_src(src):
            violations.append(
                f'<script src="{src}"> is not on the whitelist. Only these prefixes are allowed: '
                f"{', '.join(ALLOWED_SCRIPT_SRC_PREFIXES)}"
            )
    if not violations:
        return
    unconditional_errors.extend(violations)
    errors.extend(
        cap_reported_messages(
            violations,
            _MAX_REPORTED_SCRIPT_SRC_VIOLATIONS,
            "forbidden <script src> tags with the same fix (remove or replace them)",
        )
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


def _check_no_erd_results_overwrite(
    html: str, errors: list[str], unconditional_errors: list[str]
) -> None:
    r"""H1: `inject_results`(`app/engine/results.py`)在 `check_dashboard_html` 核准 HTML **之
    後**,才把真實查詢結果的 `<script id="erd-results-data">window.__ERD_RESULTS__ = {...}
    </script>` 插進 `</head>` 前。若模型自己的 `<body>` script 也對 `window.__ERD_RESULTS__`
    賦值,或直接冒用 `id="erd-results-data"` 這個 injector 專用 id,DOM 順序讓模型那份晚出現、
    蓋掉真實資料——使用者看到的就是編出來的數字。`_check_data_binding` 只檢查
    `__ERD_RESULTS__` 有沒有被「引用」,完全不會發現這種覆寫,繞過了那條規則想擋的「數字硬編」
    問題。

    賦值那半只在內嵌 `<script>` 區塊、對遮罩過的內容找(`js_lexer.extract_inline_script_spans`
    + `mask_strings_and_comments`,同 `theme_rewrite._apply_erd_theme` 的作法,見 f469c16)——
    不然「不要自己寫 __ERD_RESULTS__ = ...」這種說明文案、或 JS 註解/字串字面值裡剛好含這段
    文字,會被誤判成違規。`id="erd-results-data"` 那半屬於 `<script>` 開始標籤的 HTML 屬性,
    不是 JS 語法,遮罩對它沒意義——改用 `js_lexer._SCRIPT_OPEN_TAG_PATTERN` 只抓真正
    `<script ...>` 開始標籤的屬性字串比對,一樣不會被 body 裡提到這個 id 字串的說明文案誤觸,
    因為那不會落在一個 `<script>` 開始標籤裡。

    這是 unconditional violation(進 `unconditional_errors`,不受 `ERD_GUARD_BLOCKING=false`
    影響)——`ERD_GUARD_BLOCKING=false` 存在是為了讓「美觀但不完美」的 dashboard 照樣出貨,
    出貨「編造的數字」不屬於這個容忍範圍,性質跟截斷偵測(見 `report.py` 的 `check_structure`)
    同一類:資料正確性的最後防線,不是風格建議。假陽性的代價因此偏高,這正是上面兩個掃描範圍
    限制存在的原因。

    已知抓不到的繞過寫法(residual):`Object.assign(window.__ERD_RESULTS__, {...})`(是呼叫,
    不是賦值,`__ERD_RESULTS__` 後面接的是 `,` 不是 `=`)、`window.__ERD_RESULTS__ ??= {...}`
    (`?` 卡在 `__ERD_RESULTS__` 與 `=` 之間,pattern 要求 `=` 緊接在 `\s*` 後面因此不匹配)、
    `window['__ERD_RESULTS__'] = {...}`(這個變體的 `__ERD_RESULTS__` 落在字串字面值裡,遮罩後
    已變成空白,regex 根本找不到這段文字)。
    """
    violation_found = False
    for content_start, content_end in js_lexer.extract_inline_script_spans(html):
        masked_segment = js_lexer.mask_strings_and_comments(html[content_start:content_end])
        if _ERD_RESULTS_ASSIGNMENT_PATTERN.search(masked_segment):
            violation_found = True
            break

    if not violation_found:
        for tag_match in js_lexer._SCRIPT_OPEN_TAG_PATTERN.finditer(html):
            attrs = tag_match.group(1) or ""
            id_match = _ID_ATTR_VALUE_PATTERN.search(attrs)
            if id_match is None:
                continue
            id_value = next(group for group in id_match.groups() if group is not None)
            if id_value == _INJECTED_RESULTS_SCRIPT_ID:
                violation_found = True
                break

    if violation_found:
        error = (
            "The dashboard HTML must NEVER assign to window.__ERD_RESULTS__ or declare a "
            '<script id="erd-results-data"> tag -- both are written exclusively by the '
            "system's injector right before this HTML ships. If the model also writes them, "
            "DOM order lets the model's version win and silently replaces the real query "
            "results with fabricated numbers. Remove the assignment/tag; only READ from "
            "window.__ERD_RESULTS__['<query id>']."
        )
        errors.append(error)
        unconditional_errors.append(error)


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
