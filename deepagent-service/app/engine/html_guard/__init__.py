"""dashboard.html 確定性檢查——DASHBOARD_HTML 發送前的最後一道關卡。

engine 層——stdlib only,禁止 import 任何 LLM 框架(ruff TID251 會擋)。
"""

from app.engine.html_guard.checker import check_dashboard_html
from app.engine.html_guard.report import HTML_MAX_BYTES, GuardReport
from app.engine.html_guard.rules import ALLOWED_SCRIPT_SRC_PREFIXES

__all__ = [
    "ALLOWED_SCRIPT_SRC_PREFIXES",
    "HTML_MAX_BYTES",
    "GuardReport",
    "check_dashboard_html",
]
