"""resolve_source_path:s3 模式下載、local 模式從共享磁碟複製,同樣落到 immutable cache。

stub S3 client 記錄 download_file 呼叫次數/參數；monkeypatch 目標是
`app.engine.s3.build_s3_client`(source_cache 內是函式內延遲 import,patch 模組屬性即可)。
"""

from contextlib import contextmanager
from pathlib import Path

import openpyxl
import pytest

from app.config import get_settings
from app.engine.request_context import reset_request_identity, set_request_identity
from app.engine.source_cache import resolve_source_path, resolved_file_type


@contextmanager
def _request_identity():
    # xlsx 分支經 decrypt_upload 呼叫 require_user_id()——非 xlsx 測試不需要這個 context。
    tokens = set_request_identity("user-1", "session-1")
    try:
        yield
    finally:
        reset_request_identity(tokens)


class _FakeS3Client:
    def __init__(self, content: bytes = b"fake source content") -> None:
        self.content = content
        self.download_calls: list[tuple[str, str, str]] = []

    def download_file(self, bucket: str, key: str, filename: str) -> None:
        self.download_calls.append((bucket, key, filename))
        Path(filename).write_bytes(self.content)


def _install_fake_s3_client(
    monkeypatch: pytest.MonkeyPatch, content: bytes = b"fake source content"
) -> _FakeS3Client:
    fake_client = _FakeS3Client(content)
    monkeypatch.setattr("app.engine.s3.build_s3_client", lambda: fake_client)
    return fake_client


def _write_minimal_xlsx(path: Path, rows: list[list[object]]) -> None:
    """同 tests/test_xlsx_to_csv.py 的 `_write_xlsx` 手法:最小 openpyxl fixture。"""
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    workbook.save(path)


# 1. local 模式:複製進 .sources-cache/uploads/...,回傳 cache 內路徑
def test_local_mode_copies_into_uploads_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "ws"))
    get_settings.cache_clear()
    source_file = tmp_path / "backend-data" / "uploads" / "session-1" / "uuid_file.csv"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("system\nCRM\n", encoding="utf-8")

    result = resolve_source_path(str(source_file))

    expected_path = tmp_path / "ws" / ".sources-cache" / "uploads" / "session-1" / "uuid_file.csv"
    assert result == str(expected_path)
    assert expected_path.read_text(encoding="utf-8") == "system\nCRM\n"


# 1b. local 模式 cache 命中:第二次呼叫不再讀原始檔(改寫/刪除原始檔後,回傳的仍是快取內容)
def test_local_mode_cache_hit_skips_reading_origin_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "ws"))
    get_settings.cache_clear()
    source_file = tmp_path / "backend-data" / "uploads" / "session-1" / "uuid_file.csv"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("original", encoding="utf-8")

    first_result = resolve_source_path(str(source_file))
    source_file.unlink()  # 上傳檔 immutable 前提下,cache 命中不該再碰原始檔
    second_result = resolve_source_path(str(source_file))

    assert first_result == second_result
    assert Path(second_result).read_text(encoding="utf-8") == "original"


# 1c. local 模式:raw_path 沒有 uploads 段 -> ValueError(違反 backend 給路徑的約定)
def test_local_mode_missing_uploads_segment_raises_value_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "ws"))
    get_settings.cache_clear()

    with pytest.raises(ValueError, match="uploads"):
        resolve_source_path(str(tmp_path / "backend-data" / "session-1" / "file.csv"))


# 1d. local 模式:檔名本身含 "uploads" 字樣不誤判為路徑段(用最後一個真正的 uploads 段)
def test_local_mode_filename_containing_uploads_word_does_not_confuse_key_lookup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "ws"))
    get_settings.cache_clear()
    source_file = tmp_path / "backend-data" / "uploads" / "session-1" / "my_uploads_report.csv"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("data", encoding="utf-8")

    result = resolve_source_path(str(source_file))

    expected_path = (
        tmp_path / "ws" / ".sources-cache" / "uploads" / "session-1" / "my_uploads_report.csv"
    )
    assert result == str(expected_path)


# 2. s3 模式:下載後回傳 cache 內的本地路徑
def test_s3_mode_downloads_and_returns_cache_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("S3_BUCKET", "erd-cowork-test")
    get_settings.cache_clear()
    fake_client = _install_fake_s3_client(monkeypatch)

    result = resolve_source_path("uploads/session-1/file.csv")

    expected_path = tmp_path / ".sources-cache" / "uploads" / "session-1" / "file.csv"
    assert result == str(expected_path)
    assert expected_path.is_file()
    assert expected_path.read_bytes() == b"fake source content"
    assert len(fake_client.download_calls) == 1
    bucket, key, _filename = fake_client.download_calls[0]
    assert bucket == "erd-cowork-test"
    assert key == "uploads/session-1/file.csv"


