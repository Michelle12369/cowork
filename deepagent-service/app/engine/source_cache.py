"""上傳檔本地 cache。上傳檔 immutable(上傳後永不改寫)→ cache 命中即跳過下載/複製。
s3 模式:raw_path 是 storageKey,下載進 cache。local 模式:raw_path 是 backend 給的共享磁碟
路徑(`.../uploads/{sessionId}/{uuid}_{name}.csv`),複製進 cache——讓 local 模式的檔案
存取路徑與 s3 模式一致,不再對外洩漏 backend 的原始儲存位置。

engine 純度規則:stdlib + boto3,禁止 LLM 框架(ruff TID251)。
"""

import logging
import secrets
import shutil
from collections.abc import Callable
from pathlib import Path

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

_SOURCES_CACHE_DIRNAME = ".sources-cache"
_UPLOADS_SEGMENT = "uploads"


def resolve_source_path(raw_path: str) -> str:
    """回傳可直接餵給 duckdb 的本地路徑,cache 命中時跳過實際傳輸。"""
    settings = get_settings()
    cache_root = Path(settings.AGENT_WORKSPACE_ROOT) / _SOURCES_CACHE_DIRNAME
    if settings.STORAGE_BACKEND == "s3":
        _validate_storage_key(raw_path)
        return _fill_cache(
            cache_root / raw_path,
            lambda partial: _download_from_s3(settings, raw_path, partial),
        )
    return _fill_cache(
        cache_root / _uploads_cache_key(raw_path),
        lambda partial: shutil.copyfile(raw_path, partial),
    )


def _fill_cache(destination: Path, fill: Callable[[Path], None]) -> str:
    if destination.exists():
        return str(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # 先落 temp 再 rename:併發下載/複製互不影響,cache 內永遠只有完整檔案
    partial = destination.with_name(f"{destination.name}.part-{secrets.token_hex(4)}")
    fill(partial)
    partial.replace(destination)
    logger.info("source cached path=%s", destination)
    return str(destination)


def _download_from_s3(settings: Settings, raw_path: str, partial: Path) -> None:
    from app.engine.s3 import build_s3_client

    s3_key = _join_prefix(settings.S3_KEY_PREFIX, raw_path)
    build_s3_client().download_file(settings.S3_BUCKET, s3_key, str(partial))


def _uploads_cache_key(raw_path: str) -> str:
    """從 backend 給的完整磁碟路徑取出快取 key——從最後一個 `uploads` 段開始的子路徑。
    用 Path.parts 逐段比對(不用 substring 比對),避免檔名剛好含 "uploads" 誤判。"""
    parts = Path(raw_path).parts
    last_uploads_index = None
    for index, part in enumerate(parts):
        if part == _UPLOADS_SEGMENT:
            last_uploads_index = index
    if last_uploads_index is None:
        raise ValueError(f"source path missing {_UPLOADS_SEGMENT!r} segment: {raw_path!r}")
    cache_key = Path(*parts[last_uploads_index:]).as_posix()
    # 切出的 key 與 s3 storageKey 同一套驗證——防 `uploads` 之後夾帶 `..` 逃出 cache root。
    _validate_storage_key(cache_key)
    return cache_key


def _validate_storage_key(storage_key: str) -> None:
    key_path = Path(storage_key)
    if key_path.is_absolute() or ".." in key_path.parts or not storage_key:
        raise ValueError(f"unsafe storage key: {storage_key!r}")


def _join_prefix(prefix: str, key: str) -> str:
    """S3 key 前補 bucket 子路徑前綴——只套在 S3 邊界,本地 cache 路徑維持用原始 key。"""
    stripped_prefix = prefix.strip("/")
    return f"{stripped_prefix}/{key}" if stripped_prefix else key
