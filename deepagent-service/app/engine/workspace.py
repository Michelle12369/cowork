"""Per-user/per-session workspace 目錄佈局、WorkspaceStore 抽象、skills staging。

engine 層——stdlib only,禁止 import 任何 LLM 框架(ruff TID251 會擋)。
"""

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.config import get_settings

_SAFE_SEGMENT_PATTERN = re.compile(r"^[\w-]+$")


@dataclass(frozen=True)
class SessionWorkspace:
    root: Path

    @property
    def queries_dir(self) -> Path:
        return self.root / "queries"

    @property
    def results_dir(self) -> Path:
        return self.root / "results"

    @property
    def dashboard_path(self) -> Path:
        return self.root / "dashboard.html"

    @property
    def skills_dir(self) -> Path:
        return self.root / ".skills"

    @property
    def sources_doc_path(self) -> Path:
        return self.root / "sources.md"


class WorkspaceStore(Protocol):
    def prepare(self, user_id: str, session_id: str) -> SessionWorkspace: ...

    def persist(self, workspace: SessionWorkspace) -> None: ...

    def cleanup_scratch(self) -> None: ...


class WorkspacePersistError(RuntimeError):
    """persist 重試耗盡——本輪產出未寫入持久層。"""


def _validate_segment(value: str, label: str) -> None:
    if not _SAFE_SEGMENT_PATTERN.fullmatch(value):
        raise ValueError(f"unsafe {label}: {value!r}")


def prepare_local_layout(workspace_root: Path, user_id: str, session_id: str) -> SessionWorkspace:
    """驗證 user_id/session_id 安全性、算出 session 目錄路徑並確保骨架目錄存在。"""
    _validate_segment(user_id, "user_id")
    _validate_segment(session_id, "session_id")

    resolved_workspace_root = workspace_root.resolve()
    root = (resolved_workspace_root / user_id / "sessions" / session_id).resolve()
    if resolved_workspace_root not in root.parents:
        raise ValueError(f"workspace root escapes workspace_root: {root!r}")

    workspace = SessionWorkspace(root=root)
    workspace.root.mkdir(parents=True, exist_ok=True)
    workspace.queries_dir.mkdir(parents=True, exist_ok=True)
    workspace.results_dir.mkdir(parents=True, exist_ok=True)
    workspace.skills_dir.mkdir(parents=True, exist_ok=True)
    return workspace


def resolve_workspace_root() -> Path:
    return Path(get_settings().AGENT_WORKSPACE_ROOT)


class LocalWorkspaceStore:
    def __init__(self, workspace_root: Path) -> None:
        self._workspace_root = workspace_root

    def prepare(self, user_id: str, session_id: str) -> SessionWorkspace:
        return prepare_local_layout(self._workspace_root, user_id, session_id)

    def persist(self, workspace: SessionWorkspace) -> None:
        """本地目錄即持久層,no-op。"""

    def cleanup_scratch(self) -> None:
        """本地目錄即持久層,NEVER 刪——沒有 per-turn scratch 這種東西,no-op。"""


def build_workspace_store() -> WorkspaceStore:
    """依 STORAGE_BACKEND 分派。每 request 呼叫一次現讀 settings(不做 module 單例)——
    設定值凍結在 import 期會讓測試的 monkeypatch 失效,理由同 resolve_workspace_root()。"""
    backend = get_settings().STORAGE_BACKEND
    if backend == "local":
        return LocalWorkspaceStore(resolve_workspace_root())
    if backend == "s3":
        from app.engine.s3 import build_s3_client
        from app.engine.workspace_s3 import WORKSPACE_PREFIX, S3WorkspaceStore

        settings = get_settings()
        key_prefix = settings.S3_KEY_PREFIX.strip("/")
        combined_prefix = f"{key_prefix}/{WORKSPACE_PREFIX}" if key_prefix else WORKSPACE_PREFIX
        return S3WorkspaceStore(
            local_root=resolve_workspace_root(),
            bucket=settings.S3_BUCKET,
            # WORKSPACE_PREFIX 段與 backend S3WorkspacePurger.WORKSPACE_PREFIX 對齊——不做設定項;
            # key_prefix 段是共用 bucket 子路徑,與 backend erd.storage.s3.key-prefix 同值。
            prefix=combined_prefix,
            s3_client=build_s3_client(),
        )
    raise ValueError(f"unknown STORAGE_BACKEND: {backend!r}")


def builtin_skills_dir() -> Path:
    override = get_settings().AGENT_BUILTIN_SKILLS_DIR
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "skills"


def _has_skill(directory: Path) -> bool:
    return directory.is_dir() and any(directory.glob("*/SKILL.md"))


def stage_skills(
    workspace: SessionWorkspace, builtin_dir: Path, user_skills_dir: Path
) -> list[str]:
    """把 builtin/user skills 複製進 workspace 的 `.skills/`(deepagents filesystem backend
    要求 skills 路徑在其 root 之下)。每 turn 先清空以保證乾淨 stage;回傳存在且非空
    (含至少一個 `*/SKILL.md`)者的相對路徑,順序固定 builtin 在前——deepagents 同名 skill
    後者覆寫前者,個人 skill 蓋內建。
    """
    shutil.rmtree(workspace.skills_dir, ignore_errors=True)
    workspace.skills_dir.mkdir(parents=True, exist_ok=True)

    staged: list[str] = []
    for source_dir, name in ((builtin_dir, "builtin"), (user_skills_dir, "user")):
        if not _has_skill(source_dir):
            continue
        destination = workspace.skills_dir / name
        shutil.copytree(source_dir, destination, dirs_exist_ok=True)
        staged.append(f".skills/{name}")
    return staged


def write_sources_doc(workspace: SessionWorkspace, sources: list[tuple[str, str]]) -> None:
    """把 (alias, fileType) 清單寫成 sources.md;不含 path——路徑是 infra 細節,模型只需 alias。"""
    lines = ["# Data Sources", ""]
    for alias, file_type in sources:
        lines.append(f"- `{alias}` ({file_type})")
    workspace.sources_doc_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
