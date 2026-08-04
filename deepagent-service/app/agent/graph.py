"""deepagents assembly -- wires the chat model, data tools, staged skills, and workspace
filesystem backend into a compiled LangGraph graph the event layer drives via
`astream_events`."""

from deepagents.backends.filesystem import FilesystemBackend
from deepagents.backends.protocol import EditResult, WriteResult
from deepagents.profiles import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    register_harness_profile,
)
from duckdb import DuckDBPyConnection
from langchain_core.language_models import BaseChatModel
from langgraph.graph.state import CompiledStateGraph

from app.agent import session_state
from app.agent.middleware import (
    DashboardSkillGateMiddleware,
    SerializedToolCallsMiddleware,
    WiringManifestMiddleware,
)
from app.agent.prompts import SYSTEM_PROMPT
from app.agent.runtime.deepagents_runtime import DeepAgentsRuntime
from app.agent.tools.data import build_data_tools
from app.agent.tools.recording import ToolResultRecorder
from app.engine.workspace import SessionWorkspace

# write() 允許整份覆寫的檔案集合:dashboard.html 與記錄用的 notes.md。
_OVERWRITABLE_FILE_NAMES = frozenset({"dashboard.html", "notes.md"})

# edit() 一律退貨的檔案。
_EDIT_REJECTED_FILE_NAME = "dashboard.html"

# dashboard.html 的 edit_file 確定性退貨訊息——錯誤訊息即行為指令,模型看到後改走單次
# write_file 整份重寫(single-write 實驗的核心不變量)。
DASHBOARD_EDIT_REJECTED_MESSAGE = (
    "dashboard.html must NOT be edited in place. Rewrite it in full instead: finish all "
    "run_sql data gathering first, then produce the complete corrected HTML with a single "
    "write_file call (overwriting dashboard.html is allowed)."
)

# 關掉 general-purpose subagent:它曾委派子任務「用 Python 算迴歸」給自己,寫了 .py 腳本卻
# 沒有執行機制,繞了好幾分鐘才改用 SQL。excluded_tools 移除 edit_file 的模型可見 schema
# (single-write 補強):模型會無視 edit() 退貨訊息陷入 read→edit→退貨循環直到 recursion
# limit,物理剝除比只靠退貨訊息教育可靠。key="openai" 對應這裡唯一會建的模型類別 ChatOpenAI。
register_harness_profile(
    "openai",
    HarnessProfile(
        general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
        excluded_tools=frozenset({"edit_file"}),
    ),
)


class DashboardOverwriteBackend(FilesystemBackend):
    """single-write invariant: dashboard.html 只能整份重寫,never 局部編輯。`write()`:
    parent 預設 create-only 會擋掉已存在檔案的整份覆寫,故先 unlink `_OVERWRITABLE_FILE_NAMES`
    (dashboard.html/notes.md)再委派給 parent。`edit()`: dashboard.html 一律退貨
    `DASHBOARD_EDIT_REJECTED_MESSAGE`,逼模型改走整份 write_file;這是 defense-in-depth——
    `edit_file` 已從模型 schema 移除(見上方 excluded_tools),這裡是 backend 層再擋一次。
    """

    def write(self, file_path: str, content: str) -> WriteResult:
        try:
            resolved_path = self._resolve_path(file_path)
        except (OSError, RuntimeError) as error:
            return WriteResult(error=f"Error writing file '{file_path}': {error}")

        overwritable_paths = {(self.cwd / name).resolve() for name in _OVERWRITABLE_FILE_NAMES}
        if resolved_path in overwritable_paths and resolved_path.exists():
            resolved_path.unlink()

        return super().write(file_path, content)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        try:
            resolved_path = self._resolve_path(file_path)
        except (OSError, RuntimeError) as error:
            return EditResult(error=f"Error editing file '{file_path}': {error}")

        dashboard_path = (self.cwd / _EDIT_REJECTED_FILE_NAME).resolve()
        if resolved_path == dashboard_path:
            return EditResult(error=DASHBOARD_EDIT_REJECTED_MESSAGE)

        return super().edit(file_path, old_string, new_string, replace_all)


def build_model() -> BaseChatModel:
    return DeepAgentsRuntime().build_model()


def build_agent(
    model: BaseChatModel,
    connection: DuckDBPyConnection,
    workspace: SessionWorkspace,
    staged_skill_paths: list[str],
    recorder: ToolResultRecorder,
) -> CompiledStateGraph:
    return DeepAgentsRuntime().build_agent(
        model=model,
        tools=build_data_tools(connection, workspace, recorder),
        system_prompt=SYSTEM_PROMPT,
        # virtual_mode=True pins file tools to the session workspace root and rejects `../`
        # escapes after normalization: `..`/`~` raise ValueError before any I/O, absolute
        # paths are re-anchored inside root_dir. virtual_mode=False provides no confinement
        # -- see tests/test_filesystem_jail.py.
        backend=DashboardOverwriteBackend(root_dir=str(workspace.root), virtual_mode=True),
        skills=staged_skill_paths,
        checkpointer=session_state.checkpointer,
        # 一次只跑一個 tool call——deepagents 的檔案工具是無鎖讀改寫，併發會靜默互相覆蓋。
        # 每次 model call 重建 wiring manifest——qN 綁定不能只靠對話記憶。dashboard.html
        # 未讀過 skill 前擋寫——thread 內沒讀過 SKILL.md + examples.md 就退貨。
        middleware=[
            SerializedToolCallsMiddleware(),
            WiringManifestMiddleware(workspace),
            DashboardSkillGateMiddleware(workspace),
        ],
    )
