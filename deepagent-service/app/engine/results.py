"""這個模組負責把查詢結果落檔, 並注入到 window.__ERD_RESULTS__ 裡. Dashboard HTML 本身不
內嵌資料, 只會讀 window.__ERD_RESULTS__["qN"], 查詢結果由這個模組落檔(queries/{id}.sql 加
results/{id}.json), 送出前才注入進 HTML.

這是 engine 層, 只能用 stdlib, 不能 import 任何 LLM 框架(ruff 的 TID251 規則會擋下來).
"""

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

# 這裡列的是 build_results_script 注入的 <script id="erd-results-data"> 區塊, 要在重新
# 注入前剝掉. 主題現在不在 Python 端注入了, 改成由 Java 端的 ArtifactAssembler 統一注入,
# 所以不再需要剝 erd-theme 這個區塊.
_INJECTED_SCRIPT_IDS = ("erd-results-data",)
_INJECTED_BLOCK_PATTERN = re.compile(
    r"<script\s+id=\"(?:" + "|".join(_INJECTED_SCRIPT_IDS) + r")\"[^>]*>.*?</script>",
    re.DOTALL,
)

# 這些是 json.dumps 原生支援的 cell 型別, 其餘型別一律要經過 jsonable_cell 轉換, 細節看
# 那個函式的說明.
_JSON_NATIVE_CELL_TYPES = (str, int, float, bool, type(None))


def next_query_id(workspace: SessionWorkspace) -> str:
    """回傳格式是 q{N}, N 是現有 queries/*.sql 檔案數加一. 這個編號跨輪遞增, 同一個 session
    多輪迭代下來不會重複用到同一個編號."""
    existing_count = len(list(workspace.queries_dir.glob("*.sql")))
    return f"q{existing_count + 1}"


def jsonable_cell(value: object) -> object:
    """把 DuckDB 回傳但 json.dumps 不支援的 cell 型別轉成 JSON 安全的值: Decimal 轉成
    float, date 或 datetime 轉成 ISO-8601 字串, bytes 和其他不認識的型別一律用 str() 兜底,
    絕不拋出例外, 因為落檔不該因為欄位型別冷門就讓整條查詢報 SQL_ERROR. str, int, float,
    bool, None 這幾種已經是 JSON 安全的值會原樣通過, 對它們再套用一次結果還是自己, 也就是
    說這個函式是冪等的, 所以 record_query 跟呼叫端各自正規化一次不會互相干擾, 細節看
    normalize_rows 和 record_query 的說明."""
    if isinstance(value, _JSON_NATIVE_CELL_TYPES):
        return value
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    return str(value)


def normalize_rows(rows: list[list]) -> list[list]:
    """這是對外公開的批次版 jsonable_cell, 逐列逐個 cell 正規化, 確保落檔前的 rows 都是
    JSON 安全的值, 因為 DuckDB 原生的 Decimal, date, datetime 型別不先正規化就沒辦法被
    json.dumps 序列化. 呼叫端應該在拿到 DuckDB 原始 rows 後立刻呼叫一次, 再把結果交給
    record_query."""
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
    """寫入 queries/{query_id}.sql 和 results/{query_id}.json. 超過 STORE_MAX_ROWS 時
    truncated 會被強制設成 True, rows 一律會經過 normalize_rows 正規化. 這是對外公開的
    API, 不能假設呼叫端已經先正規化過, 所以內部再做一次, jsonable_cell 對已經正規化過的
    值是恆等函式, 重複呼叫不會有副作用. 落檔的 rows 是以欄名為 key 的物件列
    (dict(zip(columns, row))), 不是陣列列; 呼叫端(data.py)的 markdown 預覽用的仍然是
    陣列列, 兩邊的容器形狀不一樣. columns 仍然保留在 payload 裡, 因為 dashboard 的明細表
    需要欄位順序.
    """
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
    """產生 <script id="erd-results-data">...</script> 這個區塊, id 標記是給
    strip_injected_blocks 用來剝除的. 每個 < 字元都會逃脫成 \\u003c, 不只是逃脫 </: 因為
    一個 cell 值裡如果有 <!--, 會讓 HTML5 的 tokenizer 進入 escaped state, 之後沒有斜線的
    <script 也能存活進 double-escaped state, 讓後面真正的 </script> 沒辦法終止標籤; 逃脫
    每一個 < 才能堵住這條路. JSON 賦值後面接著 rows 的 Proxy 包裝程式碼(見
    _ROWS_PROXY_SCRIPT), 都在同一個 script 標籤裡, 剝除的契約不變."""
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


# 這是綁定 manifest 的標題, 也是模型看到的第一行, 明講不要憑記憶去猜編號.
_WIRING_MANIFEST_HEADER = (
    "Query results currently available in window.__ERD_RESULTS__ "
    "(bind dashboard blocks by these ids and columns -- NEVER guess a q-number from memory):"
)


def format_wiring_manifest(results: dict[str, dict]) -> str:
    """把 load_all_results 的結果攤平成 qid -- intent -- columns 這種逐行清單, 結果是空的
    就回傳空字串.

    排序用 qid 而不是 dict 原本的順序, 讓同一輪內重複呼叫時字串內容保持一致, 避免 prompt
    前綴因為 filesystem glob 的順序抖動而每次都不一樣.
    """
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
    """剝除 build_results_script 或 theme.ERD_THEME_SCRIPT 注入的 <script id="erd-...">
    區塊, 拿回還沒注入過的乾淨基底; continue-edit 在重新注入前一定要先剝, 不然會疊出兩份.
    只認得帶 id 的區塊, 沒有匹配到時原樣回傳; 這個函式是冪等的, 對已經剝過的 HTML 再呼叫
    一次結果不變.
    """
    return _INJECTED_BLOCK_PATTERN.sub("", html)
