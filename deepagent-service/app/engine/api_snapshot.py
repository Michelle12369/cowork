"""Connector API 回應落表管線——回應直接交 DuckDB `read_json_auto` 推斷 schema 落表,只守
兩條底線:`table_name` 過 `_validate_alias`(安全)與頂層空陣列不落表(0 列推不出
schema)。非空的信封 dict(如 `{"data": [...], "errorCode": ""}`)先拆封,`data` 落表、其餘
頂層欄位原樣回給呼叫端附在回饋文字裡;非信封形狀則整包落成單列表,由 DuckDB 自行推斷欄位
形狀(巢狀陣列/物件變成 LIST/STRUCT 欄)。

落表目錄由呼叫端注入(每輪一個暫存目錄,turn 結束即整個刪除)——本模組不持久化任何檔案,
不記錄雜湊,不跨 turn 重掛。呼叫端 MUST 用同一把 `connection_lock` 包住 DuckDB connection
的所有存取(connection 非 thread-safe)。

engine 層純度規則:stdlib only,禁止 import LLM 框架(ruff TID251 會擋)。
"""

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

from app.engine.duck import _validate_alias
from app.engine.results import normalize_rows

LANDING_PREVIEW_MAX_ROWS = 20


class EmptyLandingError(Exception):
    """payload 拆封後 0 列——DuckDB `read_json_auto` 推不出 schema,落表前擋下。訊息
    可行動:點名是哪張表落空,供 agent 轉告使用者(例如換一組會回資料的參數重試)。"""

    def __init__(self, table_name: str) -> None:
        super().__init__(
            f"cannot land empty response as table {table_name!r}: payload has no rows, so "
            "DuckDB read_json_auto has no schema to infer — retry with different call "
            "arguments that return at least one row before landing"
        )


@dataclass(frozen=True)
class LandingResult:
    table_name: str
    columns: list[str]
    row_count: int
    preview_rows: list[list]
    envelope_fields: dict[str, Any]


def unwrap_envelope(payload: Any) -> tuple[Any, dict[str, Any]]:
    """list → (payload, {});dict 且頂層 `data` 為 list → (data, 其餘頂層欄位);其他形狀
    (非信封 dict、純量等)→ (payload, {}) 原樣落表。"""
    if isinstance(payload, list):
        return payload, {}
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        envelope_fields = {key: value for key, value in payload.items() if key != "data"}
        return payload["data"], envelope_fields
    return payload, {}


def mount_json_file(
    connection: duckdb.DuckDBPyConnection,
    connection_lock: threading.Lock,
    table_name: str,
    json_path: Path,
) -> tuple[list[str], int]:
    """既有 JSON 檔案掛成 DuckDB 表(`read_json_auto` 推斷 schema),回傳(欄名, 列數)。
    鎖內執行——connection 非 thread-safe。"""
    with connection_lock:
        connection.execute(
            f'CREATE OR REPLACE TABLE "{table_name}" AS SELECT * FROM read_json_auto(?)',
            [str(json_path)],
        )
        columns = [row[0] for row in connection.execute(f'DESCRIBE "{table_name}"').fetchall()]
        row_count = connection.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
    return columns, row_count


def land_response(
    connection: duckdb.DuckDBPyConnection,
    connection_lock: threading.Lock,
    landing_dir: Path,
    table_name: str,
    payload: Any,
) -> LandingResult:
    """把一次 connector 呼叫的回應(`payload`,已解析的 JSON 值)落成本輪 DuckDB 表
    ——`table_name` 過 `_validate_alias` 才動作;拆封後 0 列拋 `EmptyLandingError`,不落表、
    不寫檔。寫在 `landing_dir/{table_name}.json`,同 turn 內重複呼叫是 last-wins
    (`CREATE OR REPLACE TABLE`)。"""
    _validate_alias(table_name)
    data, envelope_fields = unwrap_envelope(payload)
    if isinstance(data, list) and len(data) == 0:
        raise EmptyLandingError(table_name)

    json_path = landing_dir / f"{table_name}.json"
    json_path.write_bytes(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    columns, row_count = mount_json_file(connection, connection_lock, table_name, json_path)
    with connection_lock:
        raw_preview_rows = connection.execute(
            f'SELECT * FROM "{table_name}" LIMIT {LANDING_PREVIEW_MAX_ROWS}'
        ).fetchall()
    preview_rows = normalize_rows([list(row) for row in raw_preview_rows])
    return LandingResult(
        table_name=table_name,
        columns=columns,
        row_count=row_count,
        preview_rows=preview_rows,
        envelope_fields=envelope_fields,
    )
