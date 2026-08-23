"""查詢結果落檔與 `__ERD_RESULTS__` 注入。engine 層 stdlib only(禁止 import LLM 框架,
ruff TID251 會擋)。Dashboard HTML 不內嵌資料、只讀 `window.__ERD_RESULTS__["qN"]`;
查詢結果由本模組落檔(`queries/{id}.sql` + `results/{id}.json`),送出前注入 HTML。
"""

import datetime
import decimal
import json
import logging
import re

from app.engine.narrative_bind import RESOLVER_SCRIPT_ID
from app.engine.workspace import SessionWorkspace

logger = logging.getLogger(__name__)

STORE_MAX_ROWS = 5000

_REFERENCED_QUERY_ID_PATTERN = re.compile(r"""__ERD_RESULTS__\s*\[\s*["'](\w+)["']\s*\]""")
_HEAD_CLOSE_PATTERN = re.compile(r"</head>", re.IGNORECASE)
_BODY_OPEN_PATTERN = re.compile(r"<body\b[^>]*>", re.IGNORECASE)

# 剝除本模組 build_results_script 注入的 <script id="erd-results-data"> 區塊,以及
# narrative_bind.inject_bind_resolver 注入的 resolver 區塊。主題已不在 Python 端注入
# (改由 Java ArtifactAssembler 統一注入),故不再需要剝 erd-theme。
_INJECTED_SCRIPT_IDS = ("erd-results-data", RESOLVER_SCRIPT_ID)
_INJECTED_BLOCK_PATTERN = re.compile(
    r"<script\s+id=\"(?:" + "|".join(_INJECTED_SCRIPT_IDS) + r")\"[^>]*>.*?</script>",
    re.DOTALL,
)

# json.dumps 原生支援的 cell 型別；其餘一律經 jsonable_cell 轉換,見該函式說明。
_JSON_NATIVE_CELL_TYPES = (str, int, float, bool, type(None))


def next_query_id(workspace: SessionWorkspace) -> str:
    """`q{N}`,N＝`queries/*.sql` 現存數＋1(跨 turn 遞增,迭代 turn 不重號)。"""
    existing_count = len(list(workspace.queries_dir.glob("*.sql")))
    return f"q{existing_count + 1}"


def jsonable_cell(value: object) -> object:
    """把 DuckDB 回傳、`json.dumps` 不原生支援的 cell 型別轉成 JSON-safe 值:`Decimal` 轉
    `float`;`date`/`datetime` 轉 ISO-8601 字串;`bytes` 與其餘未知型別一律 `str()` 兜底
    (never-raise——落檔不能因為欄位型別冷門就整條 SQL_ERROR)。str/int/float/bool/None
    原樣通過(已是 JSON-safe 的值再套一次仍是自己——冪等,所以 record_query 與呼叫端各自
    正規化一次不會互相干擾,見 normalize_rows/record_query 的說明)。"""
    if isinstance(value, _JSON_NATIVE_CELL_TYPES):
        return value
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    return str(value)


def normalize_rows(rows: list[list]) -> list[list]:
    """對外公開的批次版 `jsonable_cell`——逐列逐 cell 正規化。落檔(`record_query`)與
    agent 層的 wire 表示(`ToolRunRecord.rows`,見 `app.agent.tools.data`)必須用同一份
    正規化結果,否則落檔可讀但事件層 `json.dumps(record.rows)` 對 Decimal/date/datetime
    仍會 TypeError——調用端應在拿到 DuckDB 原始 rows 後立刻呼叫一次,把同一份結果同時餵給
    `record_query` 與自己要保留的 record。"""
    return [[jsonable_cell(cell) for cell in row] for row in rows]


