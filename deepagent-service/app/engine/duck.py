"""建立 DuckDB 連線並掛載資料: 流程是先把資料表實體化, 再把連線鎖起來, 鎖上以後這個連線
就不能再碰檔案系統或網路."""

import re
from dataclasses import dataclass
from pathlib import Path

import duckdb

# 上傳管線落地的檔案一律是 .csv(xlsx 會在 source_cache 轉檔), 這是目前唯一支援的來源格式.
_READERS = {"csv": "read_csv_auto"}

# 只允許 unicode 字母, 數字, 底線, 禁止雙引號, 分號, 空白這類可能跳脫識別字引號的字元.
_SAFE_IDENTIFIER_PATTERN = re.compile(r"^\w+$", re.UNICODE)

# DuckDB 的 memory_limit 格式, 例如 "2GB", "512MB", "1.5TB".
_MEMORY_LIMIT_PATTERN = re.compile(r"^\d+(?:\.\d+)?\s*(?:KB|MB|GB|TB)$", re.IGNORECASE)


def _validate_alias(alias: str) -> None:
    """確保 alias 是安全的 SQL 識別字, 避免被注入進 CREATE TABLE 的 DDL 裡."""
    if not _SAFE_IDENTIFIER_PATTERN.fullmatch(alias):
        raise ValueError(f"unsafe source alias: {alias!r}")


def _validate_memory_limit(memory_limit: str) -> None:
    """確保 memory_limit 符合 DuckDB 接受的大小格式, 避免被注入進 SET 陳述式."""
    if not _MEMORY_LIMIT_PATTERN.fullmatch(memory_limit):
        raise ValueError(f"invalid memory_limit: {memory_limit!r}")


@dataclass(frozen=True)
class Source:
    alias: str
    path: str  # 本地掛載路徑, 由 Java 端的 resolveSourcePath 組出
    file_type: str


def open_locked_connection(
    sources: list[Source],
    memory_limit: str = "2GB",
    allowed_directories: list[str] | None = None,
) -> duckdb.DuckDBPyConnection:
    """先把資料掛好(materialize), 再把連線鎖起來: 回傳的連線上執行任何 SQL 都不能再碰檔案系統
    或網路, 唯一例外是 allowed_directories 這個白名單.

    這個白名單的洞是雙向的, 讀跟寫都通得過: 鎖門之後, 模型透過 run_sql 執行的任意 SQL 一樣能對
    allowed_directories 目錄下用 COPY TO, ATTACH, EXPORT DATABASE 寫入東西. 這個模組不做
    語句層級的過濾, 但那個目錄下的檔案只是這一輪的暫存內容(見 app.engine.api_snapshot), 一輪
    結束就整個刪掉, 不會跨輪存活, 所以不需要額外做完整性驗證.
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
