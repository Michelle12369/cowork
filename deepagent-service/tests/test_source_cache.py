"""resolve_source_path:s3 模式下載、local 模式從共享磁碟複製,同樣落到 immutable cache。

stub S3 client 記錄 download_file 呼叫次數/參數；monkeypatch 目標是
`app.engine.s3.build_s3_client`(source_cache 內是函式內延遲 import,patch 模組屬性即可)。
"""

from pathlib import Path

import pytest

from app.config import get_settings
from app.engine.source_cache import resolve_source_path


class _FakeS3Client:
    def __init__(self) -> None:
        self.download_calls: list[tuple[str, str, str]] = []

    def download_file(self, bucket: str, key: str, filename: str) -> None:
        self.download_calls.append((bucket, key, filename))
        Path(filename).write_bytes(b"fake source content")


def _install_fake_s3_client(monkeypatch: pytest.MonkeyPatch) -> _FakeS3Client:
    fake_client = _FakeS3Client()
    monkeypatch.setattr("app.engine.s3.build_s3_client", lambda: fake_client)
    return fake_client


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
