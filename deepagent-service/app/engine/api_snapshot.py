"""這個模組把 connector API 的回應直接落地成表, 交給 DuckDB 的 read_json_auto 推斷 schema,
只守兩條底線: table_name 要先過 _validate_alias, 頂層是空陣列就不落表, 因為 0 列推不出 schema.
非空的信封 dict(像 {"data": [...], "errorCode": ""})會先拆封, data 落表, 其他頂層欄位原樣
附在回饋文字裡回給呼叫端; 不是信封形狀的就整包落成單列表, 欄位形狀交給 DuckDB 自己推斷,
巢狀陣列或物件會變成 LIST 或 STRUCT 欄位.

落表用的目錄由呼叫端注入, 每一輪一個暫存目錄, 輪次結束就整個刪掉; 這個模組不會持久化任何
檔案, 不記雜湊, 也不會跨輪重新掛載. 呼叫端一定要用同一把 connection_lock 包住所有存取 DuckDB
connection 的地方, 因為這個 connection 不是 thread-safe 的.

這是 engine 層, 只能用 stdlib, 不能 import 任何 LLM 框架(ruff 的 TID251 規則會擋下來).
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
    """payload 拆封後是 0 列時拋出, 因為 DuckDB 的 read_json_auto 推不出 schema, 落表前先擋下.
    錯誤訊息會點名是哪張表落空, 方便 agent 轉告使用者, 例如建議換一組會回資料的參數重試."""

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
    """如果 payload 是 list, 回傳 (payload, {}); 如果是 dict 且頂層 data 欄位是 list, 就回傳
    (data, 其餘頂層欄位); 其他形狀(非信封的 dict, 純量等)一律回傳 (payload, {}), 原樣落表."""
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
    """把一次 connector 呼叫的回應(payload, 已經解析好的 JSON 值)落成這一輪的 DuckDB 表.
    一定要先通過 _validate_alias 檢查 table_name 才會動作; 拆封後如果是 0 列就拋出
    EmptyLandingError, 不落表也不寫檔. 檔案寫在 landing_dir/{table_name}.json, 同一輪內
    重複呼叫是後寫的贏, 用 CREATE OR REPLACE TABLE 覆蓋."""
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
