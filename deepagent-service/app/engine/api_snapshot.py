"""Connector API 回應落表管線——回應直接交 DuckDB `read_json_auto` 推斷 schema 落表,只守
兩條底線:`land_as` alias 過 `_validate_alias`(安全)與頂層空陣列不落表(0 列推不出
schema)。非空的信封 dict(如 `{"data": [...], "errorCode": ""}`)仍原樣落表——由 DuckDB
自行推斷欄位形狀(整包落成單列表,巢狀陣列/物件變成 LIST/STRUCT 欄),不做拆封。

snapshot 落檔於 `workspace.api_snapshots_dir/{alias}.json`(同目錄暫存檔 + os.replace()
原子改名,讀方不會看到半寫檔案),跨 turn 由 `remount_snapshots` 重新掛回 DuckDB。呼叫端
MUST 用同一把 `connection_lock` 包住 DuckDB connection 的所有存取(connection 非
thread-safe)。

**完整性守則**:`allowed_directories` 白名單目錄同時開放讀與寫(見 `duck.py` docstring)
——connector session 的 `run_sql` 工具在鎖後仍可對白名單目錄下 `COPY TO`/`ATTACH`/
`EXPORT`,理論上能覆寫或竄改已落表的 snapshot 檔案。因此 `land_snapshot` 落檔時記下寫入
內容的 sha256,呼叫端(agent 層)需把每個 alias 的 sha256 持久化(如 replay manifest);下一輪
`remount_snapshots` 只認呼叫端明確列出的 `expected_hashes`,逐一驗證雜湊相符才重掛,
檔案缺失或雜湊不符一律 fail loud(`SnapshotIntegrityError`)。

engine 層純度規則:stdlib only,禁止 import LLM 框架(ruff TID251 會擋)。
"""

import hashlib
import json
import os
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

from app.engine.duck import _validate_alias
from app.engine.workspace import SessionWorkspace


class EmptyLandingError(Exception):
    """payload 頂層是空陣列——DuckDB `read_json_auto` 推不出 schema,落表前擋下。訊息
    可行動:點名是哪個 alias 落空,供 agent 轉告使用者(例如換一組會回資料的參數重試)。"""

    def __init__(self, alias: str) -> None:
        super().__init__(
            f"cannot land empty response as table {alias!r}: top-level payload is an empty "
            "list, so DuckDB read_json_auto has no rows to infer a schema from — retry with "
            "different call arguments that return at least one row before landing"
        )


class SnapshotIntegrityError(Exception):
    """跨 turn 重掛前的雜湊驗證失敗——白名單目錄同時可寫,`run_sql` 有機會覆寫或竄改
    snapshot 檔案(見檔頭「完整性守則」);偵測到缺檔或雜湊不符一律拒絕重掛,訊息點名
    是哪個 alias,供 agent 轉告使用者。"""


@dataclass(frozen=True)
class LandingResult:
    columns: list[str]
    row_count: int
    sha256: str


def _serialize_json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _atomic_write_bytes(destination_path: Path, data: bytes) -> None:
    """同目錄暫存檔 + `os.replace()` 原子改名;任何失敗都清掉暫存檔,不留殘骸。寫入的
    bytes 與呼叫端算 sha256 用的 bytes 是同一份,雜湊與落地內容保證一致。"""
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    temp_descriptor, temp_name = tempfile.mkstemp(
        dir=destination_path.parent,
        prefix=f".{destination_path.name}.",
        suffix=".tmp",
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(temp_descriptor, "wb") as temp_file:
            temp_file.write(data)
        os.replace(temp_path, destination_path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def _mount_snapshot_file(
    connection: duckdb.DuckDBPyConnection,
    connection_lock: threading.Lock,
    alias: str,
    snapshot_path: Path,
) -> tuple[list[str], int]:
    """既有 snapshot 檔案(已落檔且雜湊驗證過)重建成 DuckDB 表,回傳(欄名, 列數)——
    `land_snapshot`(新落檔)與 `remount_snapshots`(既存檔案跨 turn 重掛)共用同一段
    鎖內 SQL。"""
    with connection_lock:
        connection.execute(
            f'CREATE OR REPLACE TABLE "{alias}" AS SELECT * FROM read_json_auto(?)',
            [str(snapshot_path)],
        )
        columns = [row[0] for row in connection.execute(f'DESCRIBE "{alias}"').fetchall()]
        row_count = connection.execute(f'SELECT COUNT(*) FROM "{alias}"').fetchone()[0]
    return columns, row_count


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

    寫檔在鎖外,`CREATE OR REPLACE TABLE`/`DESCRIBE`/`COUNT(*)` 在鎖內——connection 非
    thread-safe。回傳的 `sha256` 是實際寫入 snapshot 檔案的雜湊,呼叫端 MUST 持久化(如
    replay manifest),下一輪 `remount_snapshots` 要靠它驗證檔案沒被 `run_sql` 動過手腳。
    """
    _validate_alias(alias)
    if isinstance(payload, list) and len(payload) == 0:
        raise EmptyLandingError(alias)

    snapshot_path = workspace.api_snapshots_dir / f"{alias}.json"
    serialized = _serialize_json_bytes(payload)
    snapshot_hash = hashlib.sha256(serialized).hexdigest()
    _atomic_write_bytes(snapshot_path, serialized)

    columns, row_count = _mount_snapshot_file(connection, connection_lock, alias, snapshot_path)
    return LandingResult(columns=columns, row_count=row_count, sha256=snapshot_hash)


def remount_snapshots(
    connection: duckdb.DuckDBPyConnection,
    connection_lock: threading.Lock,
    workspace: SessionWorkspace,
    expected_hashes: dict[str, str],
) -> list[str]:
    """跨 turn 重掛 snapshot 檔案回新連線——connector session 每輪重新
    `open_locked_connection` 後,先前落表的資料需要這一步才能再被查詢到。**只掛
    `expected_hashes` 明確列出的 alias**(不再掃目錄——目錄裡多出的檔案一律視為不可信,
    略過不掛);逐一 `_validate_alias`,檔案缺失或實際 sha256 與 `expected_hashes[alias]`
    不符一律拋 `SnapshotIntegrityError`。回傳實際掛上的 alias 清單,順序沿
    `expected_hashes` 的 dict 順序。
    """
    mounted_aliases: list[str] = []
    for alias, expected_hash in expected_hashes.items():
        _validate_alias(alias)
        snapshot_path = workspace.api_snapshots_dir / f"{alias}.json"
        if not snapshot_path.is_file():
            raise SnapshotIntegrityError(
                f"cannot remount snapshot {alias!r}: expected file {snapshot_path} is "
                "missing — the data needs to be fetched and landed again"
            )
        actual_hash = hashlib.sha256(snapshot_path.read_bytes()).hexdigest()
        if actual_hash != expected_hash:
            raise SnapshotIntegrityError(
                f"snapshot 已被改動(alias={alias!r}):預期 sha256={expected_hash},"
                f"實際 sha256={actual_hash}——檔案在落表後遭覆寫或竄改,拒絕重掛以避免"
                "污染分析,請重新拉取並落表"
            )
        _mount_snapshot_file(connection, connection_lock, alias, snapshot_path)
        mounted_aliases.append(alias)
    return mounted_aliases
