"""Agent-facing DuckDB 探索/查詢工具——get_schema、run_sql、preview_data。

run_sql 成功時自動把結構化結果落檔（app.engine.results.record_query），並把同一筆結果交給
呼叫端提供的 per-request `ToolResultRecorder`（app.agent.tools.recording）供事件層在
on_tool_end 依 run_id 取出以發送 TABLE 事件；SQL 失敗時不落檔、不記錄。query_id（`qN`）是單一
id 空間：run_sql 回傳給模型的 `tableId: qN` 與落檔後 `__ERD_RESULTS__["qN"]` 是同一個 id。

recorder 一律 per-request 建立（見 app.main.chat），避免併發 `/chat` 請求互相覆寫暫存記錄、
造成跨請求 TABLE 資料洩漏（見 recording.ToolResultRecorder 的說明）。

同一個 turn 內也可能併發：一輪可以吐多個平行 tool_calls，LangGraph 的 ToolNode 把每個 sync
`@tool` 丟進不同 executor thread 執行。`build_data_tools` 因此在 closure 裡建一把
`threading.Lock`（`connection_lock`），把三個工具對 `connection` 的存取與
`next_query_id`/`record_query` 全包在同一把鎖內——DuckDB 單一 connection 非 thread-safe,
且拿號與落檔須是同一臨界區,否則併發呼叫可能拿到同一個 query_id、寫出錯配的檔案組。
`next_query_id` 維持「數 queries/*.sql 現存數」的檔案計數語意不變,只是把它搬進鎖內執行。
"""

import decimal
import math
import re
import threading

import duckdb
from langchain_core.callbacks import Callbacks
from langchain_core.tools import BaseTool, tool

from app.agent.tools.framing import frame_data_content
from app.agent.tools.recording import ToolResultRecorder, ToolRunRecord, tool_run_id
from app.engine.results import STORE_MAX_ROWS, next_query_id, normalize_rows, record_query
from app.engine.workspace import SessionWorkspace

# LLM VIEW 層——markdown 截到這裡給模型看，獨立於落檔用的 STORE_MAX_ROWS（app.engine.results，
# 目前 5000）：模型不需要看到落檔保留的全量列，只需要足夠判斷查詢對不對的樣本。
LLM_VIEW_MAX_ROWS = 200

# 顯示位數（12 有效數字去噪，不是固定小數位 round）。
_DISPLAY_SIGNIFICANT_DIGITS = 12

# table 名只允許 unicode 字母/數字/底線，避免注入進 `SELECT * FROM "{table}"`。
_SAFE_TABLE_NAME_PATTERN = re.compile(r"^\w+$", re.UNICODE)


def _format_display_number(value: object) -> str:
    """把 float 縮到 `_DISPLAY_SIGNIFICANT_DIGITS` 有效數字並去掉多餘的尾端零；int/整數值
    Decimal 直接顯示不帶小數點。"""
    if isinstance(value, decimal.Decimal):
        value = float(value)
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return str(value)
        if value.is_integer():
            return str(int(value))
        shortened = f"{value:.{_DISPLAY_SIGNIFICANT_DIGITS}g}"
        if "e" in shortened:
            shortened = format(decimal.Decimal(shortened), "f")
        if "." in shortened:
            shortened = shortened.rstrip("0").rstrip(".")
        return shortened
    return str(value)


def _render_markdown_cell(value: object) -> str:
    if isinstance(value, (float, decimal.Decimal)):
        return _format_display_number(value)
    return str(value)


def _render_markdown(columns: list[str], rows: list[list], truncated: bool) -> str:
    """把欄名/列資料轉成 markdown 表格，截到 LLM_VIEW_MAX_ROWS 並在超過時附註記。"""
    view_rows = rows[:LLM_VIEW_MAX_ROWS]
    header = "| " + " | ".join(columns) + " |"
    divider = "| " + " | ".join("---" for _ in columns) + " |"
    body = [
        "| " + " | ".join(_render_markdown_cell(value) for value in row) + " |" for row in view_rows
    ]
    table = "\n".join([header, divider, *body])
    if truncated or len(rows) > LLM_VIEW_MAX_ROWS:
        table += f"\n(truncated to {LLM_VIEW_MAX_ROWS} rows)"
    return table


