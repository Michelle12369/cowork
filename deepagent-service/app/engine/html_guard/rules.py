"""單一規則檢查:資料綁定、CDN script src 白名單、registerTheme 禁令、
查詢結果 id 引用完整性。每條規則只認 errors list,不 fail-fast。
"""

from urllib.parse import urlsplit

from app.engine.results import referenced_query_ids

from . import js_lexer

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


def _check_script_src_whitelist(html: str, errors: list[str]) -> None:
    """掃出所有帶 src 的 `<script` 標籤,對 URL 做 host 白名單比對。跑在生成期(serve 期的
    ArtifactCdnRewriter 尚未把 CDN URL 換成 /vendor/ 之前),確保模型寫的 src 是 rewriter
    認得的網址;不是唯一安全邊界(真正邊界在 serve 層 CSP),但比對邏輯仍不可靠字串 startswith。
    沿用 `js_lexer._SCRIPT_OPEN_TAG_PATTERN` 而非自訂 `<script\\s` regex,因為它對
    `<script/src="...">` 這種邊界寫法仍有效(`/src=` 落在該 pattern 的 `[^>]*` 裡);換成
    土砲 regex 會重新打開白名單繞過的破口。
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
