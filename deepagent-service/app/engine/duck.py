"""DuckDB 連線建立與資料掛載——先 materialize 資料表、後鎖門，鎖門後連線無法再碰檔案系統/網路。"""

import os
import re
from dataclasses import dataclass

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
    path: str  # 本地路徑或 s3://bucket/key(MinIO/S3;由 Java 端依 erd.storage.type 決定)
    file_type: str


def _s3_config() -> dict[str, object]:
    """MinIO/S3 設定(httpfs extension)。走 connect(config=...) dict 而非 SET 陳述式——
    值不進 SQL 字串。s3_url_style=path 為 MinIO 需要。"""
    return {
        "s3_endpoint": os.environ["AGENT_S3_ENDPOINT"],
        "s3_access_key_id": os.environ["AGENT_S3_ACCESS_KEY_ID"],
        "s3_secret_access_key": os.environ["AGENT_S3_SECRET_ACCESS_KEY"],
        "s3_region": os.environ.get("AGENT_S3_REGION", "us-east-1"),
        "s3_url_style": "path",
        "s3_use_ssl": os.environ.get("AGENT_S3_USE_SSL", "false").lower() == "true",
    }


def open_locked_connection(
    sources: list[Source], memory_limit: str = "2GB"
) -> duckdb.DuckDBPyConnection:
    """先掛資料(materialize)、後鎖門——回傳的連線上任何 SQL 都無法再碰檔案系統/網路。
    materialize 發生在鎖門前,鎖門後連 httpfs 也被 enable_external_access=false 封住。"""
    _validate_memory_limit(memory_limit)
    has_s3_source = any(source.path.startswith("s3://") for source in sources)
    config: dict[str, object] = {"memory_limit": memory_limit, "threads": 2}
    if has_s3_source:
        config.update(_s3_config())
    connection = duckdb.connect(":memory:", config=config)
    if has_s3_source:
        connection.execute("INSTALL httpfs; LOAD httpfs;")
    for source in sources:
        reader = _READERS.get(source.file_type)
        if reader is None:
            raise ValueError(f"unsupported file type: {source.file_type}")
        _validate_alias(source.alias)
        connection.execute(
            f'CREATE TABLE "{source.alias}" AS SELECT * FROM {reader}(?)', [source.path]
        )
    connection.execute("SET enable_external_access = false")
    connection.execute("SET lock_configuration = true")
    return connection