def build_data_tools(
    connection: duckdb.DuckDBPyConnection,
    workspace: SessionWorkspace,
    recorder: ToolResultRecorder,
) -> list[BaseTool]:
    # 見檔頭說明：三個工具對 connection 的存取與 run_sql 的拿號/落檔全部序列化在同一把鎖下。
    connection_lock = threading.Lock()

    # *_tool suffix avoids shadowing helper names in this local scope; @tool("...") still
    # exposes the bare name (get_schema/run_sql/preview_data) to the LLM.
    @tool("get_schema")
    def get_schema_tool() -> str:
        """List every mounted table with its columns and types."""
        # 表名/欄名來自使用者上傳的 CSV/Excel header，跟 cell 值一樣是使用者可控內容，一併 frame。
        # 常數 SQL 一次撈出全部表的欄位,Python 端分組——不做任何識別字插值。
        with connection_lock:
            column_rows = (
                connection.cursor()
                .execute(
                    "SELECT table_name, column_name, data_type "
                    "FROM information_schema.columns "
                    "ORDER BY table_name, ordinal_position"
                )
                .fetchall()
            )
        columns_by_table: dict[str, list[str]] = {}
        for table_name, column_name, data_type in column_rows:
            columns_by_table.setdefault(table_name, []).append(f"{column_name} {data_type}")
        lines = [
            f"table {table_name}: {', '.join(column_texts)}"
            for table_name, column_texts in columns_by_table.items()
        ]
        return frame_data_content("\n".join(lines))

    @tool("run_sql")
    def run_sql_tool(sql: str, intent: str, callbacks: Callbacks = None) -> str:
        """Run a DuckDB SQL query against the mounted tables and return the result.

        intent 為必填：用一句話、以使用者的語言，說明這條查詢想回答什麼問題（不是 SQL 的
        改寫），供人類核對意圖與實際查詢是否一致。
        """
        # 整段關鍵區（執行查詢 → fetch → 拿 query_id → 落檔 → 交給 recorder）必須是同一個
        # critical section，否則併發呼叫可能交錯出同一個 query_id 或錯配的檔案組（見檔頭
        # 說明）。markdown 組裝不碰共享狀態，鎖外做即可。
        with connection_lock:
            try:
                cursor = connection.cursor().execute(sql)
            except duckdb.Error as error:
                return f"SQL_ERROR: {error}"
            except Exception as error:  # noqa: BLE001 -- never-raise contract, forward as SQL_ERROR
                return f"SQL_ERROR: {error}"

            if cursor is None or cursor.description is None:
                return (
                    "SQL_ERROR: statement produced no result set (empty, whitespace-only, or "
                    "comment-only SQL is not a query)"
                )

            columns = [description[0] for description in cursor.description]
            fetched_rows = cursor.fetchmany(STORE_MAX_ROWS + 1)
            truncated = len(fetched_rows) > STORE_MAX_ROWS
            raw_rows = [list(row) for row in fetched_rows[:STORE_MAX_ROWS]]
            # 正規化一次、同一份結果同時餵 record_query（落檔）與 ToolRunRecord（wire 表示）
            # ——兩個通道的 rows 型別必須一致，否則 TABLE 事件的 json.dumps 對
            # Decimal/date/datetime 會 TypeError（見 app.engine.results.normalize_rows）。
            rows = normalize_rows(raw_rows)

            query_id = next_query_id(workspace)
            record_query(workspace, query_id, sql, intent, columns, rows, truncated)
            recorder.record(
                tool_run_id(callbacks),
                ToolRunRecord(
                    query_id=query_id,
                    intent=intent,
                    columns=columns,
                    rows=rows,
                    truncated=truncated,
                ),
            )

        markdown = _render_markdown(columns, rows, truncated)
        return f"tableId: {query_id}\n\n{frame_data_content(markdown)}"

    @tool("preview_data")
    def preview_data_tool(table: str) -> str:
        """Return the first rows of a mounted table (default 10)."""
        if not _SAFE_TABLE_NAME_PATTERN.fullmatch(table):
            return f"SQL_ERROR: 無效的資料表名稱: {table!r}"
        with connection_lock:
            try:
                # relation API 由 DuckDB 內部處理表名 quoting,不組 SQL 字串。
                relation = connection.table(table).limit(10)
                columns = list(relation.columns)
                rows = [list(row) for row in relation.fetchall()]
            except duckdb.Error as error:
                return f"SQL_ERROR: {error}"
            except Exception as error:  # noqa: BLE001 -- never-raise contract, forward as SQL_ERROR
                return f"SQL_ERROR: {error}"
        # 不落檔（preview 不佔用 query_id 空間），只是探索用途。
        return frame_data_content(_render_markdown(columns, rows, truncated=False))

    return [get_schema_tool, run_sql_tool, preview_data_tool]
