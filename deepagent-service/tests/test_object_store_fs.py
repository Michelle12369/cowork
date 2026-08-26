"""FilesystemObjectClient——_ObjectClient Protocol 的檔案系統實作,讓 WorkspaceStore
在 local 模式下原封重用。行為對齊 tests/test_workspace_store.py 用的 FakeS3Client 語意。
"""

import os
from pathlib import Path

import pytest

from app.engine.object_store_fs import FilesystemObjectClient

_BUCKET = "local"


def test_upload_then_list_then_download_roundtrip(tmp_path: Path) -> None:
    root = tmp_path / "bucket"
    client = FilesystemObjectClient(root=root)
    source_file = tmp_path / "source.txt"
    source_file.write_text("hello", encoding="utf-8")

    client.upload_file(str(source_file), _BUCKET, "a/b/source.txt")

    paginator = client.get_paginator("list_objects_v2")
    pages = list(paginator.paginate(Bucket=_BUCKET, Prefix="a/"))
    keys = [entry["Key"] for page in pages for entry in page.get("Contents", [])]
    assert keys == ["a/b/source.txt"]

    destination_file = tmp_path / "downloaded.txt"
    client.download_file(_BUCKET, "a/b/source.txt", str(destination_file))
    assert destination_file.read_text(encoding="utf-8") == "hello"


def test_list_objects_v2_prefix_filters_and_sorts(tmp_path: Path) -> None:
    root = tmp_path / "bucket"
    client = FilesystemObjectClient(root=root)
    client.put_object(Bucket=_BUCKET, Key="a/2.txt", Body=b"2")
    client.put_object(Bucket=_BUCKET, Key="a/1.txt", Body=b"1")
    client.put_object(Bucket=_BUCKET, Key="b/3.txt", Body=b"3")

    paginator = client.get_paginator("list_objects_v2")
    pages = list(paginator.paginate(Bucket=_BUCKET, Prefix="a/"))
    keys = [entry["Key"] for page in pages for entry in page.get("Contents", [])]
    assert keys == ["a/1.txt", "a/2.txt"]


def test_list_objects_v2_no_match_yields_page_without_contents(tmp_path: Path) -> None:
    root = tmp_path / "bucket"
    client = FilesystemObjectClient(root=root)

    paginator = client.get_paginator("list_objects_v2")
    pages = list(paginator.paginate(Bucket=_BUCKET, Prefix="nothing/here/"))

    assert all(page.get("Contents", []) == [] for page in pages)


def test_put_object_writes_bytes(tmp_path: Path) -> None:
    root = tmp_path / "bucket"
    client = FilesystemObjectClient(root=root)

    client.put_object(Bucket=_BUCKET, Key="nested/dir/file.bin", Body=b"\x00\x01binary")

    assert (root / "nested" / "dir" / "file.bin").read_bytes() == b"\x00\x01binary"


def test_delete_objects_removes_files_and_prunes_empty_parents(tmp_path: Path) -> None:
    root = tmp_path / "bucket"
    client = FilesystemObjectClient(root=root)
    client.put_object(Bucket=_BUCKET, Key="gen-1/dashboard.html", Body=b"<html></html>")
    client.put_object(Bucket=_BUCKET, Key="gen-1/results/q1.json", Body=b"{}")

    client.delete_objects(
        Bucket=_BUCKET,
        Delete={
            "Objects": [
                {"Key": "gen-1/dashboard.html"},
                {"Key": "gen-1/results/q1.json"},
            ]
        },
    )

    assert not (root / "gen-1").exists()
    assert root.is_dir()  # root 本身不會被清掉


def test_delete_objects_tolerates_missing_key(tmp_path: Path) -> None:
    root = tmp_path / "bucket"
    client = FilesystemObjectClient(root=root)

    client.delete_objects(Bucket=_BUCKET, Delete={"Objects": [{"Key": "never/existed.txt"}]})
    # no exception raised -- delete is idempotent, matching S3 semantics


