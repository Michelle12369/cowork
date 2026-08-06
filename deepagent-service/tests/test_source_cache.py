"""resolve_source_path:local 模式原樣回傳、s3 模式下載到 immutable cache。

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


# 1. local 模式:raw_path 原樣回傳,零 S3 呼叫
def test_local_mode_returns_raw_path_unchanged_without_s3_calls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    get_settings.cache_clear()

    result = resolve_source_path("/shared/disk/uploads/file.csv")

    assert result == "/shared/disk/uploads/file.csv"


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
