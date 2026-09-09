"""每一輪把 workspace 打包成 zip, 用新的 generation key 推上物件儲存 (S3 或本機檔案).
物件不覆寫, 讀方拿到的永遠是完整的一代. 本輪的 scratch 目錄在 persist 後刪除."""

import logging
import re
import secrets
import shutil
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any, Protocol

# 這裡直接 import 是為了不動到既有呼叫點; 這個檔案跟 workspace.py 同屬 engine 層, 視為
# 同一個模組群組內部共用的東西.
from app.config import get_settings
from app.engine.workspace import (
    SessionWorkspace,
    WorkspacePersistError,
    prepare_local_layout,
    resolve_workspace_root,
)

logger = logging.getLogger(__name__)

# 一個 zip 代就是 session 前綴下的單一物件全名, 例如 "gen-1723107600123-abcd1234.zip".
_ZIP_GENERATION_PATTERN = re.compile(r"^gen-(\d{13})-([0-9a-f]{8})\.zip$")
_KEPT_GENERATIONS = 2
_PERSIST_ATTEMPTS = 3
# 這個值一定要跟 backend 的 S3WorkspacePurger.WORKSPACE_PREFIX 一致, 兩邊都寫死同一個值,
# 不做成可設定項, 避免各自改動之後 backend 清不到 deepagent 實際寫入的前綴.
WORKSPACE_PREFIX = "workspace"
_SKILLS_STAGING_DIRNAME = ".skills"
_TURN_SCRATCH_DIRNAME = ".turns"
_GENERATION_DOWNLOAD_FILENAME = "_generation-download.zip"


class _ObjectClient(Protocol):
    """這是物件儲存 client 裡本模組會用到的方法, boto3 的 S3 client 跟
    FilesystemObjectClient 都滿足這個 Protocol, 用來收斂型別, 方便測試注入 stub. 參數命名
    沿用 boto3 的慣例, 所以 Bucket 和 Key 是大寫開頭."""

    def get_paginator(self, operation_name: str) -> Any: ...

    def download_file(self, Bucket: str, Key: str, Filename: str) -> None: ...

    def upload_file(self, Filename: str, Bucket: str, Key: str) -> None: ...

    def delete_objects(self, *, Bucket: str, Delete: dict[str, Any]) -> Any: ...