def test_delete_objects_leaves_non_empty_sibling_directory(tmp_path: Path) -> None:
    root = tmp_path / "bucket"
    client = FilesystemObjectClient(root=root)
    client.put_object(Bucket=_BUCKET, Key="gen-1/dashboard.html", Body=b"<html></html>")
    client.put_object(Bucket=_BUCKET, Key="gen-1/results/q1.json", Body=b"{}")

    client.delete_objects(Bucket=_BUCKET, Delete={"Objects": [{"Key": "gen-1/dashboard.html"}]})

    assert not (root / "gen-1" / "dashboard.html").exists()
    assert (root / "gen-1" / "results" / "q1.json").is_file()


@pytest.mark.parametrize(
    "escaping_key",
    ["../escape.txt", "a/../../escape.txt", "../../etc/passwd"],
)
def test_key_escape_raises_value_error(tmp_path: Path, escaping_key: str) -> None:
    root = tmp_path / "bucket"
    client = FilesystemObjectClient(root=root)

    with pytest.raises(ValueError, match="escapes"):
        client.put_object(Bucket=_BUCKET, Key=escaping_key, Body=b"evil")


def test_download_file_key_escape_raises_value_error(tmp_path: Path) -> None:
    root = tmp_path / "bucket"
    client = FilesystemObjectClient(root=root)
    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("secret", encoding="utf-8")
    destination_file = tmp_path / "downloaded.txt"

    with pytest.raises(ValueError, match="escapes"):
        client.download_file(_BUCKET, "../outside.txt", str(destination_file))


def test_upload_file_mid_write_failure_leaves_no_partial_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """copyfile 寫到暫存檔一半就拋錯 -> 最終 key 路徑不存在、暫存檔也被清掉,不留殘骸
    (原子改名前失敗,destination 從未被觸碰)。"""
    root = tmp_path / "bucket"
    client = FilesystemObjectClient(root=root)
    source_file = tmp_path / "source.txt"
    source_file.write_text("hello world", encoding="utf-8")

    def flaky_copyfile(source: str, destination: str) -> None:
        Path(destination).write_bytes(b"partial")  # 模擬寫到一半
        raise RuntimeError("simulated mid-write failure")

    monkeypatch.setattr("app.engine.object_store_fs.shutil.copyfile", flaky_copyfile)

    with pytest.raises(RuntimeError, match="simulated mid-write failure"):
        client.upload_file(str(source_file), _BUCKET, "a/b/dest.txt")

    destination_path = root / "a" / "b" / "dest.txt"
    assert not destination_path.exists()
    leftover_files = [path for path in root.rglob("*") if path.is_file()]
    assert leftover_files == []


def test_put_object_mid_write_failure_leaves_no_partial_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "bucket"
    client = FilesystemObjectClient(root=root)

    real_write_bytes = Path.write_bytes

    def flaky_write_bytes(self: Path, data: bytes) -> int:
        real_write_bytes(self, b"partial")
        raise RuntimeError("simulated mid-write failure")

    monkeypatch.setattr("app.engine.object_store_fs.Path.write_bytes", flaky_write_bytes)

    with pytest.raises(RuntimeError, match="simulated mid-write failure"):
        client.put_object(Bucket=_BUCKET, Key="gen-1/dashboard.html", Body=b"<html></html>")

    destination_path = root / "gen-1" / "dashboard.html"
    assert not destination_path.exists()
    leftover_files = [path for path in root.rglob("*") if path.is_file()]
    assert leftover_files == []


def test_upload_file_destination_invisible_until_atomic_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """對齊 s3 PUT 的整物件可見性:改名前(半寫階段)destination key 完全不存在,一個併發
    讀方(list/download)不會讀到半寫檔案或壞 zip;改名後內容一次到齊。"""
    root = tmp_path / "bucket"
    client = FilesystemObjectClient(root=root)
    source_file = tmp_path / "source.txt"
    source_file.write_text("hello", encoding="utf-8")
    destination_path = root / "a" / "dest.txt"

    real_replace = os.replace
    observed = {}

    def spying_replace(source: str, destination: str) -> None:
        observed["destination_existed_before_replace"] = Path(destination).exists()
        real_replace(source, destination)

    monkeypatch.setattr("app.engine.object_store_fs.os.replace", spying_replace)

    client.upload_file(str(source_file), _BUCKET, "a/dest.txt")

    assert observed["destination_existed_before_replace"] is False
    assert destination_path.read_text(encoding="utf-8") == "hello"
