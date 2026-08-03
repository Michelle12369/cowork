"""deepagents assembly -- wires the chat model, data tools, staged skills, and workspace
filesystem backend into a compiled LangGraph graph the event layer drives via
`astream_events`."""

import os
import uuid
from pathlib import Path

from deepagents import create_deep_agent
from deepagents.backends.filesystem import FilesystemBackend
from deepagents.backends.protocol import EditResult, WriteResult
from deepagents.profiles import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    register_harness_profile,
)
from duckdb import DuckDBPyConnection
from langchain_openai import ChatOpenAI
from langgraph.graph.state import CompiledStateGraph

from app.agent import session_state
from app.agent.auth import token_exchange_http_clients
from app.agent.middleware import (
    DashboardSkillGateMiddleware,
    SerializedToolCallsMiddleware,
    WiringManifestMiddleware,
)
from app.agent.prompts import SYSTEM_PROMPT
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
            return self._atomic_overwrite(file_path, resolved_path, content)

        return super().write(file_path, content)

    def _atomic_overwrite(self, file_path: str, resolved_path: Path, content: str) -> WriteResult:
        """dashboard.html/notes.md 的整份覆寫改走「temp file 同目錄寫入 → os.replace 原子改名」
        ——parent write() 是 create-only,舊版先 unlink 既有檔案再 super().write(),寫入中途失敗
        (磁碟滿、UnicodeEncodeError 等)就會留下「舊檔已刪、新檔沒寫成」的狀態,而這是**每次**
        dashboard 修改都會走的路徑,不是罕見 case。os.replace 在同一個檔案系統內是原子操作:
        新內容先完整落地到 temp path,只有成功才會頂替原檔;寫入中途失敗時原檔完全不受影響。
        temp file 的 open flags/mode/encoding 對齊 parent `FilesystemBackend.write()`(見該檔案)
        ——O_NOFOLLOW 防 symlink、0o644、`utf-8`/`newline=""`——確保這條路徑寫出的位元組與
        parent 寫出的完全一致,不是另一套行為。
        """
        temp_path = resolved_path.with_name(f".{resolved_path.name}.tmp-{uuid.uuid4().hex}")
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            file_descriptor = os.open(temp_path, flags, 0o644)
            with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="") as temp_file:
                temp_file.write(content)
            os.replace(temp_path, resolved_path)
        except (OSError, UnicodeEncodeError) as error:
            temp_path.unlink(missing_ok=True)
            return WriteResult(error=f"Error writing file '{file_path}': {error}")

        return WriteResult(path=file_path)

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


def build_model() -> ChatOpenAI:
    # 單次呼叫 output 上限(reasoning+正文+tool args);太低會切斷整份 dashboard 的單次寫入,
    # 0=交給 provider 預設。
    max_tokens_setting = int(os.environ.get("AGENT_MAX_TOKENS", "32768"))
    # reasoning 獨立預算(OpenRouter reasoning.max_tokens):把思考封頂,避免整個 output 預算
    # 燒在思考裡而正文歸零。0 = 不送 reasoning 參數,交給 provider 預設。
    reasoning_budget = int(os.environ.get("AGENT_REASONING_MAX_TOKENS", "8192"))
    extra_body: dict = {}
    if reasoning_budget > 0:
        extra_body["reasoning"] = {"max_tokens": reasoning_budget}
    # OpenRouter 供應商路由:sort=throughput 挑最快、ignore 排除黑名單;都不設=交給
    # OpenRouter 預設路由。
    provider_routing: dict = {}
    provider_sort = os.environ.get("AGENT_PROVIDER_SORT", "").strip()
    if provider_sort:
        provider_routing["sort"] = provider_sort
    provider_ignore = [
        name.strip()
        for name in os.environ.get("AGENT_PROVIDER_IGNORE", "").split(",")
        if name.strip()
    ]
    if provider_ignore:
        provider_routing["ignore"] = provider_ignore
    if provider_routing:
        extra_body["provider"] = provider_routing
    # 公司環境 AGENT_AUTH_MODE=token-exchange 時走自帶 client(j1→j2 交換＋401 重試,
    # 見 app.agent.auth);bearer 模式兩者為 None,SDK 用預設 client。
    sync_http_client, async_http_client = token_exchange_http_clients()
    return ChatOpenAI(
        model=os.environ.get("AGENT_MODEL", "qwen3.6-35b"),
        base_url=os.environ.get("OPENAI_BASE_URL") or None,
        api_key=os.environ.get("OPENAI_API_KEY", "unused"),
        streaming=True,
        temperature=0,
        max_tokens=max_tokens_setting if max_tokens_setting > 0 else None,
        extra_body=extra_body or None,
        http_client=sync_http_client,
        http_async_client=async_http_client,
    )


def build_agent(
    model: ChatOpenAI,
    connection: DuckDBPyConnection,
    workspace: SessionWorkspace,
    staged_skill_paths: list[str],
    recorder: ToolResultRecorder,
) -> CompiledStateGraph:
    return create_deep_agent(
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
