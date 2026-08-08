"""檔案系統版物件儲存 client——滿足 workspace_store._ObjectClient Protocol,讓 WorkspaceStore
在 STORAGE_BACKEND=local 時原封重用(而非另建一套 local-only 邏輯)。root 目錄扮演 bucket
角色,key 一律映射到 root/key。

engine 純度規則:stdlib only,禁止 LLM 框架(ruff TID251)。
"""

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

_WRITE_ATTEMPTS = 3


def _write_with_parent_retry(destination_path: Path, write_action: Callable[[], object]) -> None:
    """mkdir 父目錄後執行寫入,FileNotFoundError 時重試——併發的 delete_objects 空目錄修剪
    可能在 mkdir 與實際寫入之間把父目錄刪掉(真 S3 無目錄概念,無此 race),重建再寫即可。"""
    for attempt_index in range(_WRITE_ATTEMPTS):
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            write_action()
            return
        except FileNotFoundError:
            if attempt_index == _WRITE_ATTEMPTS - 1:
                raise


class _FilesystemPaginator:
    """list_objects_v2 的最小相容子集——一次列完整個 prefix,回傳單一頁。"""

    def __init__(self, root: Path) -> None:
        self._root = root

    def paginate(self, Bucket: str, Prefix: str) -> list[dict[str, Any]]:
        # 只走 prefix 對應的子樹,不掃整個 root——bucket 根目錄下還有 .turns/.sources-cache
        # 等與本次列舉無關的大樹。prefix 未落在目錄邊界時退到其父目錄再以字串過濾。
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
    """non-bean: instantiate per WorkspaceStore(見 WorkspaceStore 建構參數)。"""

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
        _write_with_parent_retry(
            destination_path, lambda: shutil.copyfile(Filename, destination_path)
        )

    def put_object(self, *, Bucket: str, Key: str, Body: bytes) -> None:
        destination_path = self._resolve(Key)
        _write_with_parent_retry(destination_path, lambda: destination_path.write_bytes(Body))

    def delete_objects(self, *, Bucket: str, Delete: dict[str, Any]) -> None:
        for entry in Delete["Objects"]:
            target_path = self._resolve(entry["Key"])
            target_path.unlink(missing_ok=True)
            self._prune_empty_parents(target_path.parent)

    def _resolve(self, key: str) -> Path:
        """key -> root/key,拒絕任何解析後逃出 root 的路徑(對齊 workspace_store._pull 的做法)。"""
        resolved_root = self._root.resolve()
        candidate_path = (self._root / key).resolve()
        if resolved_root not in candidate_path.parents:
            raise ValueError(f"object key escapes root: {key!r}")
        return candidate_path

    def _prune_empty_parents(self, directory: Path) -> None:
        """delete_objects 後由下往上刪空目錄,停在(不含)root——best-effort,非空即停。"""
        resolved_root = self._root.resolve()
        current_directory = directory.resolve()
        while current_directory != resolved_root and resolved_root in current_directory.parents:
            try:
                current_directory.rmdir()
            except OSError:
                break
            current_directory = current_directory.parent