# 3. cache 命中:第二次呼叫跳過下載
def test_s3_mode_cache_hit_skips_download(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("S3_BUCKET", "erd-cowork-test")
    get_settings.cache_clear()
    fake_client = _install_fake_s3_client(monkeypatch)

    first_result = resolve_source_path("uploads/session-1/file.csv")
    second_result = resolve_source_path("uploads/session-1/file.csv")

    assert first_result == second_result
    assert len(fake_client.download_calls) == 1


# 4. ".."/絕對路徑 key -> ValueError
@pytest.mark.parametrize(
    "unsafe_key",
    ["../../etc/passwd", "/etc/passwd", "uploads/../../secret", ""],
)
def test_s3_mode_unsafe_storage_key_raises_value_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, unsafe_key: str
) -> None:
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path))
    get_settings.cache_clear()
    _install_fake_s3_client(monkeypatch)

    with pytest.raises(ValueError, match="unsafe storage key"):
        resolve_source_path(unsafe_key)


# 5b. S3_KEY_PREFIX 非空:下載用的 S3 Key 補前綴,但本地 cache 路徑仍用 raw_path(不含前綴)
def test_resolve_source_path_key_prefix_downloads_from_prefixed_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("S3_BUCKET", "erd-cowork-test")
    monkeypatch.setenv("S3_KEY_PREFIX", "erd-cowork")
    get_settings.cache_clear()
    fake_client = _install_fake_s3_client(monkeypatch)

    result = resolve_source_path("uploads/sess-1/uuid_a.csv")

    expected_path = tmp_path / ".sources-cache" / "uploads" / "sess-1" / "uuid_a.csv"
    assert result == str(expected_path)
    _bucket, key, _filename = fake_client.download_calls[0]
    assert key == "erd-cowork/uploads/sess-1/uuid_a.csv"


# 5c. S3_KEY_PREFIX 空:下載 Key 不含額外前綴(既有行為零變化)
def test_resolve_source_path_key_prefix_empty_uses_key_verbatim(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("S3_BUCKET", "erd-cowork-test")
    monkeypatch.setenv("S3_KEY_PREFIX", "")
    get_settings.cache_clear()
    fake_client = _install_fake_s3_client(monkeypatch)

    resolve_source_path("uploads/sess-1/uuid_a.csv")

    _bucket, key, _filename = fake_client.download_calls[0]
    assert key == "uploads/sess-1/uuid_a.csv"


# 5. 下載中殘留的 .part-* 檔不被當成 cache 命中,仍會觸發下載
def test_s3_mode_stale_partial_file_does_not_count_as_cache_hit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("S3_BUCKET", "erd-cowork-test")
    get_settings.cache_clear()
    fake_client = _install_fake_s3_client(monkeypatch)
    stale_partial = tmp_path / ".sources-cache" / "uploads" / "session-1" / "file.csv.part-deadbeef"
    stale_partial.parent.mkdir(parents=True)
    stale_partial.write_bytes(b"incomplete leftover")

    result = resolve_source_path("uploads/session-1/file.csv")

    expected_path = tmp_path / ".sources-cache" / "uploads" / "session-1" / "file.csv"
    assert result == str(expected_path)
    assert expected_path.read_bytes() == b"fake source content"
    assert len(fake_client.download_calls) == 1


# 6. local 模式 .xlsx:複製密文→identity 解密→轉檔,cache 內落地 .csv
def test_resolve_xlsx_local_decrypts_converts_and_caches_csv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "ws"))
    get_settings.cache_clear()
    source_dir = tmp_path / "backend-data" / "uploads" / "sess-1"
    source_dir.mkdir(parents=True)
    xlsx_path = source_dir / "u1_data.xlsx"
    _write_minimal_xlsx(xlsx_path, [["col"], ["value"]])

    with _request_identity():
        resolved = resolve_source_path(str(xlsx_path))

    expected_path = tmp_path / "ws" / ".sources-cache" / "uploads" / "sess-1" / "u1_data.csv"
    assert resolved == str(expected_path)
    assert Path(resolved).read_text(encoding="utf-8").splitlines() == ["col", "value"]
    # 管線用完即清:沒有殘留的 .cipher/.plain.xlsx 暫存檔留在 cache 目錄
    leftover = list(expected_path.parent.glob("*.cipher")) + list(
        expected_path.parent.glob("*.plain.xlsx")
    )
    assert leftover == []


