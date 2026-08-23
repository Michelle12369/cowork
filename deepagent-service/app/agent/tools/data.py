"""Agent-facing DuckDB 探索/查詢工具——get_schema、run_sql、preview_data。run_sql 成功時把
結果落檔並交給呼叫端的 per-request `ToolResultRecorder`；SQL 失敗時不落檔。query_id
（`qN`）是單一 id 空間：模型看到的 `tableId: qN` 與落檔後 `__ERD_RESULTS__["qN"]` 是同一個
id。一輪可吐多個平行 tool_calls（每個 sync `@tool` 落在不同 executor thread），因此三個
工具共用一把 `connection_lock`：DuckDB connection 非 thread-safe，且拿 query_id 與落檔
必須在同一臨界區，否則併發呼叫可能撞出重複 query_id 或錯配的檔案組。
"""

import datetime
import decimal
import math
import re
import threading

import duckdb
from langchain_core.callbacks import Callbacks
from langchain_core.tools import BaseTool, tool

from app.agent.tools.framing import frame_data_content
from app.agent.tools.recording import ToolResultRecorder, ToolRunRecord, tool_run_id
from app.engine.api_fetch import (
    FETCH_ERROR_PREFIX,
    ConnectorFetchError,
    execute_fetch,
    land_snapshot,
    load_fetch_records,
    record_fetch,
    snapshot_fingerprint,
    stage_snapshot_for_validation,
)
from app.engine.connectors import MAX_FETCHES_PER_TURN, SAFE_IDENTIFIER_PATTERN, ConnectorRegistry
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
    connectors: ConnectorRegistry | None = None,
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

    tools: list[BaseTool] = [get_schema_tool, run_sql_tool, preview_data_tool]
    if connectors is not None and not connectors.is_empty():
        tools.append(_build_fetch_api_data_tool(connection, workspace, connectors, connection_lock))
    return tools


