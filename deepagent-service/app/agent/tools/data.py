"""給 agent 用的 DuckDB 探索和查詢工具: get_schema, run_sql, preview_data.
三個工具共用同一把 connection_lock, 因為 DuckDB connection 不是 thread-safe 的.
connection_lock 可由呼叫端注入共用, 沒提供時就自己建一把."""

import decimal
import math
import re
import threading

import duckdb
from langchain_core.tools import BaseTool, tool

from app.agent.tools.framing import frame_data_content
from app.engine.results import STORE_MAX_ROWS, next_query_id, normalize_rows, record_query
from app.engine.workspace import SessionWorkspace

# 給 LLM 看的 view 層上限, 跟落檔用的 STORE_MAX_ROWS 是分開的兩件事.
# 模型不需要看到全量列, 只需要足夠判斷查詢對不對的樣本.
LLM_VIEW_MAX_ROWS = 200

# 這是顯示用的位數, 12 個有效數字去噪, 不是固定小數位的四捨五入.
_DISPLAY_SIGNIFICANT_DIGITS = 12

# table 名只允許 unicode 字母, 數字, 底線, 避免被注入進 SELECT * FROM "{table}" 這種語句.
_SAFE_TABLE_NAME_PATTERN = re.compile(r"^\w+$", re.UNICODE)


def _format_display_number(value: object) -> str:
    """把 float 縮到 _DISPLAY_SIGNIFICANT_DIGITS 個有效數字, 並去掉多餘的尾端零; int 和
    整數值的 Decimal 直接顯示, 不帶小數點."""
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


def render_markdown_table(
    columns: list[str], rows: list[list], truncated: bool, max_rows: int = LLM_VIEW_MAX_ROWS
) -> str:
    """把欄名和列資料轉成 markdown 表格, 截到 max_rows 筆, 超過時附上一行註記. 這個函式
    故意公開, 因為 app.agent.connectors.wrapper 也會重用它(落表回饋的預覽用不同的列數
    上限)."""
    view_rows = rows[:max_rows]
    header = "| " + " | ".join(columns) + " |"
    divider = "| " + " | ".join("---" for _ in columns) + " |"
    body = [
        "| " + " | ".join(_render_markdown_cell(value) for value in row) + " |" for row in view_rows
    ]
    table = "\n".join([header, divider, *body])
    if truncated or len(rows) > max_rows:
        table += f"\n(truncated to {max_rows} rows)"
    return table


def build_data_tools(
    connection: duckdb.DuckDBPyConnection,
    workspace: SessionWorkspace,
    connection_lock: "threading.Lock | None" = None,
) -> list[BaseTool]:
    # 三個工具存取 connection 全部序列化在同一把鎖下, 沒提供 connection_lock 就自己建一把.
    # 型別標註用字串(forward reference), 避免 Lock | None 在函式定義當下就求值出 TypeError.
    if connection_lock is None:
        connection_lock = threading.Lock()

    # *_tool suffix avoids shadowing helper names in this local scope; @tool("...") still
    # exposes the bare name (get_schema/run_sql/preview_data) to the LLM.
    @tool("get_schema")
    def get_schema_tool() -> str:
        """List every mounted table with its columns and types."""
        # 表名和欄名跟 cell 值一樣是使用者可控內容, 所以一起 frame 起來.
        # 用固定 SQL 一次撈出全部表的欄位, 不做識別字插值.
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
    def run_sql_tool(sql: str, intent: str) -> str:
        """Run a DuckDB SQL query against the mounted tables and return the result.

        intent is required: one sentence, in the user's language, stating what question this
        query answers -- not a paraphrase of the SQL -- so a human can check the intent against
        the actual query."""
        # 執行查詢, fetch, 拿 query_id, 落檔要是同一個 critical section, 避免併發撞出重複 id.
        # markdown 組裝不碰共享狀態, 放到鎖外面做.
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
            # record_query 落檔前先正規化一次, DuckDB 原生的 Decimal, date, datetime 值
            # 才能被 json.dumps 安全序列化(細節看 app.engine.results.normalize_rows).
            rows = normalize_rows(raw_rows)

            query_id = next_query_id(workspace)
            record_query(workspace, query_id, sql, intent, columns, rows, truncated)

        markdown = render_markdown_table(columns, rows, truncated)
        return f"tableId: {query_id}\n\n{frame_data_content(markdown)}"

    @tool("preview_data")
    def preview_data_tool(table: str) -> str:
        """Return the first rows of a mounted table (default 10)."""
        if not _SAFE_TABLE_NAME_PATTERN.fullmatch(table):
            return f"SQL_ERROR: invalid table name: {table!r}"
        with connection_lock:
            try:
                # relation API 由 DuckDB 內部處理表名的 quoting, 這裡不用組 SQL 字串.
                relation = connection.table(table).limit(10)
                columns = list(relation.columns)
                rows = [list(row) for row in relation.fetchall()]
            except duckdb.Error as error:
                return f"SQL_ERROR: {error}"
            except Exception as error:  # noqa: BLE001 -- never-raise contract, forward as SQL_ERROR
                return f"SQL_ERROR: {error}"
        # 這裡不落檔, 因為 preview 不佔用 query_id 空間, 純粹是探索用途.
        return frame_data_content(render_markdown_table(columns, rows, truncated=False))

    return [get_schema_tool, run_sql_tool, preview_data_tool]
