"""S3/MinIO-backed WorkspaceStore——公司 k8s 無 RWX PV,prod 路線用物件儲存取代共享磁碟。

engine 純度規則(見 workspace.py 檔頭):stdlib + boto3 允許,LLM 框架仍禁止(ruff TID251 會擋,
本檔不在 per-file-ignores 白名單內,誤 import 會直接壞 lint)。

策略:每個 request 的本地 `AGENT_WORKSPACE_ROOT` 目錄退化成「cache」——`prepare` 時從 S3
lazy pull(只拉這個 user/session 需要的物件,覆蓋本地對應路徑),`persist` 時把 session 目錄
(排除 `.skills/` 這個每輪重新 staging 的暫存)全量推回 S3。物件皆為 KB 級小檔,全量 push/pull
比增量同步簡單且足夠快,不做 diff。
"""

import logging
import os
from pathlib import Path
from typing import Any, Protocol

from app.engine.workspace import (
    LocalWorkspaceStore,
    SessionWorkspace,
    WorkspaceStore,
    prepare_local_layout,
)

logger = logging.getLogger(__name__)

# session 目錄底下,persist 時永遠跳過的第一層資料夾——deepagents filesystem backend 要求
# skills 路徑在 workspace root 之下,所以每輪由 stage_skills() 重新複製進來,本身不是要保存
# 的使用者產出,推回 S3 既浪費頻寬也會跟下一輪的 lazy pull 打架(pull 只拉 sessions/ 前綴,
# 不會覆蓋到 .skills/,但沒有排除的話 persist 會把它當成使用者資料傳上去、越積越多)。
_SKILLS_STAGING_DIRNAME = ".skills"


class _S3Client(Protocol):
    """本模組實際只用到 boto3 S3 client 的這三個方法——用 Protocol 收斂型別、方便測試注入
    Stub/moto client,不強制呼叫端一定要是真正的 boto3.client("s3") 回傳型別。"""

    def get_paginator(self, operation_name: str) -> Any: ...

    def download_file(self, bucket: str, key: str, filename: str) -> None: ...

    def upload_file(self, filename: str, bucket: str, key: str) -> None: ...


