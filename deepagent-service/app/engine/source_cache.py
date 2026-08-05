"""s3 模式的上傳檔本地 cache。上傳檔 immutable(上傳後永不改寫)→ cache 命中即跳過下載。

engine 純度規則:stdlib + boto3,禁止 LLM 框架(ruff TID251)。
"""

import logging
import secrets
from pathlib import Path

from app.config import get_settings

logger = logging.getLogger(__name__)

_SOURCES_CACHE_DIRNAME = ".sources-cache"


def resolve_source_path(raw_path: str) -> str:
    """local 模式:raw_path 即共享磁碟路徑,原樣回傳。s3 模式:raw_path 是 storageKey,
    下載到 {AGENT_WORKSPACE_ROOT}/.sources-cache/{storageKey} 後回傳本地路徑。"""
    settings = get_settings()
    if settings.STORAGE_BACKEND != "s3":
        return raw_path
    _validate_storage_key(raw_path)
    cache_root = Path(settings.AGENT_WORKSPACE_ROOT) / _SOURCES_CACHE_DIRNAME
    destination = cache_root / raw_path
    if destination.exists():
        return str(destination)
    from app.engine.s3 import build_s3_client

    destination.parent.mkdir(parents=True, exist_ok=True)
    # 先落 temp 再 rename:併發下載互不影響,cache 內永遠只有完整檔案
    partial = destination.with_name(f"{destination.name}.part-{secrets.token_hex(4)}")
    build_s3_client().download_file(settings.S3_BUCKET, raw_path, str(partial))
    partial.replace(destination)
    logger.info("source cached key=%s", raw_path)
    return str(destination)


def _validate_storage_key(storage_key: str) -> None:
    key_path = Path(storage_key)
    if key_path.is_absolute() or ".." in key_path.parts or not storage_key:
        raise ValueError(f"unsafe storage key: {storage_key!r}")
