"""查詢結果落檔與 `__ERD_RESULTS__` 注入。

engine 層——stdlib only,禁止 import 任何 LLM 框架(ruff TID251 會擋)。

Dashboard HTML 不內嵌資料、只讀 `window.__ERD_RESULTS__["qN"]`;查詢結果由本模組
落檔(`queries/{id}.sql` + `results/{id}.json`),於發送前注入 HTML。
"""

import datetime
import decimal
import json
import re

from app.engine.workspace import SessionWorkspace

STORE_MAX_ROWS = 5000

_REFERENCED_QUERY_ID_PATTERN = re.compile(r"""__ERD_RESULTS__\s*\[\s*["'](\w+)["']\s*\]""")
_HEAD_CLOSE_PATTERN = re.compile(r"</head>", re.IGNORECASE)
_BODY_OPEN_PATTERN = re.compile(r"<body\b[^>]*>", re.IGNORECASE)

# 用來剝除本模組與 app.engine.theme 注入區塊的 id 標記——兩個 id 逐字對應
# build_results_script/theme.ERD_THEME_SCRIPT 產出的 <script id="..."> 開頭。
_INJECTED_SCRIPT_IDS = ("erd-results-data", "erd-theme")
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


def record_query(
    workspace: SessionWorkspace,
    query_id: str,
    sql: str,
    intent: str,
    columns: list[str],
    rows: list[list],
    truncated: bool,
) -> None:
    """寫 `queries/{query_id}.sql`(SQL 原文)與 `results/{query_id}.json`。

    超過 STORE_MAX_ROWS 時 truncated 強制 True。rows 逐 cell 經 `normalize_rows` 正規化
    (columns 是欄名字串,不需要);未正規化的 Decimal/date/datetime 會讓 json.dumps 直接
    TypeError,在真實 CSV(常見日期欄)上必炸,故落檔前一律過一輪。

    刻意在這裡"再正規化一次",即使呼叫端(如 app.agent.tools.data.run_sql_tool)可能已經
    對同一份 rows 呼叫過 normalize_rows 準備 wire 表示:`jsonable_cell` 對已是 JSON-native
    的值是恆等函式,重複呼叫不改變結果、也不影響效能量級(cell 數量小),所以這裡選擇「兩邊
    各自正規化一次」而非「假設呼叫端一定已正規化」——record_query 是本模組對外公開的 API,
    不能預設所有呼叫端都記得先正規化,這層防禦比省一次迴圈重要。
    """
    (workspace.queries_dir / f"{query_id}.sql").write_text(sql, encoding="utf-8")

    stored_rows = rows[:STORE_MAX_ROWS]
    is_truncated = truncated or len(rows) > STORE_MAX_ROWS
    payload = {
        "intent": intent,
        "columns": columns,
        "rows": normalize_rows(stored_rows),
        "truncated": is_truncated,
    }
    (workspace.results_dir / f"{query_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def load_all_results(workspace: SessionWorkspace) -> dict[str, dict]:
    """讀全部 `results/*.json`,key＝query_id。"""
    results: dict[str, dict] = {}
    for result_path in workspace.results_dir.glob("*.json"):
        query_id = result_path.stem
        results[query_id] = json.loads(result_path.read_text(encoding="utf-8"))
    return results


def referenced_query_ids(html: str) -> set[str]:
    """HTML 中所有 `__ERD_RESULTS__["qN"]` / `__ERD_RESULTS__['qN']` 引用到的 query_id。"""
    return set(_REFERENCED_QUERY_ID_PATTERN.findall(html))


def build_results_script(results: dict[str, dict]) -> str:
    """`<script id="erd-results-data">window.__ERD_RESULTS__ = {json};</script>`,防
    `</script>` 提前終結。id 標記讓 `strip_injected_blocks` 能確定性地剝除本區塊(見該函式
    說明)——continue-edit 選定歷史版本時,要從該版 rawHtml 重建乾淨基底。"""
    serialized = json.dumps(results, ensure_ascii=False).replace("</", "<\\/")
    return f'<script id="erd-results-data">window.__ERD_RESULTS__ = {serialized};</script>'


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
    區塊,拿回未注入的乾淨基底——continue-edit 選定歷史版本時,Java 端送來的
    `previousDashboardHtml` 是「注入後」的 artifact rawHtml,寫回 workspace 前必須先剝掉,
    否則本輪重新注入會在同一份 HTML 裡疊出兩份 `__ERD_RESULTS__`/主題 script。

    只認得帶 id 的兩個區塊(regex 非貪婪、DOTALL,一次比對可能吃掉多個 script 標籤中最短的
    那一段——用 `[^>]*` 允許屬性間有其他 attribute,`.*?` 確保在遇到第一個 `</script>` 就停);
    沒有匹配時原樣返回,冪等(對已剝過的 HTML 再呼叫一次是恆等操作)。

    刻意不處理沒有 id 的舊版注入(id 標記是本次改動才加的,存量 artifact 極少見)——剝不掉
    頂多讓基底多帶一份 stale 的舊 `<script>window.__ERD_RESULTS__ = ...;</script>`,本輪
    `inject_results`/`inject_theme` 重新注入時,新的 `__ERD_RESULTS__` 賦值語句在 DOM 順序
    上晚於舊的、會覆蓋掉它;`registerTheme('erd', ...)` 呼叫本身也是冪等的(重複註冊同名
    theme 只是覆寫,不會報錯)。用一次性的「多一份 stale script」換掉維護一條無法確定性
    比對(沒有 id 錨點)的舊格式 regex,划算。
    """
    return _INJECTED_BLOCK_PATTERN.sub("", html)