class S3WorkspaceStore:
    """`local_root` 角色是 cache,不是 source of truth——真正的資料在 `bucket`/`prefix` 底下。"""

    def __init__(self, local_root: Path, bucket: str, prefix: str, s3_client: _S3Client) -> None:
        self._local_root = local_root
        self._bucket = bucket
        self._prefix = f"{prefix.rstrip('/')}/" if prefix else ""
        self._s3_client = s3_client

    def prepare(self, user_id: str, session_id: str) -> SessionWorkspace:
        workspace = prepare_local_layout(self._local_root, user_id, session_id)
        # 拉檔失敗(網路/憑證/bucket 問題)直接讓例外往上冒——資料不完整不能開工,既有的
        # ERROR 事件路徑(main.py 的 chat() 沒有包 try 在 prepare() 外層,例外會讓整個 request
        # 500)接手,比帶著半套資料開工安全。
        self._pull(f"{self._prefix}{user_id}/sessions/{session_id}/", workspace.root)
        self._pull(f"{self._prefix}{user_id}/skills/", self._user_skills_dir(workspace))
        return workspace

    def persist(self, workspace: SessionWorkspace) -> None:
        try:
            self._push(workspace)
        except Exception:
            # persist 失敗不擋主流程:本輪產出(DASHBOARD_HTML/ANSWER 等 SSE 事件)已經送給
            # 使用者,本地 cache 檔案仍在磁碟上,下一輪同一個 pod 接手時 persist 會重推一次
            # (session 全量 push,冪等)。比照本 repo 既有「驗證器不擋主流程」的哲學——資料
            # 已經產出,不該因為儲存層的暫時性錯誤讓使用者連結果都拿不到。唯一風險是這一輪
            # 若下一個請求被排到「另一個」pod,該 pod 本地沒有這份 cache、S3 也還沒收到,會
            # 讀到舊版——這是本階段接受的已知限制,不在此次範圍內解。
            logger.warning(
                "S3 workspace persist failed, local cache retained for retry next turn: %s",
                workspace.root,
                exc_info=True,
            )

    def _user_skills_dir(self, workspace: SessionWorkspace) -> Path:
        # 與 main.py 呼叫 stage_skills() 時傳入的 user_skills_dir 同一條路徑算法
        # (workspace.root.parents[1] / "skills" == local_root/user_id/skills)。
        return workspace.root.parents[1] / "skills"

    def _pull(self, remote_prefix: str, local_dir: Path) -> None:
        local_dir.mkdir(parents=True, exist_ok=True)
        resolved_local_dir = local_dir.resolve()
        paginator = self._s3_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=remote_prefix):
            for entry in page.get("Contents", []):
                key = entry["Key"]
                relative_key = key[len(remote_prefix) :]
                if not relative_key or key.endswith("/"):
                    continue  # S3 "目錄" placeholder object,沒有實際檔案內容可拉
                destination = (local_dir / relative_key).resolve()
                if resolved_local_dir not in destination.parents:
                    # 理論上不該發生(key 只可能來自我們自己 persist 寫入的乾淨路徑),但物件
                    # 儲存沒有檔案系統那層路徑合法性保證,防禦一下、比照 workspace.py 既有的
                    # path-traversal guard 風格。
                    raise ValueError(f"S3 object key escapes local workspace dir: {key!r}")
                destination.parent.mkdir(parents=True, exist_ok=True)
                self._s3_client.download_file(self._bucket, key, str(destination))

    def _push(self, workspace: SessionWorkspace) -> None:
        resolved_local_root = self._local_root.resolve()
        session_id = workspace.root.name
        user_id = workspace.root.relative_to(resolved_local_root).parts[0]
        remote_prefix = f"{self._prefix}{user_id}/sessions/{session_id}/"

        for path in workspace.root.rglob("*"):
            if path.is_dir():
                continue
            relative_path = path.relative_to(workspace.root)
            if relative_path.parts[0] == _SKILLS_STAGING_DIRNAME:
                continue
            key = f"{remote_prefix}{relative_path.as_posix()}"
            self._s3_client.upload_file(str(path), self._bucket, key)


def _build_s3_client() -> Any:
    import boto3
    from botocore.config import Config

    use_ssl = os.environ.get("AGENT_S3_USE_SSL", "false").lower() == "true"
    scheme = "https" if use_ssl else "http"
    return boto3.client(
        "s3",
        endpoint_url=f"{scheme}://{os.environ['AGENT_S3_ENDPOINT']}",
        aws_access_key_id=os.environ["AGENT_S3_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AGENT_S3_SECRET_ACCESS_KEY"],
        region_name=os.environ.get("AGENT_S3_REGION", "us-east-1"),
        # MinIO 需要 path-style addressing(跟 engine/duck.py 的 s3_url_style='path' 同一個
        # 理由——virtual-hosted style 對非 AWS endpoint 會解析失敗)。
        config=Config(s3={"addressing_style": "path"}),
    )


def build_workspace_store() -> WorkspaceStore:
    """讀 `AGENT_WORKSPACE_BACKEND`(`local`|`s3`,預設 `local`)決定回傳哪種 store。

    每個 request 呼叫一次(main.py 每 request 建構,不做 module 層單例)——理由與既有的
    `resolve_workspace_root()` 同:env 值凍結在 import 期會讓測試的 monkeypatch.setenv 失效。
    """
    from app.engine.workspace import resolve_workspace_root

    backend = os.environ.get("AGENT_WORKSPACE_BACKEND", "local")
    if backend == "local":
        return LocalWorkspaceStore(resolve_workspace_root())
    if backend == "s3":
        bucket = os.environ.get("AGENT_WORKSPACE_S3_BUCKET", "erd-cowork")
        prefix = os.environ.get("AGENT_WORKSPACE_S3_PREFIX", "workspace/")
        return S3WorkspaceStore(resolve_workspace_root(), bucket, prefix, _build_s3_client())
    raise ValueError(f"unknown AGENT_WORKSPACE_BACKEND: {backend!r}")
