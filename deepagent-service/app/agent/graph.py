"""deepagents assembly -- wires the chat model, data tools, staged skills, and workspace
filesystem backend into a compiled LangGraph graph the event layer drives via
`astream_events`."""

import os

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

# 檔案一律整份重寫(single-write 補強):write() 的 overwrite 洞放行的檔案集合,涵蓋
# dashboard.html 與記錄用的 notes.md。
_OVERWRITABLE_FILE_NAMES = frozenset({"dashboard.html", "notes.md"})

# edit() 仍確定性退貨的檔案——只有 dashboard.html:defense-in-depth,edit_file 已從模型可見
# 工具移除(見下方 excluded_tools),但 ToolNode 端工具仍註冊,模型仍可能幻覺呼叫。
_EDIT_REJECTED_FILE_NAME = "dashboard.html"

# dashboard.html 的 edit_file 確定性退貨訊息——錯誤訊息即行為指令,模型看到後改走單次
# write_file 整份重寫(single-write 實驗的核心不變量)。
DASHBOARD_EDIT_REJECTED_MESSAGE = (
    "dashboard.html must NOT be edited in place. Rewrite it in full instead: finish all "
    "run_sql data gathering first, then produce the complete corrected HTML with a single "
    "write_file call (overwriting dashboard.html is allowed)."
)

# 關掉 create_deep_agent 自動掛的 general-purpose subagent(不留 task 工具)——它曾委派
# 「用 Python 算迴歸」給自己,呼叫 write_file 寫 .py 腳本卻沒有任何執行機制,繞了好幾分鐘
# 才自己改用 SQL。key="openai" 對應這裡唯一會建的模型類別 ChatOpenAI(已驗證)。
# excluded_tools 移除 edit_file 的模型可見 schema(single-write 補強):實測顯示模型會無視
# edit() 的退貨訊息陷入 read→edit→退貨循環直到 recursion limit;deepagents 的
# `_ToolExclusionMiddleware` 在每次 model call 前物理剝除該工具,不依賴模型遵從退貨教育。
register_harness_profile(
    "openai",
    HarnessProfile(
        general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
        excluded_tools=frozenset({"edit_file"}),
    ),
)


class DashboardOverwriteBackend(FilesystemBackend):
    """`FilesystemBackend` with two special cases at the workspace root: `write()` is
    allowed to wholesale-overwrite `dashboard.html` and `notes.md`; `edit()` on
    `dashboard.html` is always rejected in favor of that same wholesale rewrite.

    `FilesystemBackend.write()` is create-only by default -- good for source docs/query
    files, but it blocks a wholesale rewrite of an already-written file (the model's
    `write_file` gets rejected with no recovery path). Only the resolved paths in
    `_OVERWRITABLE_FILE_NAMES` under `root_dir` get unlinked before deferring to the
    parent's normal create-only `write()`. Every other path, including traversal attempts
    already rejected by `_resolve_path`, goes through unmodified.

    `edit()` on `dashboard.html` is rejected outright with
    `DASHBOARD_EDIT_REJECTED_MESSAGE` -- the single-write invariant is that every
    dashboard.html change is a full rewrite via `write()`, never a targeted patch. This is
    now defense-in-depth: `edit_file` is also removed from the model's visible tool
    schema via the "openai" harness profile's `excluded_tools`, so a well-behaved model
    never attempts the call in the first place. `notes.md` stays editable at this layer
    (only its overwrite-on-write behavior changed) and every other path is unaffected.
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
