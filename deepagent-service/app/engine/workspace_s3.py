"""S3-backed WorkspaceStore——generation 快照模型。

internal 儲存規範:同一 object key 不可重複上傳。因此 workspace 不覆寫既有物件,每 turn
persist 推一個全新 generation prefix(gen-{epochMillis13碼}-{8碼隨機hex}/),全部推完後最後寫
`_complete` 標記;prepare 只讀「timestamp 最大且帶 _complete」的 generation。讀方永遠拿到
完整一致快照,半途失敗的 push 天然不可見。

本地 scratch 為 per-turn 隔離目錄({local_root}/.turns/{hex}/),persist 成功後刪除——兩個
併發 turn(雙 tab)落在同一 pod 也不互踩;跨 turn 併發語意為 last-writer-wins(spec 定案)。

engine 純度規則:stdlib + boto3,禁止 LLM 框架(ruff TID251)。
"""

import logging
import re
import secrets
import shutil
import time
from pathlib import Path
from typing import Any, Protocol

# _validate_segment 為 workspace.py 底線私有——依 brief 選擇直接 import 而非改名公開,
# 以免動到既有呼叫點;本檔與 workspace.py 同屬 engine 層,視為同一模組群組的內部共用。
from app.engine.workspace import (
    SessionWorkspace,
    WorkspacePersistError,
    _validate_segment,
    prepare_local_layout,
)

logger = logging.getLogger(__name__)

_GENERATION_PATTERN = re.compile(r"^gen-(\d{13})-([0-9a-f]{8})$")
_COMPLETE_MARKER = "_complete"
_KEPT_GENERATIONS = 2
_PERSIST_ATTEMPTS = 3
# 未完成 generation 只有舊於此值才可刪——防止清掉「另一個併發 turn 正在推」的半成品
_STALE_INCOMPLETE_MS = 60 * 60 * 1000
_SKILLS_STAGING_DIRNAME = ".skills"
_TURN_SCRATCH_DIRNAME = ".turns"


class _S3Client(Protocol):
    """boto3 S3 client 中本模組用到的方法——Protocol 收斂型別,測試注入 stub。"""

    def get_paginator(self, operation_name: str) -> Any: ...

    def download_file(self, Bucket: str, Key: str, Filename: str) -> None: ...

    def upload_file(self, Filename: str, Bucket: str, Key: str) -> None: ...

    def put_object(self, *, Bucket: str, Key: str, Body: bytes) -> Any: ...

    def delete_objects(self, *, Bucket: str, Delete: dict[str, Any]) -> Any: ...


class S3WorkspaceStore:
    """non-bean: instantiate per request(prepare→persist 同一實例,跨呼叫持有 turn 狀態)。"""

    def __init__(self, local_root: Path, bucket: str, prefix: str, s3_client: _S3Client) -> None:
        self._local_root = local_root
        self._bucket = bucket
        self._prefix = f"{prefix.strip('/')}/" if prefix.strip("/") else ""
        self._s3_client = s3_client
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
            self._pull(f"{self._session_prefix}{latest}/", workspace.root)
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
        if self._scratch_base is not None:
            shutil.rmtree(self._scratch_base, ignore_errors=True)

    # -- internals ---------------------------------------------------------------------------

    def _scan_generations(self) -> dict[str, dict[str, Any]]:
        """單趟 list 整個 session 前綴 → {generation 名: {"keys": [...], "complete": bool}}。"""
        assert self._session_prefix is not None
        generations: dict[str, dict[str, Any]] = {}
        paginator = self._s3_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=self._session_prefix):
            for entry in page.get("Contents", []):
                key = entry["Key"]
                relative_key = key[len(self._session_prefix) :]
                generation_name, _, remainder = relative_key.partition("/")
                if not _GENERATION_PATTERN.fullmatch(generation_name):
                    continue
                record = generations.setdefault(generation_name, {"keys": [], "complete": False})
                record["keys"].append(key)
                if remainder == _COMPLETE_MARKER:
                    record["complete"] = True
        return generations

    def _latest_complete_generation(self) -> str | None:
        generations = self._scan_generations()
        complete = sorted(name for name, record in generations.items() if record["complete"])
        return complete[-1] if complete else None

    def _pull(self, remote_prefix: str, local_dir: Path) -> None:
        local_dir.mkdir(parents=True, exist_ok=True)
        resolved_local_dir = local_dir.resolve()
        paginator = self._s3_client.get_paginator("list_objects_v2")
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
                self._s3_client.download_file(self._bucket, key, str(destination))

    def _push(self, workspace: SessionWorkspace, generation: str) -> None:
        assert self._session_prefix is not None
        generation_prefix = f"{self._session_prefix}{generation}/"
        for path in sorted(workspace.root.rglob("*")):
            if path.is_dir():
                continue
            relative_path = path.relative_to(workspace.root)
            if relative_path.parts[0] == _SKILLS_STAGING_DIRNAME:
                continue
            self._s3_client.upload_file(
                str(path), self._bucket, f"{generation_prefix}{relative_path.as_posix()}"
            )
        # 完成標記 MUST 最後寫——它落地前這個 generation 對所有讀方不可見
        self._s3_client.put_object(
            Bucket=self._bucket, Key=f"{generation_prefix}{_COMPLETE_MARKER}", Body=b""
        )

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
                    timestamp_millis = int(_GENERATION_PATTERN.fullmatch(name).group(1))
                    if now_millis - timestamp_millis < _STALE_INCOMPLETE_MS:
                        continue  # 可能是另一個併發 turn 正在推的半成品,不碰
                doomed_keys.extend(record["keys"])
            for batch_start in range(0, len(doomed_keys), 1000):
                batch = doomed_keys[batch_start : batch_start + 1000]
                self._s3_client.delete_objects(
                    Bucket=self._bucket,
                    Delete={"Objects": [{"Key": key} for key in batch], "Quiet": True},
                )
        except Exception:
            logger.warning("generation cleanup failed, leftover objects remain", exc_info=True)


def _new_generation_name() -> str:
    return f"gen-{time.time_ns() // 1_000_000:013d}-{secrets.token_hex(4)}"