def _build_fetch_api_data_tool(
    connection: duckdb.DuckDBPyConnection,
    workspace: SessionWorkspace,
    connectors: ConnectorRegistry,
    connection_lock: threading.Lock,
) -> BaseTool:
    """獨立建構函式(而非直接塞進 build_data_tools 主體)只是為了讓驗證/查候選邏輯不再擠在
    一個函式裡;仍與另外三個工具共用呼叫端傳入的同一把 connection_lock。"""
    fetch_count = {"used": 0}

    def _last_fingerprint_for_alias(alias: str) -> str | None:
        # fetches.json 是 append-only 軌跡,同 alias 可能有多筆(每次刷新都 append 一筆)——
        # 要判斷「這個表現在是不是同源」,看的是最後一筆,不是第一筆。`.get()` 兩欄都要有才算
        # 一筆有效紀錄——pre-§12 舊格式紀錄沒有 fingerprint 欄,直接 `record["fingerprint"]`
        # 會 KeyError 炸出 fetch_api_data_tool 的 never-raise 契約(§12 review finding 1),
        # 與 chat_turn.py remount 側的 `if "fingerprint" in record` 同一道防線。
        last_fingerprint = None
        for record in load_fetch_records(workspace):
            if record.get("alias") == alias and record.get("fingerprint") is not None:
                last_fingerprint = record["fingerprint"]
        return last_fingerprint

    def _resolve_lookup_alias(lookup_connector_name: str, mounted: set[str]) -> str | None:
        # 約定:lookup 一律用 connector 名當掛載 alias(Task 6 prompt 規範)。找不到才反查
        # fetch 紀錄——模型也可能用別的 alias 掛過同一個 lookup connector。兩個分支都必須用
        # `mounted` 過濾:fetch 紀錄是歷史軌跡,不保證該 alias 現在還掛著(模型可能事後
        # `run_sql("DROP TABLE ...")`)——回傳一個已經不存在的表名會讓後面的 SELECT 對著
        # 空氣查詢,炸出未捕捉的 duckdb.CatalogException,違反 fetch 工具 never-raise 契約。
        if lookup_connector_name in mounted:
            return lookup_connector_name
        for record in load_fetch_records(workspace):
            if record["connector"] == lookup_connector_name and record["alias"] in mounted:
                return record["alias"]
        return None

    def _nearest_candidates(lookup_alias: str, column: str, value: str) -> list[str]:
        # column/alias 來自 config(已過識別字驗證),仍以白名單樣式雙保險;值走參數綁定。
        rows = (
            connection.cursor()
            .execute(
                f'SELECT DISTINCT "{column}" FROM "{lookup_alias}" '
                f'ORDER BY levenshtein(lower(CAST("{column}" AS VARCHAR)), lower(?)) LIMIT 5',
                [value],
            )
            .fetchall()
        )
        return [str(row[0]) for row in rows]

    def _validate_fetch_params(definition, params: dict, mounted: set[str]) -> str | None:
        known_param_names = set(definition.params)
        unknown_param_names = set(params) - known_param_names
        if unknown_param_names:
            return (
                f"{FETCH_ERROR_PREFIX}: 未知參數 {sorted(unknown_param_names)}——"
                f"{definition.name} 合法參數: {sorted(known_param_names)}。請修正後重試。"
            )
        for param_name, param in definition.params.items():
            if param.required and param_name not in params:
                return (
                    f"{FETCH_ERROR_PREFIX}: 缺少必填參數 {param_name!r}(型別 {param.type})——"
                    "請補上後重試。"
                )
        for param_name, param in definition.params.items():
            if param_name not in params:
                continue
            param_value = params[param_name]
            if param.type == "date":
                try:
                    datetime.date.fromisoformat(str(param_value))
                except ValueError:
                    return (
                        f"{FETCH_ERROR_PREFIX}: 參數 {param_name!r} 日期格式錯誤"
                        f"(收到 {param_value!r})——請用 YYYY-MM-DD 格式(例: 2026-08-20)重試。"
                    )
            elif param.type == "int":
                try:
                    int(param_value)
                except (TypeError, ValueError):
                    return (
                        f"{FETCH_ERROR_PREFIX}: 參數 {param_name!r} 型別錯誤"
                        f"(收到 {param_value!r})——請提供整數後重試。"
                    )
            elif param.type == "float":
                try:
                    float(param_value)
                except (TypeError, ValueError):
                    return (
                        f"{FETCH_ERROR_PREFIX}: 參數 {param_name!r} 型別錯誤"
                        f"(收到 {param_value!r})——請提供數字後重試。"
                    )
            if param.validate_against is not None:
                lookup_connector_name = param.validate_against.connector
                lookup_alias = _resolve_lookup_alias(lookup_connector_name, mounted)
                if lookup_alias is None:
                    return (
                        f"{FETCH_ERROR_PREFIX}: 參數 {param_name!r} 需要先驗證合法值——"
                        f"請先呼叫 fetch_api_data(connector={lookup_connector_name!r}) 取得可用值,"
                        "再重試本次呼叫。"
                    )
                validate_column = param.validate_against.column
                if not SAFE_IDENTIFIER_PATTERN.fullmatch(
                    validate_column
                ) or not SAFE_IDENTIFIER_PATTERN.fullmatch(lookup_alias):
                    return (
                        f"{FETCH_ERROR_PREFIX}: connector config 的 validate_against 識別字非法,"
                        "請聯繫維運修正設定。"
                    )
                # 第二層防線:`_resolve_lookup_alias` 已經過濾過 `mounted`,但兩者之間仍有
                # 極小的 TOCTOU 窗口(理論上;連線在 connection_lock 內序列化,目前不會發生)
                # ——查詢一律包 duckdb.Error,never-raise 契約不能靠上游過濾單一路徑保證。
                try:
                    matched_row_count = (
                        connection.cursor()
                        .execute(
                            f'SELECT COUNT(*) FROM "{lookup_alias}" WHERE "{validate_column}" = ?',
                            [str(param_value)],
                        )
                        .fetchone()[0]
                    )
                except duckdb.Error:
                    return (
                        f"{FETCH_ERROR_PREFIX}: 參數 {param_name!r} 需要先驗證合法值,但 "
                        f"{lookup_connector_name} 的掛載表已不存在(可能被中途 DROP)——"
                        f"請先呼叫 fetch_api_data(connector={lookup_connector_name!r}) 重新取得,"
                        "再重試本次呼叫。"
                    )
                if matched_row_count == 0:
                    try:
                        candidates = _nearest_candidates(
                            lookup_alias, validate_column, str(param_value)
                        )
                    except duckdb.Error:
                        return (
                            f"{FETCH_ERROR_PREFIX}: 參數 {param_name!r} 需要先驗證合法值,但 "
                            f"{lookup_connector_name} 的掛載表已不存在(可能被中途 DROP)——"
                            f"請先呼叫 fetch_api_data(connector={lookup_connector_name!r}) 重新"
                            "取得,再重試本次呼叫。"
                        )
                    return (
                        f"{FETCH_ERROR_PREFIX}: 參數 {param_name!r} 值 {param_value!r} 不存在於 "
                        f"{lookup_connector_name}(欄位 {validate_column})——最接近的候選: "
                        f"{candidates}。請改用候選值之一,或確認拼字後重試。"
                    )
        return None

    @tool("fetch_api_data")
    def fetch_api_data_tool(connector: str, params: dict, alias: str) -> str:
        """Fetch data from a configured API connector into a queryable table.

        connector: 一個已設定的 connector 名稱(見 system prompt 的資料源清單)。
        params: 該 connector 宣告的參數(名稱→值)。alias: 掛載後的表名(底線識別字)。
        """
        with connection_lock:
            if fetch_count["used"] >= MAX_FETCHES_PER_TURN:
                return (
                    f"{FETCH_ERROR_PREFIX}: 本輪 fetch 次數已達上限({MAX_FETCHES_PER_TURN})。"
                    "請先用 run_sql 分析既有資料,或請使用者下一輪再繼續。"
                )
            definition = connectors.get(connector)
            if definition is None:
                available = ", ".join(item.name for item in connectors.all())
                return f"{FETCH_ERROR_PREFIX}: connector {connector!r} 不存在。可用: {available}"
            # 身分＝(connector, params) 的指紋(§12.4);alias 降為表名/顯示名——碰撞語意看的是
            # 指紋是否同源,不是 alias 是否已被佔用。
            fingerprint = snapshot_fingerprint(connector, params)
            if not SAFE_IDENTIFIER_PATTERN.fullmatch(alias):
                return (
                    f"{FETCH_ERROR_PREFIX}: alias {alias!r} 非法——只能用字母/數字/底線,"
                    "請換一個(例: yield_data)再呼叫一次。"
                )
            mounted = {
                row[0]
                for row in connection.cursor()
                .execute("SELECT table_name FROM information_schema.tables")
                .fetchall()
            }
            if alias in mounted and _last_fingerprint_for_alias(alias) != fingerprint:
                # alias 表已存在,但不是同一個 (connector, params) 身分——換了查詢卻撞了表名。
                # 靜默沖掉會讓模型/使用者以為還是原本語意的資料,一律退貨叫換名。alias 表存在
                # 但 fetches.json 查無記錄(例如上傳檔掛的表)同樣落在這支——沒有身分可比對,
                # 一律視為衝突。
                return (
                    f"{FETCH_ERROR_PREFIX}: alias {alias!r} 已用於不同查詢(表名衝突),"
                    "請換一個名稱重試。"
                )
            # 上面判斷若 alias 已存在且同指紋,代表這是同源重抓——放行到底,下面用
            # CREATE OR REPLACE 覆蓋刷新(M9 化解:原本「alias 已存在就退貨」擋掉合理的重抓)。
            validation_error = _validate_fetch_params(definition, params, mounted)
            if validation_error is not None:
                return validation_error
            try:
                payload = execute_fetch(definition, params)
            except ConnectorFetchError as fetch_error:
                return f"{FETCH_ERROR_PREFIX}: {fetch_error}"
            # Stage-then-swap(§12 review finding 3):驗証(parse+列數)一律先對暫存表/暫存檔
            # 跑,通過才落正式指紋檔、把暫存表轉正——舊表、舊指紋檔在驗証通過前完全不動。舊序是
            # 先 CREATE OR REPLACE 真表+落正式檔再驗証,同指紋刷新若拿到超量或壞掉的回應,會先
            # 把好資料沖掉才發現要回滾,回滾後舊表/舊檔都沒了,只留一句 FETCH_ERROR。
            stage_table = f"__fetch_stage_{alias}"
            stage_path = stage_snapshot_for_validation(workspace, fingerprint, payload)
            try:
                connection.execute(
                    f'CREATE OR REPLACE TABLE "{stage_table}" AS SELECT * FROM read_json_auto(?)',
                    [str(stage_path)],
                )
            except duckdb.Error as mount_error:
                stage_path.unlink(missing_ok=True)
                return (
                    f"{FETCH_ERROR_PREFIX}: 回應不是可解析的 JSON 表格({mount_error})。"
                    "請確認參數正確;若持續失敗請如實告知使用者。"
                )
            row_count = connection.execute(f'SELECT COUNT(*) FROM "{stage_table}"').fetchone()[0]
            if row_count > definition.limits.max_rows:
                connection.execute(f'DROP TABLE "{stage_table}"')
                stage_path.unlink()
                return (
                    f"{FETCH_ERROR_PREFIX}: 回應 {row_count} 列超過上限"
                    f"({definition.limits.max_rows})——請縮小查詢範圍(如日期區間)。"
                )
            # 驗証通過,才動真的:落正式指紋檔(覆蓋同指紋的舊檔——此刻起才允許沖掉),暫存表
            # 轉正成真表,清掉暫存產物。
            land_snapshot(workspace, fingerprint, payload)
            connection.execute(
                f'CREATE OR REPLACE TABLE "{alias}" AS SELECT * FROM "{stage_table}"'
            )
            connection.execute(f'DROP TABLE "{stage_table}"')
            stage_path.unlink(missing_ok=True)
            record_fetch(workspace, fingerprint, alias, connector, params)
            fetch_count["used"] += 1
            schema_rows = (
                connection.cursor()
                .execute(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_name = ? ORDER BY ordinal_position",
                    [alias],
                )
                .fetchall()
            )
            sample_rows = connection.execute(f'SELECT * FROM "{alias}" LIMIT 3').fetchall()
        schema_line = ", ".join(f"{name} {dtype}" for name, dtype in schema_rows)
        empty_note = "\n(0 rows——資料為空,請如實告知使用者,勿臆測內容)" if row_count == 0 else ""
        sample_markdown = _render_markdown(
            [name for name, _ in schema_rows], [list(row) for row in sample_rows], truncated=False
        )
        return (
            f"table {alias} mounted ({row_count} rows)\n"
            + frame_data_content(f"schema: {schema_line}\n{sample_markdown}")
            + empty_note
        )

    return fetch_api_data_tool