class WorkspaceStore:
    """每個 request 各自建立一個實例: 同一個實例會從 prepare 一路用到 persist,
    中間跨呼叫保存這一輪的狀態."""

    def __init__(
        self, local_root: Path, bucket: str, prefix: str, object_client: _ObjectClient
    ) -> None:
        self._local_root = local_root
        self._bucket = bucket
        self._prefix = f"{prefix.strip('/')}/" if prefix.strip("/") else ""
        self._object_client = object_client
        self._session_prefix: str | None = None
        self._scratch_base: Path | None = None

    def prepare(self, user_id: str, session_id: str) -> SessionWorkspace:
        self._session_prefix = f"{self._prefix}{user_id}/sessions/{session_id}/"
        self._scratch_base = self._local_root / _TURN_SCRATCH_DIRNAME / secrets.token_hex(8)
        workspace = prepare_local_layout(self._scratch_base, user_id, session_id)
        latest = self._latest_generation()
        if latest is not None:
            _generation_name, zip_key = latest
            self._pull_zip(zip_key, workspace.root)
        # user skills 跟 session 無關且唯讀, 這個 store 不會把它推回去, 只拉到 scratch 對應位置.
        # 讓 chat_turn 算 workspace.root.parents[1]/"skills" 這條路徑時能照常運作.
        self._pull(f"{self._prefix}{user_id}/skills/", workspace.root.parents[1] / "skills")
        return workspace

    def persist(self, workspace: SessionWorkspace) -> None:
        last_error: Exception | None = None
        for _attempt in range(_PERSIST_ATTEMPTS):
            generation = _new_generation_name()
            try:
                self._push(workspace, generation)
                break
            except Exception as error:
                last_error = error
                logger.warning(
                    "workspace push failed generation=%s, retrying with fresh key",
                    generation,
                    exc_info=True,
                )
        else:
            raise WorkspacePersistError(
                f"workspace persist failed after {_PERSIST_ATTEMPTS} attempts"
            ) from last_error
        self._cleanup_generations()
        self.cleanup_scratch()

    def cleanup_scratch(self) -> None:
        """刪掉這一輪的 per-turn scratch 目錄, 這個方法是冪等的, 重複呼叫也安全.
        沒有走到 persist() 的路徑一定要自己呼叫這個方法, 例如只 prepare 不 persist 的情境.
        不呼叫的話 scratch 目錄永遠不會被清掉."""
        if self._scratch_base is not None:
            shutil.rmtree(self._scratch_base, ignore_errors=True)

    def download_file(self, relative_path: str) -> bytes | None:
        """從最新一代裡取出單一檔案, 一定要先呼叫過 prepare().
        找不到檔案, 找不到 generation, 或整個 session 沒有快照, 都回傳 None, 不是例外情況."""
        assert self._session_prefix is not None, "download_file() 需先呼叫 prepare()"
        latest = self._latest_generation()
        if latest is None:
            return None
        _generation_name, zip_key = latest
        return self._download_zip_generation_entry(zip_key, relative_path)

    # -- internals ---------------------------------------------------------------------------

    def _scan_generations(self) -> dict[str, str]:
        """列出整個 session 前綴下的物件, 回傳 {generation 名: 物件 key}.
        generation 名格式是 gen-{13 碼 timestamp}-{8 碼 hex}, 固定長度讓字串排序等於時間排序."""
        assert self._session_prefix is not None
        generations: dict[str, str] = {}
        paginator = self._object_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=self._session_prefix):
            for entry in page.get("Contents", []):
                key = entry["Key"]
                relative_key = key[len(self._session_prefix) :]
                zip_match = _ZIP_GENERATION_PATTERN.fullmatch(relative_key)
                if not zip_match:
                    continue
                generation_name = f"gen-{zip_match.group(1)}-{zip_match.group(2)}"
                generations[generation_name] = key
        return generations

    def _latest_generation(self) -> tuple[str, str] | None:
        generations = self._scan_generations()
        if not generations:
            return None
        latest_name = max(generations)
        return latest_name, generations[latest_name]

    def _pull(self, remote_prefix: str, local_dir: Path) -> None:
        """逐一下載指定前綴下的所有物件, 目前只有 user skills 這種唯讀, 不是 generation
        快照的情境會用到."""
        local_dir.mkdir(parents=True, exist_ok=True)
        resolved_local_dir = local_dir.resolve()
        paginator = self._object_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=remote_prefix):
            for entry in page.get("Contents", []):
                key = entry["Key"]
                relative_key = key[len(remote_prefix) :]
                if not relative_key or key.endswith("/"):
                    continue
                destination = (local_dir / relative_key).resolve()
                if resolved_local_dir not in destination.parents:
                    raise ValueError(f"S3 object key escapes local workspace dir: {key!r}")
                destination.parent.mkdir(parents=True, exist_ok=True)
                self._object_client.download_file(self._bucket, key, str(destination))

    def _pull_zip(self, key: str, local_dir: Path) -> None:
        """把單一 zip 代物件下載到 scratch, 解壓進 local_dir 之後就刪掉暫存的 zip 檔."""
        assert self._scratch_base is not None
        zip_path = self._scratch_base / _GENERATION_DOWNLOAD_FILENAME
        self._object_client.download_file(self._bucket, key, str(zip_path))
        try:
            _extract_zip(zip_path, local_dir)
        finally:
            zip_path.unlink(missing_ok=True)

    def _download_zip_generation_entry(self, zip_key: str, relative_path: str) -> bytes | None:
        """zip 物件缺失, 損毀, 或 entry 不存在, 一律回傳 None, 不讓例外往外穿透.
        符合 download_file() 找不到就回 None 的契約."""
        from botocore.exceptions import ClientError

        with tempfile.TemporaryDirectory() as scratch_dir:
            zip_path = Path(scratch_dir) / _GENERATION_DOWNLOAD_FILENAME
            try:
                self._object_client.download_file(self._bucket, zip_key, str(zip_path))
                with zipfile.ZipFile(zip_path) as archive:
                    return archive.read(relative_path)
            except (FileNotFoundError, KeyError, ClientError, zipfile.BadZipFile):
                return None

    def _push(self, workspace: SessionWorkspace, generation: str) -> None:
        """把 workspace 打包成單一 zip, 上傳到 {session_prefix}{generation}.zip. 單一物件
        的 PUT 天然是原子的, 不需要 _complete 這種 marker. zip 檔會在 scratch 旁邊組裝,
        上傳完就刪掉."""
        assert self._session_prefix is not None
        assert self._scratch_base is not None
        zip_path = self._scratch_base / f"{generation}.zip"
        try:
            _build_zip(workspace, zip_path)
            self._object_client.upload_file(
                str(zip_path), self._bucket, f"{self._session_prefix}{generation}.zip"
            )
        finally:
            zip_path.unlink(missing_ok=True)

    def _cleanup_generations(self) -> None:
        try:
            generations = self._scan_generations()
            keep = set(sorted(generations)[-_KEPT_GENERATIONS:])
            doomed_keys = [key for name, key in generations.items() if name not in keep]
            for batch_start in range(0, len(doomed_keys), 1000):
                batch = doomed_keys[batch_start : batch_start + 1000]
                self._object_client.delete_objects(
                    Bucket=self._bucket,
                    Delete={"Objects": [{"Key": key} for key in batch], "Quiet": True},
                )
        except Exception:
            logger.warning("generation cleanup failed, leftover objects remain", exc_info=True)


