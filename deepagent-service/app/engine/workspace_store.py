"""WorkspaceStore——generation 快照模型,物件儲存 client 可插拔(s3=boto3、
local=FilesystemObjectClient),兩種 STORAGE_BACKEND 共用同一條 code path 與磁碟佈局。

internal 儲存規範:同一 object key 不可重複上傳。因此 workspace 不覆寫既有物件,每 turn
persist 把整個 workspace 打包成單一 zip、推一個全新 generation key
(`gen-{epochMillis13碼}-{8碼隨機hex}.zip`)。單物件 PUT 天然原子——不存在半途可見的中間
狀態,讀方永遠拿到完整快照或完全看不到這一代,因此不再需要 `_complete` marker。

**向後相容**:早期版本用「per-file 逐一上傳到 `gen-*/` 目錄前綴 + 最後寫 `_complete`
marker」的舊格式(多物件無原子性,靠 marker 自造 commit)。prepare/cleanup/download_file
仍認得這種舊代——`_scan_generations` 同時掃兩種形狀,取聯集中 timestamp 最大的 complete
代;generation 名字空間共用同一個定長 timestamp 前綴,字串排序即可跨形狀比較新舊。persist
只再寫新的 zip 格式,舊格式只讀不寫。

本地 scratch 為 per-turn 隔離目錄({local_root}/.turns/{hex}/),persist 成功後刪除——兩個
併發 turn(雙 tab)落在同一 pod 也不互踩;跨 turn 併發語意為 last-writer-wins(spec 定案)。

engine 純度規則:stdlib + boto3,禁止 LLM 框架(ruff TID251)。
"""

import logging
import re
import secrets
import shutil
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any, Protocol

# _validate_segment 為 workspace.py 底線私有——依 brief 選擇直接 import 而非改名公開,
# 以免動到既有呼叫點;本檔與 workspace.py 同屬 engine 層,視為同一模組群組的內部共用。
from app.config import get_settings
from app.engine.workspace import (
    SessionWorkspace,
    WorkspacePersistError,
    _validate_segment,
    prepare_local_layout,
    resolve_workspace_root,
)

logger = logging.getLogger(__name__)

# 舊 per-file 代:目錄前綴的第一段,例如 "gen-1723107600123-abcd1234"。
_DIR_GENERATION_PATTERN = re.compile(r"^gen-(\d{13})-([0-9a-f]{8})$")
# 新 zip 代:session 前綴下的單一物件全名,例如 "gen-1723107600123-abcd1234.zip"。
_ZIP_GENERATION_PATTERN = re.compile(r"^gen-(\d{13})-([0-9a-f]{8})\.zip$")
_COMPLETE_MARKER = "_complete"
_KEPT_GENERATIONS = 2
_PERSIST_ATTEMPTS = 3
# 與 backend S3WorkspacePurger.WORKSPACE_PREFIX 必須一致——兩側寫死同一個值,不做設定項,
# 避免各自改動導致 backend 清不到 deepagent 實際寫入的前綴。
WORKSPACE_PREFIX = "workspace"
# 未完成 generation 只有舊於此值才可刪——防止清掉「另一個併發 turn 正在推」的半成品
# (只適用舊 per-file 代;zip 代單物件 PUT 沒有「未完成但存在」的狀態)。
_STALE_INCOMPLETE_MS = 60 * 60 * 1000
_SKILLS_STAGING_DIRNAME = ".skills"
_TURN_SCRATCH_DIRNAME = ".turns"
_GENERATION_DOWNLOAD_FILENAME = "_generation-download.zip"


class _ObjectClient(Protocol):
    """物件儲存 client 中本模組用到的方法(boto3 S3 與 FilesystemObjectClient 都滿足)——
    Protocol 收斂型別,測試注入 stub。簽名沿用 boto3 慣例(Bucket/Key 大寫參數名)。"""

    def get_paginator(self, operation_name: str) -> Any: ...

    def download_file(self, Bucket: str, Key: str, Filename: str) -> None: ...

    def upload_file(self, Filename: str, Bucket: str, Key: str) -> None: ...

    def delete_objects(self, *, Bucket: str, Delete: dict[str, Any]) -> Any: ...


