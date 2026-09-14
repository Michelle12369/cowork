"""把查詢結果落檔, 並注入到 window.__ERD_RESULTS__. dashboard HTML 本身不內嵌資料.
engine 層只用 stdlib, 不 import LLM 框架."""

import datetime
import decimal
import json
import logging
import re

from app.engine.workspace import SessionWorkspace

logger = logging.getLogger(__name__)

STORE_MAX_ROWS = 5000

_REFERENCED_QUERY_ID_PATTERN = re.compile(r"""__ERD_RESULTS__\s*\[\s*["'](\w+)["']\s*\]""")
_HEAD_CLOSE_PATTERN = re.compile(r"</head>", re.IGNORECASE)
_BODY_OPEN_PATTERN = re.compile(r"<body\b[^>]*>", re.IGNORECASE)

MCP_RUNTIME_SCRIPT_ID = "erd-mcp-runtime"
MCP_RUNTIME_VERSION = "1"

# 這裡列的是注入過的 <script id="..."> 區塊, 要在重新注入前剝掉.
# 主題改由 Java 端的 ArtifactAssembler 統一注入, 這裡不再需要剝 erd-theme 區塊.
_INJECTED_SCRIPT_IDS = ("erd-results-data", MCP_RUNTIME_SCRIPT_ID)
_INJECTED_BLOCK_PATTERN = re.compile(
    r"<script\s+id=\"(?:" + "|".join(_INJECTED_SCRIPT_IDS) + r")\"[^>]*>.*?</script>",
    re.DOTALL,
)
_HEAD_OPEN_PATTERN = re.compile(r"<head(?=[\s>/])[^>]*>", re.IGNORECASE)

# 這些是 json.dumps 原生支援的 cell 型別, 其餘型別一律要經過 jsonable_cell 轉換, 細節看
# 那個函式的說明.
_JSON_NATIVE_CELL_TYPES = (str, int, float, bool, type(None))


def next_query_id(workspace: SessionWorkspace) -> str:
    """回傳格式是 q{N}, N 是現有 queries/*.sql 檔案數加一. 這個編號跨輪遞增, 同一個 session
    多輪迭代下來不會重複用到同一個編號."""
    existing_count = len(list(workspace.queries_dir.glob("*.sql")))
    return f"q{existing_count + 1}"


def jsonable_cell(value: object) -> object:
    """把 DuckDB 回傳但 json.dumps 不支援的 cell 型別轉成 JSON 安全的值.
    Decimal 轉 float, date/datetime 轉 ISO-8601 字串, 其他不認識的型別用 str() 兜底, 絕不拋出例外.
    這個函式是冪等的, 已經是 JSON 安全的值會原樣通過."""
    if isinstance(value, _JSON_NATIVE_CELL_TYPES):
        return value
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    return str(value)


def normalize_rows(rows: list[list]) -> list[list]:
    """批次版 jsonable_cell, 逐列逐個 cell 正規化, 確保 rows 落檔前都是 JSON 安全的值.
    呼叫端應該在拿到 DuckDB 原始 rows 後立刻呼叫, 再把結果交給 record_query."""
    return [[jsonable_cell(cell) for cell in row] for row in rows]


def _dedupe_columns(columns: list[str]) -> list[str]:
    """遇到重複欄名時, 第一次出現的保留原名, 之後依序加上 _2, _3 這樣的後綴; 後綴會避開
    所有原始欄名, 不會搶走本來就存在的 a_2 這種名字, 一路遞增到不重複為止."""
    original_names = set(columns)
    used: set[str] = set()
    unique_columns: list[str] = []
    for name in columns:
        candidate = name
        suffix_counter = 2
        while candidate in used or (candidate != name and candidate in original_names):
            candidate = f"{name}_{suffix_counter}"
            suffix_counter += 1
        used.add(candidate)
        unique_columns.append(candidate)
    return unique_columns


