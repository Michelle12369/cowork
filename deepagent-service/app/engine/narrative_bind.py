"""敘事綁定 resolver 的確定性注入——填 `[data-bind]` 元素的文字內容,值來自
`__ERD_RESULTS__`,路徑無效顯示「—」。與 `inject_results` 同層後處理:engine 層
stdlib only(禁止 import LLM 框架,ruff TID251 會擋)。resolver 掛 `id="erd-bind-resolver"`,
併入 `app/engine/results.py` 的 `_INJECTED_SCRIPT_IDS` 白名單,共用同一套
`strip_injected_blocks` 剝除機制——新增 script id 時兩處常數需同步更新。
"""

# 與 app/engine/results.py 的 _INJECTED_SCRIPT_IDS 白名單同步;改這個值時那邊也要改。
RESOLVER_SCRIPT_ID = "erd-bind-resolver"

# __ERD_RESULTS__[qid].rows 是「以欄名為 key 的物件列」(見 results.record_query),不是
# 陣列列,故直接用欄名索引,不走 columnIndex。data-bind-row="qN:k" 可選,指定非第 0 列;
# rowIndex 越界時 query.rows[rowIndex] 是 undefined,直接落到下面的 !row 分支回 null 顯示
# 「—」——絕不 fallback 回第 0 列(那會安靜顯示錯的那一列,比顯示「—」更危險)。
_RESOLVER_SCRIPT = f"""\
<script id="{RESOLVER_SCRIPT_ID}">
(function () {{
  function resolveBind(path, rowIndex) {{
    var parts = path.split(".");
    var query = (window.__ERD_RESULTS__ || {{}})[parts[0]];
    if (!query || !query.rows || !query.rows.length) return null;
    var row = query.rows[rowIndex];
    if (!row || !(parts[1] in row)) return null;
    return row[parts[1]];
  }}
  document.querySelectorAll("[data-bind]").forEach(function (element) {{
    var rowSpec = element.getAttribute("data-bind-row");
    var rowIndex = 0;
    if (rowSpec) {{
      var parsedIndex = parseInt(rowSpec.split(":")[1], 10);
      if (!isNaN(parsedIndex)) rowIndex = parsedIndex;
    }}
    var value = resolveBind(element.getAttribute("data-bind"), rowIndex);
    element.textContent = value === null || value === undefined ? "—" : String(value);
  }});
}})();
</script>"""

_RESOLVER_ID_MARKER = f'id="{RESOLVER_SCRIPT_ID}"'


def inject_bind_resolver(html: str) -> str:
    """resolver script 插在 `</body>` 之前——`[data-bind]` 元素必須已在 DOM 中,resolver
    才抓得到,不能像 `inject_results` 那樣插在文件前段。冪等:已注入時原樣返回;連續呼叫
    不重複疊加。不夾帶多餘字元(無插入的換行)使 strip_injected_blocks 的往返是逐字還原。"""
    if _RESOLVER_ID_MARKER in html:
        return html
    if "</body>" in html:
        return html.replace("</body>", f"{_RESOLVER_SCRIPT}</body>", 1)
    return html + _RESOLVER_SCRIPT