class WorkspaceStore:
    """non-bean: instantiate per request(prepare→persist 同一實例,跨呼叫持有 turn 狀態)。"""

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
        _validate_segment(user_id, "user_id")
        _validate_segment(session_id, "session_id")
        self._session_prefix = f"{self._prefix}{user_id}/sessions/{session_id}/"
        self._scratch_base = self._local_root / _TURN_SCRATCH_DIRNAME / secrets.token_hex(8)
        workspace = prepare_local_layout(self._scratch_base, user_id, session_id)
        latest = self._latest_complete_generation()
        if latest is not None:
            generation_name, record = latest
            if record["kind"] == "zip":
                self._pull_zip(record["keys"][0], workspace.root)
            else:
                self._pull(f"{self._session_prefix}{generation_name}/", workspace.root)
        # user skills 與 session 無關、read-only(本 store 永不推回)——拉到 scratch 內對應
        # 位置,讓 chat_turn 的 workspace.root.parents[1]/"skills" 路徑算法照常成立
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
        """刪掉本輪的 per-turn scratch 目錄({local_root}/.turns/{hex}/)——冪等,persist 成功
        後已刪除時再呼叫一次也安全(ignore_errors)。除了 persist() 尾端呼叫,呼叫端 MUST 在
        任何不 persist 的路徑(/repair 只 prepare 不 persist、/chat 提前以 ErrorEvent 終止、
        finalize 內 guard 修復輪 ErrorEvent return、persist 重試耗盡後 raise)也呼叫這個方法,
        否則 scratch 目錄永遠不會被清掉。"""
        if self._scratch_base is not None:
            shutil.rmtree(self._scratch_base, ignore_errors=True)

    def download_file(self, relative_path: str) -> bytes | None:
        """從最新 complete 代取單一檔案,不需完整 prepare()——MUST 先呼叫過 prepare()
        (需要 self._session_prefix)。zip 代下載整包後解出該 entry;舊 per-file 代直接下載
        對應單物件。找不到該檔案、該 generation、或整個 session 尚無快照皆回傳 None(呼叫端
        以 None 代表「檔案不存在」,不是例外情境)。"""
        assert self._session_prefix is not None, "download_file() 需先呼叫 prepare()"
        latest = self._latest_complete_generation()
        if latest is None:
            return None
        generation_name, record = latest
        if record["kind"] == "zip":
            return self._download_zip_generation_entry(record["keys"][0], relative_path)
        return self._download_legacy_generation_file(
            f"{self._session_prefix}{generation_name}/{relative_path}"
        )

    # -- internals ---------------------------------------------------------------------------

    def _scan_generations(self) -> dict[str, dict[str, Any]]:
        """單趟 list 整個 session 前綴 -> {generation 名: {"keys": [...], "complete": bool,
        "kind": "zip"|"dir"}}。zip 代(單一 `gen-*.zip` 物件,PUT 落地即 complete)與舊
        per-file 代(`gen-*/` 目錄形,靠 `_complete` marker 判定)統一用 generation 名
        (`gen-{定長 13 碼 timestamp}-{8 碼 hex}`,zip 代去掉 `.zip` 副檔名後同形)登記——
        定長 timestamp 讓字串排序與時間排序一致,兩種形狀因此可直接混合比較新舊。"""
        assert self._session_prefix is not None
        generations: dict[str, dict[str, Any]] = {}
        paginator = self._object_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=self._session_prefix):
            for entry in page.get("Contents", []):
                key = entry["Key"]
                relative_key = key[len(self._session_prefix) :]
                zip_match = _ZIP_GENERATION_PATTERN.fullmatch(relative_key)
                if zip_match:
                    generation_name = f"gen-{zip_match.group(1)}-{zip_match.group(2)}"
                    generations[generation_name] = {
                        "keys": [key],
                        "complete": True,
                        "kind": "zip",
                    }
                    continue
                generation_name, _, remainder = relative_key.partition("/")
                if not _DIR_GENERATION_PATTERN.fullmatch(generation_name):
                    continue
                record = generations.setdefault(
                    generation_name, {"keys": [], "complete": False, "kind": "dir"}
                )
                record["keys"].append(key)
                if remainder == _COMPLETE_MARKER:
                    record["complete"] = True
        return generations

    def _latest_complete_generation(self) -> tuple[str, dict[str, Any]] | None:
        generations = self._scan_generations()
        complete_names = sorted(name for name, record in generations.items() if record["complete"])
        if not complete_names:
            return None
        latest_name = complete_names[-1]
        return latest_name, generations[latest_name]

    def _pull(self, remote_prefix: str, local_dir: Path) -> None:
        local_dir.mkdir(parents=True, exist_ok=True)
        resolved_local_dir = local_dir.resolve()
        paginator = self._object_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=remote_prefix):
            for entry in page.get("Contents", []):
                key = entry["Key"]
                relative_key = key[len(remote_prefix) :]
                if not relative_key or key.endswith("/") or relative_key == _COMPLETE_MARKER:
                    continue
                destination = (local_dir / relative_key).resolve()
                if resolved_local_dir not in destination.parents:
                    raise ValueError(f"S3 object key escapes local workspace dir: {key!r}")
                destination.parent.mkdir(parents=True, exist_ok=True)
                self._object_client.download_file(self._bucket, key, str(destination))

    def _pull_zip(self, key: str, local_dir: Path) -> None:
        """下載單一 zip 代物件到 scratch,解壓進 local_dir 後刪除暫存 zip。"""
        assert self._scratch_base is not None
        zip_path = self._scratch_base / _GENERATION_DOWNLOAD_FILENAME
        self._object_client.download_file(self._bucket, key, str(zip_path))
        try:
            _extract_zip(zip_path, local_dir)
        finally:
            zip_path.unlink(missing_ok=True)

    def _download_zip_generation_entry(self, zip_key: str, relative_path: str) -> bytes | None:
        """對稱於 `_download_legacy_generation_file`:zip 物件本身缺失(FileNotFoundError/
        KeyError/ClientError)或已下載但損毀(zipfile.BadZipFile,例如寫入未完成即被讀到)、
        entry 不存在(KeyError)皆回傳 None,不讓例外穿透——符合 download_file() docstring
        「找不到皆回 None」的契約。"""
        from botocore.exceptions import ClientError

        with tempfile.TemporaryDirectory() as scratch_dir:
            zip_path = Path(scratch_dir) / _GENERATION_DOWNLOAD_FILENAME
            try:
                self._object_client.download_file(self._bucket, zip_key, str(zip_path))
                with zipfile.ZipFile(zip_path) as archive:
                    return archive.read(relative_path)
            except (FileNotFoundError, KeyError, ClientError, zipfile.BadZipFile):
                return None

    def _download_legacy_generation_file(self, key: str) -> bytes | None:
        from botocore.exceptions import ClientError

        with tempfile.TemporaryDirectory() as scratch_dir:
            destination = Path(scratch_dir) / "download.bin"
            try:
                self._object_client.download_file(self._bucket, key, str(destination))
            except (FileNotFoundError, KeyError, ClientError):
                return None
            return destination.read_bytes()

    def _push(self, workspace: SessionWorkspace, generation: str) -> None:
        """把 workspace 打包成單一 zip 上傳到 `{session_prefix}{generation}.zip`——單物件
        PUT 天然原子,不再需要 `_complete` marker。zip 在 scratch 旁組裝、上傳後刪除。"""
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
            complete_names = sorted(
                name for name, record in generations.items() if record["complete"]
            )
            keep = set(complete_names[-_KEPT_GENERATIONS:])
            now_millis = time.time_ns() // 1_000_000
            doomed_keys: list[str] = []
            for name, record in generations.items():
                if name in keep:
                    continue
                if not record["complete"]:
                    timestamp_millis = int(_DIR_GENERATION_PATTERN.fullmatch(name).group(1))
                    if now_millis - timestamp_millis < _STALE_INCOMPLETE_MS:
                        continue  # 可能是另一個併發 turn 正在推的半成品,不碰
                doomed_keys.extend(record["keys"])
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
    """把 workspace.root 打包成單一 zip(排除 `.skills` staging,沿用舊 `_push` 的排除
    邏輯)——單物件 PUT 天然原子,讀方不會看到半個 workspace。"""
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(workspace.root.rglob("*")):
            if path.is_dir():
                continue
            relative_path = path.relative_to(workspace.root)
            if relative_path.parts[0] == _SKILLS_STAGING_DIRNAME:
                continue
            archive.write(path, relative_path.as_posix())


def _extract_zip(zip_path: Path, local_dir: Path) -> None:
    """zip 解壓到 local_dir——entry 名稱是歷史寫入的不可信輸入,逐一驗證解析後路徑仍落在
    local_dir 內(zip-slip 防護),比照 `_pull()` 對 S3 object key 的 escape 檢查。"""
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
    """依 STORAGE_BACKEND 選 object client。每 request 呼叫一次現讀 settings(不做 module
    單例)——設定值凍結在 import 期會讓測試的 monkeypatch 失效。

    local 模式用 FilesystemObjectClient 把 AGENT_WORKSPACE_ROOT 本身當「bucket」,磁碟
    佈局因此與 s3 模式一致:`workspace/{userId}/sessions/{sessionId}/gen-*.zip`(持久化
    generation,單物件)、`workspace/{userId}/skills/`(使用者 skills,唯讀)、
    `.turns/{hex}/...`(per-turn scratch,persist 後刪除)、`.sources-cache/uploads/...`
    (上傳檔 cache,見 source_cache.resolve_source_path)。"""
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
