"""Agent-facing `fetch_api_data` tool——httpx 呼叫 registry 登記的 mock API、正規化回應、
落地快照，並掛上鎖定(`enable_external_access=false`/`lock_configuration=true`)的 DuckDB
connection。httpx 只在這層合法(engine/ 是 stdlib-only)。

never-raise 契約：任何失敗都回傳 `PARAM_ERROR: ...` / `API_ERROR: ...` 字串，不讓例外逃出
給 agent。快照不是查詢結果，不接 `ToolResultRecorder`，不發 TABLE 事件。
"""

import threading
from datetime import UTC, datetime

import duckdb
import httpx
from langchain_core.tools import BaseTool, tool

from app.agent.tools.framing import frame_data_content
from app.config import get_settings
from app.engine.api_registry import ApiDefinition, validate_params
from app.engine.api_snapshot import (
    SnapshotMeta,
    infer_column_types,
    normalize_json_array,
    sanitize_column_names,
    write_snapshot,
)
from app.engine.workspace import SessionWorkspace


def build_api_tools(
    connection: duckdb.DuckDBPyConnection,
    workspace: SessionWorkspace,
    registry: dict[str, ApiDefinition],
    transport: httpx.BaseTransport | None = None,
) -> list[BaseTool]:
    # 鏡射 app.agent.tools.data 的理由：deepagents 靠 SerializedToolCallsMiddleware 序列化同一輪
    # 的 tool calls，理論上不會跟這把鎖搶；但 Task 6 會把本工具跟 data.py 的三個工具掛在同一顆
    # connection 上，仍自帶一把鎖保險（同 data.py 檔頭說明的理由：DuckDB connection 非 thread-safe）。
    connection_lock = threading.Lock()

    # *_tool suffix 避免遮蔽這個 local scope 的其他名稱；@tool("fetch_api_data") 仍對 LLM
    # 暴露不帶後綴的名稱。
    @tool("fetch_api_data")
    def fetch_api_data_tool(source_id: str, params: dict) -> str:
        """Fetch data from a registered upstream API, normalize it, snapshot it, and mount
        it as a table on the DuckDB connection so it can be queried with run_sql."""
        definition = registry.get(source_id)
        if definition is None:
            available = ", ".join(sorted(registry))
            return f"PARAM_ERROR: unknown source_id {source_id!r}; available: {available}"

        validation_errors = validate_params(definition, params)
        if validation_errors:
            return f"PARAM_ERROR: {'; '.join(validation_errors)}"

        alias = definition.alias
        with connection_lock:
            existing_table_names = {
                row[0]
                for row in connection.execute(
                    "SELECT table_name FROM information_schema.tables"
                ).fetchall()
            }
        own_snapshot_meta_path = workspace.api_dir / f"{alias}.meta.json"
        if alias in existing_table_names and not own_snapshot_meta_path.is_file():
            return f"PARAM_ERROR: alias {alias!r} already taken by an uploaded file"

        settings = get_settings()
        if not settings.API_MOCK_BASE_URL:
            return "API_ERROR: API_MOCK_BASE_URL not configured"

        query_params = {
            parameter_name: (
                ",".join(str(item) for item in parameter_value)
                if isinstance(parameter_value, list)
                else parameter_value
            )
            for parameter_name, parameter_value in params.items()
        }

        try:
            with httpx.Client(
                base_url=settings.API_MOCK_BASE_URL,
                timeout=settings.API_FETCH_TIMEOUT_SECONDS,
                transport=transport,
            ) as client:
                response = client.request(
                    definition.method, definition.endpoint_path, params=query_params
                )
            if not (200 <= response.status_code < 300):
                return f"API_ERROR: HTTP {response.status_code}"
        except httpx.HTTPError as error:
            return f"API_ERROR: {error}"

        try:
            payload = response.json()
            columns, rows = normalize_json_array(payload)
        except ValueError as error:
            return f"API_ERROR: unexpected response shape: {error}"

        truncated = len(rows) > definition.max_rows
        if truncated:
            rows = rows[: definition.max_rows]
        sanitized_columns = sanitize_column_names(columns)
        schema = infer_column_types(sanitized_columns, rows)

        fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
        meta = SnapshotMeta(
            api_id=definition.id,
            alias=alias,
            params=params,
            fetched_at=fetched_at,
            schema=schema,
            row_count=len(rows),
            truncated=truncated,
        )
        write_snapshot(workspace, meta, sanitized_columns, rows, raw_text=response.text)

        column_ddl = ", ".join(f'"{column_name}" {duck_type}' for column_name, duck_type in schema)
        with connection_lock:
            connection.execute(f'CREATE OR REPLACE TABLE "{alias}" ({column_ddl})')
            if rows:
                placeholders = ", ".join("?" for _ in schema)
                connection.executemany(f'INSERT INTO "{alias}" VALUES ({placeholders})', rows)

        schema_summary = ", ".join(
            f"{column_name} {duck_type}" for column_name, duck_type in schema
        )
        truncated_note = f", truncated to {definition.max_rows}" if truncated else ""
        summary = f"mounted table {alias} ({len(rows)} rows{truncated_note}): {schema_summary}"
        return frame_data_content(summary)

    return [fetch_api_data_tool]
