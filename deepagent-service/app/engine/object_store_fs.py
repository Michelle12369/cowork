"""檔案系統版物件儲存 client, 滿足 workspace_store 用的 _ObjectClient protocol.
root 目錄扮演 bucket, key 一律映射到 root/key. 只用 stdlib, 不 import LLM 框架."""

import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

_WRITE_ATTEMPTS = 3


def _write_with_parent_retry(destination_path: Path, write_action: Callable[[], object]) -> None:
    """mkdir 父目錄後執行寫入, FileNotFoundError 時重試, 因為併發刪除可能把父目錄清空.
    只用在 download_file, 寫入呼叫端指定的本地路徑, 不需要原子性."""
    for attempt_index in range(_WRITE_ATTEMPTS):
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            write_action()
            return
        except FileNotFoundError:
            if attempt_index == _WRITE_ATTEMPTS - 1:
                raise


def _atomic_write_with_parent_retry(
    destination_path: Path, write_action: Callable[[Path], object]
) -> None:
    """先寫到同目錄的暫存檔, 成功後用 os.replace 原子改名覆蓋過去, 讀方不會看到半寫內容.
    任何失敗都會清掉暫存檔, 不留殘骸."""
    for attempt_index in range(_WRITE_ATTEMPTS):
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            temp_descriptor, temp_name = tempfile.mkstemp(
                dir=destination_path.parent,
                prefix=f".{destination_path.name}.",
                suffix=".tmp",
            )
        except FileNotFoundError:
            if attempt_index == _WRITE_ATTEMPTS - 1:
                raise
            continue
        temp_path = Path(temp_name)
        try:
            os.close(temp_descriptor)
            write_action(temp_path)
            os.replace(temp_path, destination_path)
            return
        except FileNotFoundError:
            temp_path.unlink(missing_ok=True)
            if attempt_index == _WRITE_ATTEMPTS - 1:
                raise
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise


class _FilesystemPaginator:
    """list_objects_v2 的最小相容子集, 一次列完整個 prefix, 回傳單一頁."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def paginate(self, Bucket: str, Prefix: str) -> list[dict[str, Any]]:
        # 只走 prefix 對應的子樹, 不掃整個 root, 根目錄下還有其他與此次列舉無關的檔案.
        # prefix 沒有落在目錄邊界時, 退到其父目錄再用字串過濾.
        directory_part, _, _ = Prefix.rpartition("/")
        scan_base = self._root / directory_part if directory_part else self._root
        if not scan_base.is_dir():
            return [{}]
        matched_keys = sorted(
            path.relative_to(self._root).as_posix()
            for path in scan_base.rglob("*")
            if path.is_file() and path.relative_to(self._root).as_posix().startswith(Prefix)
        )
        if not matched_keys:
            return [{}]
        return [{"Contents": [{"Key": key} for key in matched_keys]}]


class FilesystemObjectClient:
    """本機檔案系統版的物件儲存, 每個 WorkspaceStore 各自建立一個 (見 WorkspaceStore 建構參數)."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def get_paginator(self, operation_name: str) -> _FilesystemPaginator:
        if operation_name != "list_objects_v2":
            raise ValueError(f"unsupported paginator operation: {operation_name!r}")
        return _FilesystemPaginator(self._root)

    def download_file(self, Bucket: str, Key: str, Filename: str) -> None:
        source_path = self._resolve(Key)
        destination_path = Path(Filename)
        _write_with_parent_retry(
            destination_path, lambda: shutil.copyfile(source_path, destination_path)
        )

    def upload_file(self, Filename: str, Bucket: str, Key: str) -> None:
        destination_path = self._resolve(Key)
        _atomic_write_with_parent_retry(
            destination_path, lambda temp_path: shutil.copyfile(Filename, temp_path)
        )

    def put_object(self, *, Bucket: str, Key: str, Body: bytes) -> None:
        destination_path = self._resolve(Key)
        _atomic_write_with_parent_retry(
            destination_path, lambda temp_path: temp_path.write_bytes(Body)
        )

    def delete_objects(self, *, Bucket: str, Delete: dict[str, Any]) -> None:
        for entry in Delete["Objects"]:
            target_path = self._resolve(entry["Key"])
            target_path.unlink(missing_ok=True)
            self._prune_empty_parents(target_path.parent)

    def _resolve(self, key: str) -> Path:
        """key 轉成 root/key, 拒絕任何解析後逃出 root 的路徑."""
        resolved_root = self._root.resolve()
        candidate_path = (self._root / key).resolve()
        if resolved_root not in candidate_path.parents:
            raise ValueError(f"object key escapes root: {key!r}")
        return candidate_path

    def _prune_empty_parents(self, directory: Path) -> None:
        """delete_objects 後由下往上刪空目錄, 停在 root 之前, 非空目錄就停手, 盡力而為."""
        resolved_root = self._root.resolve()
        current_directory = directory.resolve()
        while current_directory != resolved_root and resolved_root in current_directory.parents:
            try:
                current_directory.rmdir()
            except OSError:
                break
            current_directory = current_directory.parent
