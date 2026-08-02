"""deepagents assembly -- wires the chat model, data tools, staged skills, and workspace
filesystem backend into a compiled LangGraph graph the event layer drives via
`astream_events`."""

import os

from deepagents import create_deep_agent
from deepagents.backends.filesystem import FilesystemBackend
from deepagents.backends.protocol import WriteResult
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

# The one file iteration turns are allowed to wholesale-rewrite via write_file. See
# `DashboardOverwriteBackend` docstring for why.
_OVERWRITABLE_FILE_NAME = "dashboard.html"

# 關掉 create_deep_agent 自動掛的 general-purpose subagent(不留 task 工具)——它曾委派
# 「用 Python 算迴歸」給自己,呼叫 write_file 寫 .py 腳本卻沒有任何執行機制,繞了好幾分鐘
# 才自己改用 SQL。key="openai" 對應這裡唯一會建的模型類別 ChatOpenAI(已驗證)。
register_harness_profile(
    "openai",
    HarnessProfile(general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)),
)


class DashboardOverwriteBackend(FilesystemBackend):
    """`FilesystemBackend` with exactly one hole punched in its create-only `write()`:
    `dashboard.html` at the workspace root.

    `FilesystemBackend.write()` is create-only by default -- good for source docs/query
    files, but it blocks a wholesale dashboard rewrite once dashboard.html already exists
    (the model's `write_file` gets rejected with no recovery path).

    Only the resolved path for `dashboard.html` under `root_dir` gets unlinked before
    deferring to the parent's normal create-only `write()`. Every other path, including
    traversal attempts already rejected by `_resolve_path`, goes through unmodified.
    """

    def write(self, file_path: str, content: str) -> WriteResult:
        try:
            resolved_path = self._resolve_path(file_path)
        except (OSError, RuntimeError) as error:
            return WriteResult(error=f"Error writing file '{file_path}': {error}")

        dashboard_path = (self.cwd / _OVERWRITABLE_FILE_NAME).resolve()
        if resolved_path == dashboard_path and resolved_path.exists():
            resolved_path.unlink()

        return super().write(file_path, content)


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