# 6b. local 模式 .xlsx 大小寫混合副檔名(如 `.XLSX`):Java 端小寫化判型別但 key 保留原
# 大小寫,此處比對 MUST 大小寫不敏感,否則落回 plaintext-copy 路線把密文原樣端進 duckdb。
def test_resolve_xlsx_local_uppercase_extension_decrypts_converts_and_caches_csv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "ws"))
    get_settings.cache_clear()
    source_dir = tmp_path / "backend-data" / "uploads" / "sess-1"
    source_dir.mkdir(parents=True)
    xlsx_path = source_dir / "u1_Data.XLSX"
    _write_minimal_xlsx(xlsx_path, [["col"], ["value"]])

    with _request_identity():
        resolved = resolve_source_path(str(xlsx_path))

    expected_path = tmp_path / "ws" / ".sources-cache" / "uploads" / "sess-1" / "u1_Data.csv"
    assert resolved == str(expected_path)
    assert Path(resolved).read_text(encoding="utf-8").splitlines() == ["col", "value"]
    # 管線用完即清:沒有殘留的 .cipher/.plain.xlsx 暫存檔留在 cache 目錄
    leftover = list(expected_path.parent.glob("*.cipher")) + list(
        expected_path.parent.glob("*.plain.xlsx")
    )
    assert leftover == []


# 7. local 模式 .xlsx cache 命中:第二次呼叫不重跑解密/轉檔管線
def test_resolve_xlsx_cache_hit_skips_pipeline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "ws"))
    get_settings.cache_clear()
    source_dir = tmp_path / "backend-data" / "uploads" / "sess-1"
    source_dir.mkdir(parents=True)
    xlsx_path = source_dir / "u1_data.xlsx"
    _write_minimal_xlsx(xlsx_path, [["col"], ["value"]])

    with _request_identity():
        first_result = resolve_source_path(str(xlsx_path))
        Path(first_result).write_text("cache-marker", encoding="utf-8")
        xlsx_path.unlink()  # 上傳檔 immutable 前提下,cache 命中不該再碰原始檔/重跑管線
        second_result = resolve_source_path(str(xlsx_path))

    assert first_result == second_result
    assert Path(second_result).read_text(encoding="utf-8") == "cache-marker"


# 8. s3 模式 .xlsx:下載密文→identity 解密→轉檔,cache 內落地 .csv
def test_resolve_xlsx_s3_downloads_decrypts_converts_and_caches_csv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("S3_BUCKET", "erd-cowork-test")
    get_settings.cache_clear()
    xlsx_source = tmp_path / "source.xlsx"
    _write_minimal_xlsx(xlsx_source, [["col"], ["value"]])
    fake_client = _install_fake_s3_client(monkeypatch, content=xlsx_source.read_bytes())

    with _request_identity():
        result = resolve_source_path("uploads/session-1/file.xlsx")

    expected_path = tmp_path / ".sources-cache" / "uploads" / "session-1" / "file.csv"
    assert result == str(expected_path)
    assert expected_path.read_text(encoding="utf-8").splitlines() == ["col", "value"]
    assert len(fake_client.download_calls) == 1
    _bucket, key, _filename = fake_client.download_calls[0]
    assert key == "uploads/session-1/file.xlsx"


# 9. .csv 來源行為零變化(釘既有路線不受 .xlsx 分流影響)
def test_resolve_csv_path_behaviour_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "ws"))
    get_settings.cache_clear()
    source_file = tmp_path / "backend-data" / "uploads" / "session-1" / "uuid_file.csv"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("system\nCRM\n", encoding="utf-8")

    result = resolve_source_path(str(source_file))

    expected_path = tmp_path / "ws" / ".sources-cache" / "uploads" / "session-1" / "uuid_file.csv"
    assert result == str(expected_path)
    assert expected_path.read_text(encoding="utf-8") == "system\nCRM\n"


# 10. resolved_file_type:由 resolved path 副檔名推斷 duckdb file_type,大小寫不敏感;
# xlsx 與未知副檔名一律 fail loud——resolved path 出現 .xlsx 本身就是 bug(理應已轉 .csv)。
@pytest.mark.parametrize(
    ("resolved_path", "expected_type"),
    [
        ("/cache/uploads/sess-1/file.csv", "csv"),
        ("/cache/uploads/sess-1/file.CSV", "csv"),
        ("/cache/uploads/sess-1/file.parquet", "parquet"),
        ("/cache/uploads/sess-1/file.Parquet", "parquet"),
    ],
)
def test_resolved_file_type_maps_known_extensions(resolved_path: str, expected_type: str) -> None:
    assert resolved_file_type(resolved_path) == expected_type


@pytest.mark.parametrize(
    "resolved_path",
    ["/cache/uploads/sess-1/file.xlsx", "/cache/uploads/sess-1/file.XLSX"],
)
def test_resolved_file_type_rejects_xlsx(resolved_path: str) -> None:
    with pytest.raises(ValueError, match="unsupported source extension"):
        resolved_file_type(resolved_path)


def test_resolved_file_type_rejects_unknown_extension() -> None:
    with pytest.raises(ValueError, match="unsupported source extension"):
        resolved_file_type("/cache/uploads/sess-1/file.json")
