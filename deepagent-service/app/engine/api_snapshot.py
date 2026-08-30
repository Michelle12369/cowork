"""Connector API 回應的寬鬆落表管線(Phase 1 實驗,見 spec §4-2)——回應直接交 DuckDB
`read_json_auto` 推斷 schema(信封摊成怪表、淺巢狀成 STRUCT 欄照吞),只守兩條底線:
`land_as` alias 過 `_validate_alias`(安全)與頂層空陣列不落表(0 列推不出 schema)。
非空的信封 dict(如 `{"data": [...], "errorCode": ""}`)仍原樣落表——寬鬆模式下由 DuckDB
自行推斷欄位形狀(整包落成單列表,巢狀陣列/物件變成 LIST/STRUCT 欄),不做 record_path
拆封;候補機制(拆封/攤平/1NF 硬驗證)待實驗訊號觸發才建,見 spec §4-2。

snapshot 落檔於 `workspace.api_snapshots_dir/{alias}.json`(同目錄暫存檔 + os.replace()
原子改名,讀方不會看到半寫檔案),跨 turn 由 `remount_snapshots` 重新掛回 DuckDB。呼叫端
MUST 用同一把 `connection_lock` 包住 DuckDB connection 的所有存取(connection 非
thread-safe,比照 `app.agent.tools.data` 的既有作法)。

engine 層純度規則:stdlib only,禁止 import LLM 框架(ruff TID251 會擋)。
"""

import json
import logging
import os
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

from app.engine.duck import _validate_alias
from app.engine.workspace import SessionWorkspace

logger = logging.getLogger(__name__)


class EmptyLandingError(Exception):
    """payload 頂層是空陣列——DuckDB `read_json_auto` 推不出 schema,落表前擋下。訊息
    可行動:點名是哪個 alias 落空,供 agent 轉告使用者(例如換一組會回資料的參數重試)。"""

    def __init__(self, alias: str) -> None:
        super().__init__(
            f"cannot land empty response as table {alias!r}: top-level payload is an empty "
            "list, so DuckDB read_json_auto has no rows to infer a schema from — retry with "
            "different call arguments that return at least one row before landing"
        )


@dataclass(frozen=True)
class LandingResult:
    columns: list[str]
    row_count: int


def _atomic_write_json(destination_path: Path, payload: Any) -> None:
    """同目錄暫存檔 + `os.replace()` 原子改名,比照 `object_store_fs._atomic_write_with_
    parent_retry` 的手法(此處呼叫端目錄由 workspace 佈局保證已存在,不需要它的
    FileNotFoundError 重試)。任何失敗都清掉暫存檔,不留殘骸。"""
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    temp_descriptor, temp_name = tempfile.mkstemp(
        dir=destination_path.parent,
        prefix=f".{destination_path.name}.",
        suffix=".tmp",
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(temp_descriptor, "w", encoding="utf-8") as temp_file:
            json.dump(payload, temp_file, ensure_ascii=False)
        os.replace(temp_path, destination_path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def _land_snapshot_file(
    connection: duckdb.DuckDBPyConnection,
    connection_lock: threading.Lock,
    alias: str,
    snapshot_path: Path,
) -> LandingResult:
    """既有 snapshot 檔案(已落檔)重建成 DuckDB 表——`land_snapshot`(新落檔)與
    `remount_snapshots`(既存檔案跨 turn 重掛)共用同一段鎖內 SQL。"""
    with connection_lock:
        connection.execute(
            f'CREATE OR REPLACE TABLE "{alias}" AS SELECT * FROM read_json_auto(?)',
            [str(snapshot_path)],
        )
        columns = [row[0] for row in connection.execute(f'DESCRIBE "{alias}"').fetchall()]
        row_count = connection.execute(f'SELECT COUNT(*) FROM "{alias}"').fetchone()[0]
    return LandingResult(columns=columns, row_count=row_count)


def land_snapshot(
    connection: duckdb.DuckDBPyConnection,
    connection_lock: threading.Lock,
    workspace: SessionWorkspace,
    alias: str,
    payload: Any,
) -> LandingResult:
    """把一次 connector 呼叫的回應(`payload`,已解析的 JSON 值)落成 DuckDB 表——alias
    過 `_validate_alias` 才動作;頂層空陣列拋 `EmptyLandingError`,不落表、不寫檔。同一
    alias 重複呼叫是 last-wins(`CREATE OR REPLACE TABLE`),供同 turn 內重試/迭代使用。

    寫檔在鎖外(不佔用 DuckDB critical section),`CREATE OR REPLACE TABLE`/`DESCRIBE`/
    `COUNT(*)` 在鎖內——connection 非 thread-safe,見檔頭說明。
    """
    _validate_alias(alias)
    if isinstance(payload, list) and len(payload) == 0:
        raise EmptyLandingError(alias)

    snapshot_path = workspace.api_snapshots_dir / f"{alias}.json"
    _atomic_write_json(snapshot_path, payload)

    return _land_snapshot_file(connection, connection_lock, alias, snapshot_path)


def remount_snapshots(
    connection: duckdb.DuckDBPyConnection,
    connection_lock: threading.Lock,
    workspace: SessionWorkspace,
) -> list[str]:
    """跨 turn 重掛既有 snapshot 檔案(`api_snapshots/*.json`,依檔名排序)回新連線——
    connector session 每輪重新 `open_locked_connection` 後,先前落表的資料需要這一步才能
    再被查詢到。檔名(alias)理論上都經 `land_snapshot` 的 `_validate_alias` 才寫出,這裡
    仍防禦性重驗一次(檔案自寫、成本低),不合法的直接跳過並記警告,不讓一份壞檔卡死
    整個 remount。回傳實際掛上的 alias 清單(依檔名排序)。
    """
    mounted_aliases: list[str] = []
    for snapshot_path in sorted(workspace.api_snapshots_dir.glob("*.json")):
        alias = snapshot_path.stem
        try:
            _validate_alias(alias)
        except ValueError:
            logger.warning("skipping snapshot with unsafe alias: %s", snapshot_path)
            continue
        _land_snapshot_file(connection, connection_lock, alias, snapshot_path)
        mounted_aliases.append(alias)
    return mounted_aliases
