"""DuckDB 連線建立與資料掛載——先 materialize 資料表、後鎖門，鎖門後連線無法再碰檔案系統/網路。"""

import re
from dataclasses import dataclass
from pathlib import Path

import duckdb

_READERS = {"csv": "read_csv_auto", "parquet": "read_parquet"}

# 只允許 unicode 字母/數字/底線,禁止雙引號、分號、空白等可脫離識別字引號的字元。
_SAFE_IDENTIFIER_PATTERN = re.compile(r"^\w+$", re.UNICODE)

# DuckDB memory_limit 格式,例如 "2GB"、"512MB"、"1.5TB"。
_MEMORY_LIMIT_PATTERN = re.compile(r"^\d+(?:\.\d+)?\s*(?:KB|MB|GB|TB)$", re.IGNORECASE)


def _validate_alias(alias: str) -> None:
    """確保 alias 是安全的 SQL 識別字,避免注入進 CREATE TABLE DDL。"""
    if not _SAFE_IDENTIFIER_PATTERN.fullmatch(alias):
        raise ValueError(f"unsafe source alias: {alias!r}")


def _validate_memory_limit(memory_limit: str) -> None:
    """確保 memory_limit 符合 DuckDB 接受的大小格式,避免注入進 SET 陳述式。"""
    if not _MEMORY_LIMIT_PATTERN.fullmatch(memory_limit):
        raise ValueError(f"invalid memory_limit: {memory_limit!r}")


@dataclass(frozen=True)
class Source:
    alias: str
    path: str  # 本地掛載路徑(由 Java 端 resolveSourcePath 組出)
    file_type: str


def open_locked_connection(
    sources: list[Source],
    memory_limit: str = "2GB",
    allowed_directories: list[str] | None = None,
) -> duckdb.DuckDBPyConnection:
    """先掛資料(materialize)、後鎖門——回傳的連線上任何 SQL 都無法再碰檔案系統/網路,
    唯一例外是 `allowed_directories` 白名單(見下)。資料源一律為本地掛載路徑(PVC),
    不載入任何網路 extension。

    `allowed_directories`(connector session 專用,檔案 session 不傳/傳 None):非 None 時
    額外 `SET allowed_directories = [...]`(路徑先經 `Path.resolve()` 正規化)——DuckDB 的
    `allowed_directories` 語意是「即使 enable_external_access=false 仍放行的例外目錄」
    (見 `duckdb_settings()` 對該設定的官方描述),因此兩個分支都會執行
    `enable_external_access=false`,差異只在於是否額外加這道白名單洞——connector session
    落表後(`api_snapshot.land_snapshot`)還需要在鎖門後讀取新寫入的 snapshot 檔案,
    白名單洞就是唯一還開著的入口;檔案 session(None)沒有這個需求,現行行為 byte 不變。
    """
    _validate_memory_limit(memory_limit)
    config: dict[str, object] = {"memory_limit": memory_limit, "threads": 2}
    connection = duckdb.connect(":memory:", config=config)
    for source in sources:
        reader = _READERS.get(source.file_type)
        if reader is None:
            raise ValueError(f"unsupported file type: {source.file_type}")
        _validate_alias(source.alias)
        connection.execute(
            f'CREATE TABLE "{source.alias}" AS SELECT * FROM {reader}(?)', [source.path]
        )
    if allowed_directories is not None:
        resolved_directories = [str(Path(directory).resolve()) for directory in allowed_directories]
        connection.execute("SET allowed_directories = ?", [resolved_directories])
    connection.execute("SET enable_external_access = false")
    connection.execute("SET lock_configuration = true")
    return connection
