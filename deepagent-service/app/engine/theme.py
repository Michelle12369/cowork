"""erd ECharts 主題注入。engine 層 stdlib only(禁止 import LLM 框架,ruff TID251 會擋)。
8 色盤與 textStyle/tooltip/categoryAxis/valueAxis 皆逐字複製自 backend head-inject.vm,
與該檔 MUST-sync,槽位順序 NEVER 重排。
"""

import re

ERD_THEME_SCRIPT = (
    '<script id="erd-theme">(function(){function registerErdTheme(){if(!window.echarts)return;'
    "echarts.registerTheme('erd',{color:['#2a78d6','#eb6834','#1baf7a','#eda100',"
    "'#e87ba4','#008300','#4a3aa7','#e34948'],textStyle:{fontFamily:\"Inter,-apple-system,"
    "'PingFang TC',sans-serif\"},tooltip:{backgroundColor:'#1E293B',borderWidth:0,"
    "textStyle:{color:'#F1F5F9',fontSize:12},extraCssText:'border-radius:8px;"
    "box-shadow:0 4px 12px rgba(0,0,0,.15);padding:8px 12px;'},"
    "categoryAxis:{axisLine:{lineStyle:{color:'#CBD5E1'}},axisLabel:{color:'#64748B'}},"
    "valueAxis:{axisLine:{show:false},splitLine:{lineStyle:{color:'#F1F5F9'}},"
    "axisLabel:{color:'#64748B'}}});}if(window.echarts){registerErdTheme();}else{"
    "document.addEventListener('DOMContentLoaded',registerErdTheme);}})();</script>"
)

_HEAD_CLOSE_PATTERN = re.compile(r"</head>", re.IGNORECASE)
_BODY_OPEN_PATTERN = re.compile(r"<body\b[^>]*>", re.IGNORECASE)


def inject_theme(html: str) -> str:
    """插入點優先序 `</head>` → `<body...>` 之後 → 前置;冪等。"""
    if "registerTheme('erd'" in html:
        return html

    head_close_match = _HEAD_CLOSE_PATTERN.search(html)
    if head_close_match:
        insert_index = head_close_match.start()
        return html[:insert_index] + ERD_THEME_SCRIPT + html[insert_index:]

    body_open_match = _BODY_OPEN_PATTERN.search(html)
    if body_open_match:
        insert_index = body_open_match.end()
        return html[:insert_index] + ERD_THEME_SCRIPT + html[insert_index:]

    return ERD_THEME_SCRIPT + html
