"""WorkspaceStore 建構點。

engine 純度規則(見 workspace.py 檔頭):僅 stdlib,LLM 框架禁止(ruff TID251 會擋)。
"""

from app.engine.workspace import LocalWorkspaceStore, WorkspaceStore


def build_workspace_store() -> WorkspaceStore:
    """每個 request 呼叫一次(不做 module 層單例)——env 值凍結在 import 期會讓測試的
    monkeypatch.setenv 失效,理由同 resolve_workspace_root()。"""
    from app.engine.workspace import resolve_workspace_root

    return LocalWorkspaceStore(resolve_workspace_root())