def record_query(
    workspace: SessionWorkspace,
    query_id: str,
    sql: str,
    intent: str,
    columns: list[str],
    rows: list[list],
    truncated: bool,
) -> None:
    """寫入 queries/{query_id}.sql 和 results/{query_id}.json.
    超過 STORE_MAX_ROWS 時 truncated 會被強制設成 True, rows 一律會經過 normalize_rows 正規化.
    落檔的 rows 是以欄名為 key 的物件列, 不是陣列列, columns 仍保留在 payload 裡供欄位排序."""
    (workspace.queries_dir / f"{query_id}.sql").write_text(sql, encoding="utf-8")

    stored_rows = rows[:STORE_MAX_ROWS]
    is_truncated = truncated or len(rows) > STORE_MAX_ROWS
    # 重複欄名(例如 SELECT * join 出現同名欄)會讓 dict(zip) 靜默丟欄, Proxy 也攔不到這種
    # 情況, 所以要去重加後綴, payload 裡的 columns 也要一起改寫, 讓欄名跟物件 key 保持一致.
    unique_columns = _dedupe_columns(columns)
    object_rows = [
        dict(zip(unique_columns, row, strict=False)) for row in normalize_rows(stored_rows)
    ]
    payload = {
        "intent": intent,
        "columns": unique_columns,
        "rows": object_rows,
        "truncated": is_truncated,
    }
    (workspace.results_dir / f"{query_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def load_all_results(workspace: SessionWorkspace) -> dict[str, dict]:
    """讀取所有 results/*.json 檔案, key 是 query_id. 如果單一檔案損毀, 例如併發寫入或
    process 被砍到一半, 就只跳過那一筆並記一筆警告, 不讓一份壞檔卡住整個 session.
    """
    results: dict[str, dict] = {}
    for result_path in workspace.results_dir.glob("*.json"):
        query_id = result_path.stem
        try:
            results[query_id] = json.loads(result_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as load_error:
            logger.warning("skipping unreadable result file %s: %s", result_path, load_error)
    return results


def referenced_query_ids(html: str) -> set[str]:
    """找出 HTML 裡所有 __ERD_RESULTS__["qN"] 或 __ERD_RESULTS__['qN'] 引用到的 query_id."""
    return set(_REFERENCED_QUERY_ID_PATTERN.findall(html))


# 每一列都包一層 Proxy: 讀到錯的欄名(包含用 index 存取)就直接丟例外, 把原本安靜的 NaN
# 變成修復鏈路接得到的錯誤; symbol, 原型屬性, toJSON, then 這些探測性存取放行, 資料形狀不變.
_ROWS_PROXY_SCRIPT = """
(function(){
  var PROBE_PASS = {toJSON:1, then:1};
  Object.keys(window.__ERD_RESULTS__).forEach(function(queryId){
    var result = window.__ERD_RESULTS__[queryId];
    var columns = result.columns || [];
    result.rows = (result.rows || []).map(function(row){
      return new Proxy(row, {
        get: function(target, prop, receiver){
          if (typeof prop === 'symbol' || prop in target || PROBE_PASS[prop]) {
            return Reflect.get(target, prop, receiver);
          }
          throw new Error('[ERD] ' + queryId + ' row has no column "' + String(prop) +
            '"; available columns: ' + columns.join(', '));
        }
      });
    });
  });
})();"""


def build_results_script(results: dict[str, dict]) -> str:
    """產生 <script id="erd-results-data"> 區塊, id 標記給 strip_injected_blocks 用來剝除.
    每個 < 字元都逃脫成 \\u003c, 不只逃脫 </, 避免 cell 值裡的 <!-- 讓 </script> 提早結束標籤."""
    serialized = json.dumps(results, ensure_ascii=False).replace("<", "\\u003c")
    return (
        f'<script id="erd-results-data">window.__ERD_RESULTS__ = {serialized};'
        f"{_ROWS_PROXY_SCRIPT}</script>"
    )


def inject_results(html: str, results: dict[str, dict]) -> str:
    """插入點的優先序是: </head> 之前, 其次是 <body...> 之後, 都找不到就放在最前面."""
    script = build_results_script(results)

    head_close_match = _HEAD_CLOSE_PATTERN.search(html)
    if head_close_match:
        insert_index = head_close_match.start()
        return html[:insert_index] + script + html[insert_index:]

    body_open_match = _BODY_OPEN_PATTERN.search(html)
    if body_open_match:
        insert_index = body_open_match.end()
        return html[:insert_index] + script + html[insert_index:]

    return script + html


# window.mcp() 的 host 端協定; 頁面呼叫後透過 postMessage 交給 host bridge 現抓資料,
# 結果經 erd-mcp-result 訊息送回, handler 只叫一次且一律非同步. 沒有自己的 try/catch,
# handler 拋錯會流到 window.onerror(Java 端的 relay), 也沒有自己的 error 事件監聽.
_MCP_RUNTIME_SCRIPT_BODY = """
(function () {
  var nextCallId = 1;
  var pendingHandlersById = {};

  window.mcp = function (connectorName, toolName, toolArgs, handler) {
    var callId = String(nextCallId++);
    pendingHandlersById[callId] = handler;
    var args = JSON.parse(JSON.stringify(toolArgs === undefined ? {} : toolArgs));
    parent.postMessage({ type: 'erd-mcp-call', id: callId, connector: connectorName, tool: toolName, args: args }, '*');
  };

  window.addEventListener('message', function (messageEvent) {
    if (messageEvent.source !== parent) return; // 只有 host 頁面本身能回答, 不接其他來源.
    var message = messageEvent.data;
    if (!message || message.type !== 'erd-mcp-result') return;
    var handler = pendingHandlersById[message.id];
    if (!handler) return;
    delete pendingHandlersById[message.id];
    var result = message.result;
    if (result && result.error && (result.error.code === 'TOOL_ERROR' || result.error.code === 'INVALID_CALL')) {
      parent.postMessage({ type: 'erd-artifact-error', errors: [{ message: 'mcp ' + result.error.code + ': ' + String(result.error.message).slice(0, 500), line: 0, col: 0 }] }, '*');
    }
    handler(result);
  });
})();"""


def build_mcp_runtime_script() -> str:
    """產生 <script id="erd-mcp-runtime"> 區塊, 定義 window.mcp -- host 端的橋接邏輯在前端.
    本體是審過的常數(不含使用者資料), 不逃脫 < 字元, 只斷言常數本身不含 </."""
    if "</" in _MCP_RUNTIME_SCRIPT_BODY:
        raise ValueError(
            "_MCP_RUNTIME_SCRIPT_BODY must not contain '</' -- it would end the tag early"
        )
    return (
        f'<script id="{MCP_RUNTIME_SCRIPT_ID}" data-erd-runtime="{MCP_RUNTIME_VERSION}">'
        f"{_MCP_RUNTIME_SCRIPT_BODY}</script>"
    )


def inject_mcp_runtime(html: str) -> str:
    """插入點是開頭 <head ...> 標籤之後, 在任何頁面自身的 script 之前; 找不到 <head> 就
    整份 prepend(跟 inject_results 的 </head> 優先序是獨立的, 順序互不影響)."""
    script = build_mcp_runtime_script()
    head_open_match = _HEAD_OPEN_PATTERN.search(html)
    if head_open_match:
        insert_index = head_open_match.end()
        return html[:insert_index] + script + html[insert_index:]
    return script + html


def has_mcp_runtime(html: str) -> bool:
    """是否已經帶有 erd-mcp-runtime 區塊, /repair 用來決定要不要重新注入."""
    return f'id="{MCP_RUNTIME_SCRIPT_ID}"' in html


# 這是綁定 manifest 的標題, 也是模型看到的第一行, 明講不要憑記憶去猜編號.
_WIRING_MANIFEST_HEADER = (
    "Query results currently available in window.__ERD_RESULTS__ "
    "(bind dashboard blocks by these ids and columns -- NEVER guess a q-number from memory):"
)


def format_wiring_manifest(results: dict[str, dict]) -> str:
    """把結果攤平成 qid -- intent -- columns 的逐行清單, 空結果回傳空字串.
    排序用 qid, 讓同一輪內重複呼叫時字串內容保持一致."""
    if not results:
        return ""
    manifest_lines = [_WIRING_MANIFEST_HEADER]
    for query_id in sorted(results):
        result = results[query_id]
        column_names = ", ".join(result.get("columns") or [])
        manifest_lines.append(
            f"- {query_id} -- intent: {result.get('intent', '')} -- columns: {column_names}"
        )
    return "\n".join(manifest_lines)


def strip_injected_blocks(html: str) -> str:
    """剝除注入過的 <script id="erd-..."> 區塊, 拿回乾淨基底, 重新注入前一定要先剝.
    沒有匹配到時原樣回傳, 這個函式是冪等的."""
    return _INJECTED_BLOCK_PATTERN.sub("", html)