def _new_generation_name() -> str:
    return f"gen-{time.time_ns() // 1_000_000:013d}-{secrets.token_hex(4)}"


def _build_zip(workspace: SessionWorkspace, zip_path: Path) -> None:
    """把 workspace.root 打包成單一 zip, 排除 .skills 這個 staging 目錄, 沿用原本 _push
    的排除邏輯. 單一物件的 PUT 天然是原子的, 讀方不會看到打包到一半的 workspace."""
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(workspace.root.rglob("*")):
            if path.is_dir():
                continue
            relative_path = path.relative_to(workspace.root)
            if relative_path.parts[0] == _SKILLS_STAGING_DIRNAME:
                continue
            archive.write(path, relative_path.as_posix())


def _extract_zip(zip_path: Path, local_dir: Path) -> None:
    """把 zip 解壓到 local_dir. entry 名稱是過去寫入留下來的不可信輸入, 所以要逐一驗證
    解析後的路徑仍然落在 local_dir 裡面, 防止 zip-slip 攻擊, 做法跟 _pull() 對 S3 object
    key 的逃逸檢查一樣."""
    local_dir.mkdir(parents=True, exist_ok=True)
    resolved_local_dir = local_dir.resolve()
    with zipfile.ZipFile(zip_path) as archive:
        for entry_name in archive.namelist():
            if entry_name.endswith("/"):
                continue
            destination = (local_dir / entry_name).resolve()
            if resolved_local_dir not in destination.parents:
                raise ValueError(f"zip entry escapes local workspace dir: {entry_name!r}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(entry_name) as source, destination.open("wb") as target:
                shutil.copyfileobj(source, target)


def build_workspace_store() -> WorkspaceStore:
    """依 STORAGE_BACKEND 決定要用哪個 object client, local 用 FilesystemObjectClient, s3 用 boto3.
    每個 request 都重新讀一次 settings, 不做成模組層級單例."""
    backend = get_settings().STORAGE_BACKEND
    if backend == "local":
        from app.engine.object_store_fs import FilesystemObjectClient

        workspace_root = resolve_workspace_root()
        return WorkspaceStore(
            local_root=workspace_root,
            bucket="local",
            prefix=WORKSPACE_PREFIX,
            object_client=FilesystemObjectClient(root=workspace_root),
        )
    if backend == "s3":
        from app.engine.s3 import build_s3_client

        settings = get_settings()
        key_prefix = settings.S3_KEY_PREFIX.strip("/")
        combined_prefix = f"{key_prefix}/{WORKSPACE_PREFIX}" if key_prefix else WORKSPACE_PREFIX
        return WorkspaceStore(
            local_root=resolve_workspace_root(),
            bucket=settings.S3_BUCKET,
            prefix=combined_prefix,
            object_client=build_s3_client(),
        )
    raise ValueError(f"unknown STORAGE_BACKEND: {backend!r}")