def _dedupe_columns(columns: list[str]) -> list[str]:
    """重複欄名首見保留原名,後續依序加 `_2`/`_3` 後綴;後綴避開所有原始欄名(不偷走原本
    就存在的 `a_2` 這種名字),遞增到唯一為止。"""
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
    """寫 `queries/{query_id}.sql` 與 `results/{query_id}.json`。超過 STORE_MAX_ROWS 時
    truncated 強制 True;rows 一律經 `normalize_rows` 正規化。這是對外公開的 API,不能假設
    呼叫端已先正規化過,故內部再做一次——`jsonable_cell` 對已正規化的值是恆等函式,重複呼叫
    無害。落檔的 rows 是「以欄名為 key」的物件列(`dict(zip(columns, row))`),不是陣列列
    ——呼叫端(`data.py`)的 wire 表示(`ToolRunRecord`)與 markdown 預覽仍是陣列列,兩個
    通道自此分岔,呼叫簽章不變、只有這裡的落檔形狀變了。`columns` 仍保留在 payload 裡,
    dashboard 的明細表需要欄位順序。
    """
    (workspace.queries_dir / f"{query_id}.sql").write_text(sql, encoding="utf-8")

    stored_rows = rows[:STORE_MAX_ROWS]
    is_truncated = truncated or len(rows) > STORE_MAX_ROWS
    # 重複欄名(SELECT * join 同名欄)會讓 dict(zip) 靜默丟欄且 Proxy 攔不到——去重加後綴,
    # payload 的 columns 同步改寫,欄名與物件 key 保持一致。
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
    """讀全部 `results/*.json`,key＝query_id。單一檔案損毀(併發寫入、process 被砍到一半)
    只跳過那一筆並記警告,不讓一份壞檔卡死整個 session。
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
    """HTML 中所有 `__ERD_RESULTS__["qN"]` / `__ERD_RESULTS__['qN']` 引用到的 query_id。"""
    return set(_REFERENCED_QUERY_ID_PATTERN.findall(html))


# 每列包 Proxy:錯欄名(含 index 存取)直接 throw,安靜 NaN 變成修復鏈路接得到的爆炸;
# symbol/原型屬性/toJSON/then 探測放行,資料形狀不變。
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
    """`<script id="erd-results-data">...</script>`,id 標記供 `strip_injected_blocks` 剝除。
    每個 `<` 逃脫成 `\\u003c`(不只逃 `</`):一個 cell 裡的 `<!--` 會讓 HTML5 tokenizer 進入
    escaped state,之後沒有斜線的 `<script` 就能存活進 double-escaped state,讓後面的
    `</script>` 失效終止不了標籤——逃脫每個 `<` 才能堵住這條路。JSON 賦值後跟著 rows 的
    Proxy 包裝碼(見 `_ROWS_PROXY_SCRIPT`),同一個 script 標籤,剝除契約不變。"""
    serialized = json.dumps(results, ensure_ascii=False).replace("<", "\\u003c")
    return (
        f'<script id="erd-results-data">window.__ERD_RESULTS__ = {serialized};'
        f"{_ROWS_PROXY_SCRIPT}</script>"
    )


def inject_results(html: str, results: dict[str, dict]) -> str:
    """插入點優先序 `</head>` → `<body...>` 之後 → 前置。"""
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


# 綁定 manifest 的標題——模型看到的第一行,明講「不要憑記憶對編號」。
_WIRING_MANIFEST_HEADER = (
    "Query results currently available in window.__ERD_RESULTS__ "
    "(bind dashboard blocks by these ids and columns -- NEVER guess a q-number from memory):"
)


def format_wiring_manifest(results: dict[str, dict]) -> str:
    """把 `load_all_results` 的結果攤成 `qid -- intent -- columns` 的逐行清單;空結果回空字串。

    依 qid 排序而非 dict 順序,讓同一輪內重複呼叫產生一致的字串(避免 prompt 前綴每次都
    因為 filesystem glob 順序抖動而不同)。
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
    """剝除 `build_results_script`/`theme.ERD_THEME_SCRIPT` 注入的 `<script id="erd-...">`
    區塊,拿回未注入的乾淨基底(continue-edit 重新注入前必須先剝,否則會疊出兩份)。只認得
    帶 id 的區塊;沒有匹配時原樣返回,冪等——對已剝過的 HTML 再呼叫一次是恆等操作。
    """
    return _INJECTED_BLOCK_PATTERN.sub("", html)
