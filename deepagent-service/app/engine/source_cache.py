"""這裡是上傳檔案的本地 cache. 上傳檔案是不可變的, 上傳後永遠不會被改寫, 所以 cache 命中時就
可以直接跳過下載或複製. s3 模式下 raw_path 是 storageKey, 下載進 cache; local 模式下 raw_path
是 backend 給的共享磁碟路徑(像 .../uploads/{sessionId}/{uuid}_{name}.csv), 複製進 cache,
讓 local 模式的檔案存取路徑跟 s3 模式一致, 不再對外洩漏 backend 的原始儲存位置.

這是 engine 層, 只能用 stdlib 加 boto3, 不能 import 任何 LLM 框架(ruff 的 TID251 規則會擋下來).
"""

import logging
import secrets
import shutil
from collections.abc import Callable
from pathlib import Path

from app.config import Settings, get_settings
from app.engine.upload_decrypt import decrypt_upload
from app.engine.xlsx_to_csv import convert_xlsx_to_csv

logger = logging.getLogger(__name__)

_SOURCES_CACHE_DIRNAME = ".sources-cache"
_UPLOADS_SEGMENT = "uploads"
_XLSX_SUFFIX = ".xlsx"
_CSV_SUFFIX = ".csv"

# resolved path 的副檔名對應到 duckdb 的 reader 型別. 這裡不收 xlsx, 因為 xlsx 一定會在
# 落地前轉成 .csv, resolved path 出現 .xlsx 本身就代表有 bug.
_RESOLVED_FILE_TYPES = {".csv": "csv"}


def resolved_file_type(resolved_path: str) -> str:
    """從 resolve_source_path 回傳的路徑推斷 duckdb 要用的 file_type. wire 上的 fileType
    描述的是原始儲存檔, 也就是轉檔前的 xlsx, 跟轉檔後的 resolved path 不一致, 一定要以這個
    函式的推斷結果為準, 不能直接沿用 wire 上的值."""
    suffix = Path(resolved_path).suffix.lower()
    file_type = _RESOLVED_FILE_TYPES.get(suffix)
    if file_type is None:
        raise ValueError(f"unsupported source extension: {suffix!r}")
    return file_type


def resolve_source_path(raw_path: str) -> str:
    """回傳一個可以直接餵給 duckdb 的本地路徑, cache 命中時就跳過實際的下載或複製. .xlsx 來源
    (上傳後原樣以密文儲存)在 cache 裡一律換成 .csv 檔名, 第一次落地時要多跑一段下載或複製
    密文, 再解密, 再轉檔的管線, cache 命中的話這整段就跳過."""
    settings = get_settings()
    cache_root = Path(settings.AGENT_WORKSPACE_ROOT) / _SOURCES_CACHE_DIRNAME
    if settings.STORAGE_BACKEND == "s3":
        _validate_storage_key(raw_path)
        # 這裡跟 backend 的 FileService.RAW_STORED_TYPES 互為鏡像, 那份清單加新型別時這裡的
        # 推斷邏輯就會失效, 要改成用 per-file metadata(細節看 spec). 比對不分大小寫: Java 端
        # 是把字串轉小寫後判斷型別, 但 key 本身保留原本的大小寫(例如 Data.XLSX), 這裡也要
        # 同樣容忍這種情況.
        if raw_path.lower().endswith(_XLSX_SUFFIX):
            return _fill_cache(
                cache_root / _with_csv_suffix(raw_path),
                lambda partial: _fill_xlsx_cache(
                    partial,
                    lambda cipher_tmp: _download_from_s3(settings, raw_path, cipher_tmp),
                ),
            )
        return _fill_cache(
            cache_root / raw_path,
            lambda partial: _download_from_s3(settings, raw_path, partial),
        )
    uploads_key = _uploads_cache_key(raw_path)
    # 這裡跟 backend 的 FileService.RAW_STORED_TYPES 互為鏡像, 那份清單加新型別時這裡的
    # 推斷邏輯就會失效, 要改成用 per-file metadata(細節看 spec). 比對不分大小寫: Java 端
    # 是把字串轉小寫後判斷型別, 但 key 本身保留原本的大小寫(例如 Data.XLSX), 這裡也要
    # 同樣容忍這種情況.
    if uploads_key.lower().endswith(_XLSX_SUFFIX):
        return _fill_cache(
            cache_root / _with_csv_suffix(uploads_key),
            lambda partial: _fill_xlsx_cache(
                partial,
                lambda cipher_tmp: shutil.copyfile(raw_path, cipher_tmp),
            ),
        )
    return _fill_cache(
        cache_root / uploads_key,
        lambda partial: shutil.copyfile(raw_path, partial),
    )


def _with_csv_suffix(key: str) -> str:
    return key[: -len(_XLSX_SUFFIX)] + _CSV_SUFFIX


def _fill_xlsx_cache(partial: Path, fetch_ciphertext: Callable[[Path], None]) -> None:
    """流程是先把密文存到 partial 旁邊的暫存檔, 解密到另一個暫存檔, 再轉檔進 partial. 不管
    成功或失敗, 兩個暫存檔都會在 finally 裡清掉; partial 本身的 temp 加 rename 原子性由呼叫端
    _fill_cache 負責. plain_tmp 的檔名一定要以 .xlsx 結尾, 因為 openpyxl 是看副檔名而不是看
    內容來判斷格式支不支援."""
    cipher_tmp = partial.with_name(partial.name + ".cipher")
    plain_tmp = partial.with_name(partial.name + ".plain.xlsx")
    try:
        fetch_ciphertext(cipher_tmp)
        decrypt_upload(cipher_tmp, plain_tmp)
        convert_xlsx_to_csv(plain_tmp, partial)
    finally:
        cipher_tmp.unlink(missing_ok=True)
        plain_tmp.unlink(missing_ok=True)


def _fill_cache(destination: Path, fill: Callable[[Path], None]) -> str:
    if destination.exists():
        return str(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # 先寫進暫存檔再 rename: 併發的下載或複製彼此不會互相影響, cache 裡永遠只會出現完整的檔案.
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
    """從 backend 給的完整磁碟路徑取出 cache key, 也就是從最後一個 uploads 這段開始算起的
    子路徑. 用 Path.parts 逐段比對, 不用字串子字串比對, 避免檔名裡剛好含有 "uploads" 造成
    誤判."""
    parts = Path(raw_path).parts
    last_uploads_index = None
    for index, part in enumerate(parts):
        if part == _UPLOADS_SEGMENT:
            last_uploads_index = index
    if last_uploads_index is None:
        raise ValueError(f"source path missing {_UPLOADS_SEGMENT!r} segment: {raw_path!r}")
    cache_key = Path(*parts[last_uploads_index:]).as_posix()
    # 切出來的 key 跟 s3 的 storageKey 用同一套驗證, 防止 uploads 之後夾帶 .. 逃出 cache root.
    _validate_storage_key(cache_key)
    return cache_key


def _validate_storage_key(storage_key: str) -> None:
    key_path = Path(storage_key)
    if key_path.is_absolute() or ".." in key_path.parts or not storage_key:
        raise ValueError(f"unsafe storage key: {storage_key!r}")


def _join_prefix(prefix: str, key: str) -> str:
    """在 S3 key 前面補上 bucket 的子路徑前綴, 這個處理只套用在 S3 這一側, 本地 cache 路徑
    仍然用原始的 key."""
    stripped_prefix = prefix.strip("/")
    return f"{stripped_prefix}/{key}" if stripped_prefix else key
