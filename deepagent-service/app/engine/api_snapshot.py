"""把 connector 回應寫成 JSON 檔, 交給 DuckDB read_json_auto 建成本輪的表.
信封 dict 只落 data, 其他頂層欄位回給呼叫端. engine 層只用 stdlib, 不 import LLM 框架."""

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
    """payload 拆封後是 0 列時拋出, 因為 DuckDB 的 read_json_auto 推不出 schema, 落表前先擋下.
    錯誤訊息會點名是哪張表落空, 方便 agent 轉告使用者, 例如建議換一組會回資料的參數重試."""

    def __init__(self, table_name: str) -> None:
        super().__init__(
            f"cannot land empty response as table {table_name!r}: payload has no rows, so "
            "DuckDB read_json_auto has no schema to infer -- retry with different call "
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
    """list 直接回傳; dict 有 data (list, null 或空 dict) 就回 (data, 其餘頂層欄位); FastMCP 把非 dict
    回傳值包成 {"result": ...}, 只有這一個 key 且內容是 list, dict, null 或空字串時先拆開再套同樣規則;
    其他形狀原樣落表."""
    if isinstance(payload, list):
        return payload, {}
    if isinstance(payload, dict) and "data" in payload:
        data = payload["data"]
        if data is None or data == {} or isinstance(data, list):
            envelope_fields = {key: value for key, value in payload.items() if key != "data"}
            return data, envelope_fields
    if isinstance(payload, dict) and set(payload) == {"result"}:
        inner = payload["result"]
        if inner is None or inner == "" or isinstance(inner, list | dict):
            return unwrap_envelope(inner)
    return payload, {}


def _is_empty_payload(data: Any) -> bool:
    """null, 空字串, 空 list, 空 dict 都算沒資料."""
    if data is None:
        return True
    if isinstance(data, str | list | dict):
        return len(data) == 0
    return False


def mount_json_file(
    connection: duckdb.DuckDBPyConnection,
    connection_lock: threading.Lock,
    table_name: str,
    json_path: Path,
) -> tuple[list[str], int]:
    """把現有的 JSON 檔案掛成一張 DuckDB 表, schema 交給 read_json_auto 推斷, 回傳欄位名稱
    與列數. 整個過程要在鎖內執行, 因為 connection 不是 thread-safe 的."""
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
    """把一次 connector 呼叫的回應落成這一輪的 DuckDB 表, table_name 會先過 _validate_alias 檢查.
    拆封後是 null, 空字串, 空 list 或空 dict 會拋出 EmptyLandingError, 不落表也不寫檔.
    同一輪內重複呼叫是後寫的贏, 用 CREATE OR REPLACE TABLE 覆蓋."""
    _validate_alias(table_name)
    data, envelope_fields = unwrap_envelope(payload)
    if _is_empty_payload(data):
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
