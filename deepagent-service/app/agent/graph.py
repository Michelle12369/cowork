"""deepagents assembly -- wires the chat model, data tools, staged skills, and workspace
filesystem backend into a compiled LangGraph graph the event layer drives via
`astream_events`."""

import threading

from deepagents.backends.filesystem import FilesystemBackend
from deepagents.backends.protocol import WriteResult
from deepagents.profiles import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    register_harness_profile,
)
from duckdb import DuckDBPyConnection
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph

from app.agent import session_state
from app.agent.middleware import (
    DashboardSkillGateMiddleware,
    SerializedToolCallsMiddleware,
    WiringManifestMiddleware,
)
from app.agent.prompts import SYSTEM_PROMPT
from app.agent.runtime import load_runtime
from app.agent.tools.data import build_data_tools
from app.agent.tools.recording import ToolResultRecorder
from app.engine.workspace import SessionWorkspace

# write() 允許整份覆寫的檔案集合:dashboard.html 與記錄用的 notes.md。
_OVERWRITABLE_FILE_NAMES = frozenset({"dashboard.html", "notes.md"})

# 關掉 general-purpose subagent:它曾委派子任務「用 Python 算迴歸」給自己,寫了 .py 腳本卻
# 沒有執行機制,繞了好幾分鐘才改用 SQL。key="openai" 對應這裡唯一會建的模型類別 ChatOpenAI。
register_harness_profile(
    "openai",
    HarnessProfile(general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)),
)


class DashboardOverwriteBackend(FilesystemBackend):
    """dashboard.html/notes.md 可整份覆寫:parent 預設 create-only 會擋掉已存在檔案的
    write,故先 unlink `_OVERWRITABLE_FILE_NAMES` 再委派。局部編輯走 parent 的 edit()
    (edit_file 已重新開放,大改動改用 write_file 由 prompt 引導)。"""

    def write(self, file_path: str, content: str) -> WriteResult:
        try:
            resolved_path = self._resolve_path(file_path)
        except (OSError, RuntimeError) as error:
            return WriteResult(error=f"Error writing file '{file_path}': {error}")

        overwritable_paths = {(self.cwd / name).resolve() for name in _OVERWRITABLE_FILE_NAMES}
        if resolved_path in overwritable_paths and resolved_path.exists():
            resolved_path.unlink()

        return super().write(file_path, content)


def build_model() -> BaseChatModel:
    return load_runtime().build_model()


def build_agent(
    model: BaseChatModel,
    connection: DuckDBPyConnection,
    workspace: SessionWorkspace,
    staged_skill_paths: list[str],
    recorder: ToolResultRecorder,
    extra_tools: list[BaseTool] | None = None,
    connection_lock: "threading.Lock | None" = None,
) -> CompiledStateGraph:
    # extra_tools 由呼叫端(connector 模式的 ChatTurn)在選定 connector 時提供
    # (`app.agent.connectors.wrapper.build_connector_tools`)——未選 connector 時為 None,
    # tools 清單與舊行為 byte 不變。
    #
    # connection_lock 同理由呼叫端提供:connector 模式下 ChatTurn 建一把鎖同時交給
    # `build_connector_tools` 與這裡的 `build_data_tools`——同一個 DuckDB connection
    # 只能有一把鎖守門(見 `app.agent.tools.data`/`app.engine.api_snapshot` 模組
    # docstring),未提供時 `build_data_tools` 照舊自建,檔案模式行為不變。型別標註用字串
    # (forward reference)——`threading.Lock` 是 factory function 不是 class,
    # `threading.Lock | None` 這個 union 運算式在函式定義當下就會被求值,factory function
    # 沒有 `__or__` 會直接 TypeError,quoting 讓它留在字串型別不被求值。
    tools = build_data_tools(connection, workspace, recorder, connection_lock=connection_lock)
    if extra_tools:
        tools = [*tools, *extra_tools]
    return load_runtime().build_agent(
        model=model,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        # virtual_mode=True pins file tools to the session workspace root and rejects `../`
        # escapes after normalization: `..`/`~` raise ValueError before any I/O, absolute
        # paths are re-anchored inside root_dir. virtual_mode=False provides no confinement
        # -- see tests/test_filesystem_jail.py.
        backend=DashboardOverwriteBackend(root_dir=str(workspace.root), virtual_mode=True),
        skills=staged_skill_paths,
        checkpointer=session_state.checkpointer,
        # 一次只跑一個 tool call——deepagents 的檔案工具是無鎖讀改寫，併發會靜默互相覆蓋。
        # 每次 model call 重建 wiring manifest——qN 綁定不能只靠對話記憶。dashboard.html 只能
        # 用 write_file(擋 edit_file)，且未讀過 skill 前擋寫(thread 內沒讀過 SKILL.md 就退貨)。
        middleware=[
            SerializedToolCallsMiddleware(),
            WiringManifestMiddleware(workspace),
            # DashboardWriteFileOnlyMiddleware(),
            DashboardSkillGateMiddleware(workspace),
        ],
    )
