# 公司環境接縫與單向同步 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓同一份程式碼能同時活在 GitHub（家裡）與公司內網，且公司側對共用檔零編輯——把所有必須分歧的地方轉成接縫，並提供一支可重複、可稽核的單向同步腳本。

**Architecture:** 三個接縫皆為「家裡放預設實作、公司放替換實作」：deepagent-service 以 `AgentRuntime` Protocol 抽出 model／checkpointer／agent 三個建構點；frontend 以 `import.meta.glob` 偵測不存在即 no-op 的 bootstrap 檔，並以 Vite plugin 依 env 注入公司 script。同步採 replace-then-restore（`git read-tree --reset` 換成上游後還原公司獨佔路徑），落在 feature branch 上供人工適配後發 PR。

**Tech Stack:** Python 3.11 / FastAPI / LangGraph / deepagents 0.5.5 / pytest；React 18 / TypeScript / Vite / Vitest；Bash / git。

## Global Constraints

- Python 端變數命名 NEVER 用 1–2 字元；迴圈計數器用 `index`／`rowIndex` 等描述性名稱。
- `app/engine/**` 受 ruff `banned-api` 規則禁止 import langchain／langgraph／deepagents；`app/agent/**` 與 `tests/**` 已在 `per-file-ignores` 豁免，本計畫新增的檔案全部位於 `app/agent/runtime/`，在豁免範圍內。
- 前端：TypeScript 嚴格模式，NEVER 使用 `any`；function MUST 有明確 return type；type-only import MUST 用 `import type`。
- 前端測試用 Vitest + React Testing Library，斷言元素級行為；fetch mock 用 `vi.stubGlobal`。
- 測試方法命名 `methodName_condition_expectedBehavior`（Python 端沿用既有 `test_<行為>` 風格即可，與現有檔案一致）。
- 註解寫「目的＋做法」1–2 行；NEVER 寫 spec 編號、commit hash、事故敘事。
- 家裡的 `backend/pom.xml`、`backend/src/main/resources/application.yml` 本計畫**一行都不改**。
- 公司獨佔路徑 NEVER 加進 `.gitignore`（公司必須能 commit 它們到 `develop`，同步時才撈得回來）。
- 每個 task 結束前 MUST 跑完該側測試：`cd deepagent-service && uv run pytest` 或 `cd frontend && npm test`。

---

### Task 1: 抽出 `AgentRuntime` Protocol 與 `DeepAgentsRuntime`

把 `graph.py` 裡的 model／agent 建構搬進 runtime 實作，`graph.py` 僅保留 backend 類別與委派。**行為必須完全不變**——既有 `tests/test_graph.py`、`tests/test_chat.py` 全綠即為驗收。

**Files:**
- Create: `deepagent-service/app/agent/runtime/__init__.py`
- Create: `deepagent-service/app/agent/runtime/base.py`
- Create: `deepagent-service/app/agent/runtime/deepagents_runtime.py`
- Modify: `deepagent-service/app/agent/graph.py`
- Test: `deepagent-service/tests/test_runtime.py`

**Interfaces:**
- Consumes: 既有 `app.agent.graph.DashboardOverwriteBackend`、`app.agent.tools.data.build_data_tools`、`app.agent.prompts.SYSTEM_PROMPT`。
- Produces:
  - `app.agent.runtime.base.AgentRuntime`（Protocol，三個方法簽名見下）
  - `app.agent.runtime.deepagents_runtime.DeepAgentsRuntime`（無建構參數）
  - `app.agent.graph.build_model()` 與 `build_agent(model, connection, workspace, staged_skill_paths, recorder)` **簽名不變**，供 `chat_turn.py` 繼續使用

- [ ] **Step 1: 寫失敗測試**

建立 `deepagent-service/tests/test_runtime.py`：

```python
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver

from app.agent.runtime.deepagents_runtime import DeepAgentsRuntime


def test_deepagents_runtime_builds_model(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "unused")
    assert isinstance(DeepAgentsRuntime().build_model(), BaseChatModel)


def test_deepagents_runtime_builds_checkpointer() -> None:
    checkpointer = DeepAgentsRuntime().build_checkpointer()
    assert isinstance(checkpointer, BaseCheckpointSaver)
    # 每次呼叫 MUST 是新實例——reset_for_tests() 靠這點清掉跨測試殘留的 thread 歷史。
    assert checkpointer is not DeepAgentsRuntime().build_checkpointer()
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd deepagent-service && uv run pytest tests/test_runtime.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.agent.runtime'`

- [ ] **Step 3: 建立 Protocol**

建立 `deepagent-service/app/agent/runtime/__init__.py`（本 task 先留空，Task 2 才放選擇器）：

```python
```

建立 `deepagent-service/app/agent/runtime/base.py`：

```python
"""AgentRuntime -- agent 建構層的三個接縫點。公司環境以另一個實作整組替換 model、
checkpointer 與 agent 的建立方式；型別一律用 langchain/langgraph base type，因為公司 lib
是 langgraph wrapper，兩個實作天然滿足同一組簽名。"""

from typing import Any, Protocol

from deepagents.backends.filesystem import FilesystemBackend
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph


class AgentRuntime(Protocol):
    def build_model(self) -> BaseChatModel: ...

    def build_checkpointer(self) -> BaseCheckpointSaver: ...

    def build_agent(
        self,
        *,
        model: BaseChatModel,
        tools: list[Any],
        system_prompt: str,
        backend: FilesystemBackend,
        skills: list[str],
        checkpointer: BaseCheckpointSaver,
        middleware: list[Any],
    ) -> CompiledStateGraph: ...
```

- [ ] **Step 4: 建立 `DeepAgentsRuntime`**

建立 `deepagent-service/app/agent/runtime/deepagents_runtime.py`。`build_model` 的內容整段從 `graph.py:97-135` 搬過來（連同註解），`build_agent` 收下 `graph.py:145-164` 的 `create_deep_agent(...)` 呼叫：

```python
"""家裡的 AgentRuntime 實作：deepagents + ChatOpenAI + 記憶體 checkpointer。"""

import os
from typing import Any

from deepagents import create_deep_agent
from deepagents.backends.filesystem import FilesystemBackend
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph

from app.agent.auth import token_exchange_http_clients


class DeepAgentsRuntime:
    def build_model(self) -> BaseChatModel:
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

    def build_checkpointer(self) -> BaseCheckpointSaver:
        return InMemorySaver()

    def build_agent(
        self,
        *,
        model: BaseChatModel,
        tools: list[Any],
        system_prompt: str,
        backend: FilesystemBackend,
        skills: list[str],
        checkpointer: BaseCheckpointSaver,
        middleware: list[Any],
    ) -> CompiledStateGraph:
        return create_deep_agent(
            model=model,
            tools=tools,
            system_prompt=system_prompt,
            backend=backend,
            skills=skills,
            checkpointer=checkpointer,
            middleware=middleware,
        )
```

- [ ] **Step 5: 跑測試確認通過**

Run: `cd deepagent-service && uv run pytest tests/test_runtime.py -v`
Expected: PASS（2 passed）

- [ ] **Step 6: 讓 `graph.py` 改為委派**

編輯 `deepagent-service/app/agent/graph.py`：

1. 刪除 `import os`、`from deepagents import create_deep_agent`、`from langchain_openai import ChatOpenAI`、`from app.agent.auth import token_exchange_http_clients` 這四行 import。
2. 新增 `from app.agent.runtime.deepagents_runtime import DeepAgentsRuntime`（Task 2 會換成選擇器）。
3. 保留 `register_harness_profile(...)`、`DashboardOverwriteBackend`、`_OVERWRITABLE_FILE_NAMES`、`DASHBOARD_EDIT_REJECTED_MESSAGE` 全部不動。
4. 把 `build_model()` 與 `build_agent(...)` 換成：

```python
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
```

5. `build_model` 的回傳型別由 `ChatOpenAI` 改為 `BaseChatModel`，並在檔案頂端加 `from langchain_core.language_models import BaseChatModel`。

- [ ] **Step 7: 跑全部 Python 測試確認行為未變**

Run: `cd deepagent-service && uv run pytest`
Expected: PASS（全數綠燈，含 `test_graph.py`、`test_chat.py`）

- [ ] **Step 8: Commit**

```bash
git add deepagent-service/app/agent/runtime/ deepagent-service/app/agent/graph.py deepagent-service/tests/test_runtime.py
git commit -m "refactor(deepagent): 抽出 AgentRuntime 與 DeepAgentsRuntime"
```

---

### Task 2: runtime 選擇器與 checkpointer 接線

讓 `AGENT_RUNTIME` 決定實作，並把 `session_state` 的 checkpointer 改由 runtime 提供。設為 `internal` 而實作檔不存在時 MUST 啟動即失敗。

**Files:**
- Modify: `deepagent-service/app/agent/runtime/__init__.py`
- Modify: `deepagent-service/app/agent/graph.py`
- Modify: `deepagent-service/app/agent/session_state.py`
- Modify: `.env.example`
- Test: `deepagent-service/tests/test_runtime.py`

**Interfaces:**
- Consumes: Task 1 的 `AgentRuntime`、`DeepAgentsRuntime`。
- Produces: `app.agent.runtime.load_runtime() -> AgentRuntime`（附 `load_runtime.cache_clear()`，測試用）。

- [ ] **Step 1: 寫失敗測試**

在 `deepagent-service/tests/test_runtime.py` 末尾追加：

```python
import pytest

from app.agent.runtime import load_runtime
from app.agent.runtime.deepagents_runtime import DeepAgentsRuntime


@pytest.fixture(autouse=True)
def _clear_runtime_cache():
    load_runtime.cache_clear()
    yield
    load_runtime.cache_clear()


def test_load_runtime_defaults_to_deepagents(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_RUNTIME", raising=False)
    assert isinstance(load_runtime(), DeepAgentsRuntime)


def test_load_runtime_internal_without_impl_raises_with_module_name(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_RUNTIME", "internal")
    with pytest.raises(RuntimeError) as error:
        load_runtime()
    # 訊息 MUST 指出缺哪個模組，否則公司端只會看到一句無資訊的啟動失敗。
    assert "app.agent.runtime.internal_runtime" in str(error.value)


def test_load_runtime_unknown_value_raises(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_RUNTIME", "nope")
    with pytest.raises(RuntimeError) as error:
        load_runtime()
    assert "nope" in str(error.value)
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd deepagent-service && uv run pytest tests/test_runtime.py -v`
Expected: FAIL with `ImportError: cannot import name 'load_runtime'`

- [ ] **Step 3: 實作選擇器**

寫入 `deepagent-service/app/agent/runtime/__init__.py`：

```python
"""以 AGENT_RUNTIME 選擇 agent 建構層實作。internal 實作只存在於公司環境，
找不到時 MUST 啟動即失敗——靜默 fallback 回 deepagents 會讓公司跑在錯誤的 runtime 上而無人察覺。"""

import importlib
import os
from functools import lru_cache

from app.agent.runtime.base import AgentRuntime

_RUNTIME_TARGETS = {
    "deepagents": ("app.agent.runtime.deepagents_runtime", "DeepAgentsRuntime"),
    "internal": ("app.agent.runtime.internal_runtime", "InternalRuntime"),
}


@lru_cache(maxsize=1)
def load_runtime() -> AgentRuntime:
    runtimeName = os.environ.get("AGENT_RUNTIME", "deepagents")
    target = _RUNTIME_TARGETS.get(runtimeName)
    if target is None:
        raise RuntimeError(
            f"AGENT_RUNTIME={runtimeName!r} 無效；可選 {sorted(_RUNTIME_TARGETS)}"
        )
    modulePath, className = target
    try:
        module = importlib.import_module(modulePath)
    except ModuleNotFoundError as error:
        # 缺的若是實作檔本身才是「公司未提供實作」；缺的是它的依賴時原始錯誤更有用，直接放行。
        if error.name != modulePath:
            raise
        raise RuntimeError(
            f"AGENT_RUNTIME={runtimeName} 但找不到 {modulePath}；"
            "公司環境 MUST 提供該實作檔，NEVER fallback 回 deepagents。"
        ) from error
    return getattr(module, className)()
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd deepagent-service && uv run pytest tests/test_runtime.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: 把 `graph.py` 與 `session_state.py` 接到選擇器**

`deepagent-service/app/agent/graph.py`：把 `from app.agent.runtime.deepagents_runtime import DeepAgentsRuntime` 改成 `from app.agent.runtime import load_runtime`，並把兩處 `DeepAgentsRuntime()` 改成 `load_runtime()`。

`deepagent-service/app/agent/session_state.py`：把 `from langgraph.checkpoint.memory import InMemorySaver` 換成 `from app.agent.runtime import load_runtime`，並改兩處建構：

```python
# checkpointer 由 runtime 提供——公司環境可換成自家實作而不動本檔。
checkpointer = load_runtime().build_checkpointer()


def reset_for_tests() -> None:
    """Test-only: an autouse fixture calls this before/after every test so sessionId reuse
    across unrelated tests never leaks checkpointed history. Production code MUST NOT call
    this -- state is expected to persist for the process lifetime (see module docstring)."""
    global checkpointer
    checkpointer = load_runtime().build_checkpointer()
```

- [ ] **Step 6: 跑全部 Python 測試**

Run: `cd deepagent-service && uv run pytest`
Expected: PASS（全數綠燈）

- [ ] **Step 7: 記錄新環境變數**

在 `.env.example` 的 `[B] deep agent 線` 區塊末尾加入：

```bash
# agent 建構層實作。deepagents（預設）＝本專案；internal＝公司內部 langgraph wrapper，
# MUST 另外提供 app/agent/runtime/internal_runtime.py，找不到時啟動即失敗（刻意不 fallback）。
# AGENT_RUNTIME=deepagents
```

- [ ] **Step 8: Commit**

```bash
git add deepagent-service/app/agent/runtime/__init__.py deepagent-service/app/agent/graph.py deepagent-service/app/agent/session_state.py deepagent-service/tests/test_runtime.py .env.example
git commit -m "feat(deepagent): AGENT_RUNTIME 選擇器與 checkpointer 接線"
```

---

### Task 3: `apiClient` 的 `setUserId` 擴充點

公司 SSO 接縫需要覆寫 `X-User-Id`。開一個具名 export，讓耦合有型別、改名時會編譯失敗。

**Files:**
- Modify: `frontend/src/api/apiClient.ts`
- Test: `frontend/src/api/apiClient.test.ts`

**Interfaces:**
- Produces: `setUserId(userId: string): void`（來自 `@/api/apiClient`），Task 4 的範例與公司實作會用到。

- [ ] **Step 1: 寫失敗測試**

建立 `frontend/src/api/apiClient.test.ts`：

```typescript
import type { InternalAxiosRequestConfig } from 'axios';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { apiClient, getUserId, setUserId } from './apiClient';
import { streamAgentMessage } from './agentApi';

describe('setUserId', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('setUserId_thenGetUserId_returnsSameValue', () => {
    setUserId('sso-user-1');
    expect(getUserId()).toBe('sso-user-1');
  });

  it('setUserId_axiosRequest_carriesNewIdHeader', async () => {
    setUserId('sso-user-2');
    let captured: InternalAxiosRequestConfig | undefined;
    // 用 adapter 攔截：走完整的 interceptor 鏈，但不發出真實請求。
    apiClient.defaults.adapter = async (config) => {
      captured = config;
      return { data: {}, status: 200, statusText: 'OK', headers: {}, config };
    };

    await apiClient.get('/config');

    expect(captured?.headers['X-User-Id']).toBe('sso-user-2');
  });

  it('setUserId_agentStreamFetch_carriesNewIdHeader', async () => {
    setUserId('sso-user-3');
    // body: null 讓 streamAgentMessage 在建立 reader 前就結束，只驗證送出的 header。
    const mockFetch = vi.fn().mockResolvedValue({ ok: true, body: null });
    vi.stubGlobal('fetch', mockFetch);

    const stream = streamAgentMessage({
      sessionId: 'session-1',
      question: 'hello',
      signal: new AbortController().signal,
    });
    await stream.next();

    const requestInit = mockFetch.mock.calls[0][1] as { headers: Record<string, string> };
    expect(requestInit.headers['X-User-Id']).toBe('sso-user-3');
    vi.unstubAllGlobals();
  });
});
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && npx vitest run src/api/apiClient.test.ts`
Expected: FAIL with `"setUserId" is not exported by "src/api/apiClient.ts"`

- [ ] **Step 3: 加上 export**

在 `frontend/src/api/apiClient.ts` 的 `getUserId` 之後插入：

```typescript
/** 覆寫目前使用者 id。公司環境的 SSO 接縫用它取代匿名 UUID（見 bootstrap/internal.ts）；
 *  家裡沒有呼叫端。具名 export 是為了讓耦合顯性化——直接硬寫 localStorage key 的話，
 *  key 改名時公司端不會編譯錯誤，只會安靜退回匿名身分。 */
export function setUserId(userId: string): void {
  localStorage.setItem(USER_KEY, userId);
}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd frontend && npx vitest run src/api/apiClient.test.ts`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/apiClient.ts frontend/src/api/apiClient.test.ts
git commit -m "feat(frontend): 開放 setUserId 作為公司 SSO 接縫的擴充點"
```

---

### Task 4: frontend bootstrap 接縫

`main.tsx` 在 mount 前呼叫一個接縫；家裡沒有實作檔時是 no-op。

**Files:**
- Create: `frontend/src/bootstrap/internal.ts`
- Modify: `frontend/src/main.tsx`
- Test: `frontend/src/bootstrap/internal.test.ts`

**Interfaces:**
- Consumes: Task 3 的 `setUserId`（僅公司實作會用，家裡不引用）。
- Produces: `initInternalRuntime(loaders?: Record<string, () => Promise<InternalBootstrap>>): Promise<void>`；`InternalBootstrap = { initialize: () => Promise<void> }`。

- [ ] **Step 1: 寫失敗測試**

建立 `frontend/src/bootstrap/internal.test.ts`：

```typescript
import { describe, expect, it, vi } from 'vitest';
import { initInternalRuntime } from './internal';

describe('initInternalRuntime', () => {
  it('initInternalRuntime_noImplFile_resolvesWithoutThrowing', async () => {
    // 家裡的真實狀態：internal.impl.ts 不存在 → glob 回傳空物件。
    await expect(initInternalRuntime()).resolves.toBeUndefined();
  });

  it('initInternalRuntime_implPresent_callsInitialize', async () => {
    const initialize = vi.fn().mockResolvedValue(undefined);
    await initInternalRuntime({ './internal.impl.ts': async () => ({ initialize }) });
    expect(initialize).toHaveBeenCalledOnce();
  });

  it('initInternalRuntime_implThrows_propagates', async () => {
    const initialize = vi.fn().mockRejectedValue(new Error('SSO 未載入'));
    await expect(
      initInternalRuntime({ './internal.impl.ts': async () => ({ initialize }) }),
    ).rejects.toThrow('SSO 未載入');
  });
});
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && npx vitest run src/bootstrap/internal.test.ts`
Expected: FAIL with `Failed to resolve import "./internal"`

- [ ] **Step 3: 實作接縫**

建立 `frontend/src/bootstrap/internal.ts`：

```typescript
export interface InternalBootstrap {
  initialize: () => Promise<void>;
}

// 公司初始化接縫：internal.impl.ts 只存在於公司環境。import.meta.glob 對不存在的檔案
// 回傳空物件而非 build error——這是本接縫在家裡能成立的原因。
const impls = import.meta.glob<InternalBootstrap>('./internal.impl.ts');

/** 公司環境的啟動初始化（例如 SSO 決定 X-User-Id）；家裡無實作檔時為 no-op。
 *  loaders 參數僅供測試注入，正式路徑一律走上面的 glob 結果。 */
export async function initInternalRuntime(
  loaders: Record<string, () => Promise<InternalBootstrap>> = impls,
): Promise<void> {
  const load = loaders['./internal.impl.ts'];
  if (!load) return;
  await (await load()).initialize();
}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd frontend && npx vitest run src/bootstrap/internal.test.ts`
Expected: PASS（3 passed）

- [ ] **Step 5: 接進 `main.tsx`**

編輯 `frontend/src/main.tsx`：加入 import 並把 `createRoot(...).render(...)` 包成函式，在接縫完成後才 mount：

```typescript
import { initInternalRuntime } from './bootstrap/internal';

function mountApp(): void {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <AntdApp>
          <App />
        </AntdApp>
      </QueryClientProvider>
    </StrictMode>,
  );
}

// 公司環境的初始化 MUST 在 mount 前完成（SSO 決定 X-User-Id）；家裡是 no-op 立即 resolve。
// 刻意不 catch：初始化失敗時讓 rejection 浮上 console 且不 mount，NEVER 以匿名身分繼續。
void initInternalRuntime().then(mountApp);
```

- [ ] **Step 6: 跑全部前端測試**

Run: `cd frontend && npm test`
Expected: PASS（全數綠燈）

- [ ] **Step 7: Commit**

```bash
git add frontend/src/bootstrap/ frontend/src/main.tsx
git commit -m "feat(frontend): 新增公司環境的 bootstrap 接縫"
```

---

### Task 5: Vite `internal-script` plugin

依 `VITE_INTERNAL_SCRIPT_URL` 注入公司 global script；未設時 `index.html` 產出必須與現況逐字元相同。

**Files:**
- Create: `frontend/src/vite/internalScriptPlugin.ts`
- Modify: `frontend/vite.config.ts`
- Modify: `.env.example`
- Test: `frontend/src/vite/internalScriptPlugin.test.ts`

**Interfaces:**
- Produces: `internalScriptPlugin(scriptUrl: string | undefined): Plugin`（來自 `@/vite/internalScriptPlugin`）。URL 由呼叫端傳入而非在 plugin 內讀 env，這樣測試不必動 `process.env`。

- [ ] **Step 1: 寫失敗測試**

建立 `frontend/src/vite/internalScriptPlugin.test.ts`：

```typescript
import { describe, expect, it } from 'vitest';
import { internalScriptPlugin } from './internalScriptPlugin';

function runTransform(scriptUrl: string | undefined): unknown {
  const transform = internalScriptPlugin(scriptUrl).transformIndexHtml;
  if (typeof transform !== 'function') throw new Error('transformIndexHtml 必須是函式');
  return transform('<html></html>', {
    path: '/index.html',
    filename: 'index.html',
  } as never);
}

describe('internalScriptPlugin', () => {
  it('internalScriptPlugin_urlUnset_injectsNothing', () => {
    expect(runTransform(undefined)).toEqual([]);
  });

  it('internalScriptPlugin_urlBlank_injectsNothing', () => {
    expect(runTransform('   ')).toEqual([]);
  });

  it('internalScriptPlugin_urlSet_injectsScriptIntoHead', () => {
    expect(runTransform('https://internal.example/sso.js')).toEqual([
      { tag: 'script', attrs: { src: 'https://internal.example/sso.js' }, injectTo: 'head' },
    ]);
  });
});
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && npx vitest run src/vite/internalScriptPlugin.test.ts`
Expected: FAIL with `Failed to resolve import "./internalScriptPlugin"`

- [ ] **Step 3: 實作 plugin**

建立 `frontend/src/vite/internalScriptPlugin.ts`：

```typescript
import type { Plugin } from 'vite';

/** 公司環境把內部 library 以 global script 掛進 index.html。URL 未設時不注入任何標籤，
 *  使家裡產出的 HTML 與加這個 plugin 之前逐字元相同。 */
export function internalScriptPlugin(scriptUrl: string | undefined): Plugin {
  const trimmedUrl = scriptUrl?.trim();
  return {
    name: 'internal-script',
    transformIndexHtml: () =>
      trimmedUrl ? [{ tag: 'script', attrs: { src: trimmedUrl }, injectTo: 'head' as const }] : [],
  };
}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd frontend && npx vitest run src/vite/internalScriptPlugin.test.ts`
Expected: PASS（3 passed）

- [ ] **Step 5: 接進 `vite.config.ts`**

編輯 `frontend/vite.config.ts`：

1. 加 import：`import { internalScriptPlugin } from './src/vite/internalScriptPlugin';`
2. 在 `backendUrl` 常數附近加：

```typescript
// 公司環境的內部 library 以 global script 注入（非 npm 套件，package.json 不受影響）。
// 沿用本檔既有風格從 process.env 讀取，與 BACKEND_URL / ALLOWED_HOSTS 一致。
const internalScriptUrl = process.env.VITE_INTERNAL_SCRIPT_URL;
```

3. `plugins` 改為：`plugins: [react(), tailwindcss(), internalScriptPlugin(internalScriptUrl)],`

- [ ] **Step 6: 驗證未設變數時 build 產出不變**

```bash
cd frontend && npm run build && grep -c "<script" dist/index.html
```
Expected: 只有 Vite 自己注入的 bundle script，**沒有** `internal` 相關標籤（數量與加 plugin 前相同）。

- [ ] **Step 7: 記錄新環境變數**

在 `.env.example` 的 `[0] 共用設定` 區塊末尾加入：

```bash
# 公司環境的前端內部 library（global script，非 npm 套件）。設了才會注入 index.html；
# 未設時前端產出與 dev 完全相同。VITE_INTERNAL_APP_ID 由 internal.impl.ts 傳給該 library。
# VITE_INTERNAL_SCRIPT_URL=https://internal.example/sso.js
# VITE_INTERNAL_APP_ID=cowork
#
# 公司環境的 backend profile：啟用 application-internal.yml（該檔僅存在於公司側）。
# SPRING_PROFILES_ACTIVE=internal
```

- [ ] **Step 8: 跑全部前端測試並 commit**

```bash
cd frontend && npm test
git add frontend/src/vite/ frontend/vite.config.ts .env.example
git commit -m "feat(frontend): 依 env 注入公司 global script 的 Vite plugin"
```

---

### Task 6: `requirements.txt` 漂移防護

`requirements.txt` 是公司環境的安裝來源，由 `uv.lock` 匯出。忘記重新匯出時家裡完全無感，只有公司會裝到舊依賴——用一支測試在家裡就攔下來。

**Files:**
- Test: `deepagent-service/tests/test_requirements_sync.py`

**Interfaces:**
- Consumes: 無。
- Produces: 無（純測試）。

- [ ] **Step 1: 寫測試**

建立 `deepagent-service/tests/test_requirements_sync.py`：

```python
"""requirements.txt 是給公司環境的安裝來源，由 uv.lock 匯出。漂移時家裡走
uv sync --frozen 完全無感，只有公司會裝到舊依賴——故在家裡的測試就攔下。"""

import shutil
import subprocess
from pathlib import Path

import pytest

SERVICE_ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS_PATH = SERVICE_ROOT / "requirements.txt"

EXPORT_COMMAND = [
    "uv",
    "export",
    "--no-dev",
    "--no-hashes",
    "--format",
    "requirements-txt",
]


def _package_lines(text: str) -> list[str]:
    """只留套件行。註解含當初的 export 指令字串，會因 -o 參數不同而有差異，與依賴內容無關。"""
    return [line for line in text.splitlines() if line and not line.lstrip().startswith("#")]


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv 不在 PATH")
def test_requirements_txt_matches_uv_lock() -> None:
    exported = subprocess.run(
        EXPORT_COMMAND, cwd=SERVICE_ROOT, capture_output=True, text=True, check=True
    ).stdout
    assert _package_lines(exported) == _package_lines(
        REQUIREMENTS_PATH.read_text(encoding="utf-8")
    ), (
        "requirements.txt 與 uv.lock 不同步，公司環境會裝到舊依賴。重新匯出："
        "uv export --no-dev --no-hashes --format requirements-txt -o requirements.txt"
    )
```

- [ ] **Step 2: 跑測試確認現況通過**

Run: `cd deepagent-service && uv run pytest tests/test_requirements_sync.py -v`
Expected: PASS（現有 `requirements.txt` 應與 lock 一致；若 FAIL 代表已經漂移了，先照錯誤訊息重新匯出並把 `requirements.txt` 一起 commit）

- [ ] **Step 3: 驗證測試真的會抓到漂移**

```bash
cd deepagent-service
printf 'zzz-not-a-real-package==1.0.0\n' >> requirements.txt
uv run pytest tests/test_requirements_sync.py -v   # Expected: FAIL
git checkout -- requirements.txt
uv run pytest tests/test_requirements_sync.py -v   # Expected: PASS
```

- [ ] **Step 4: Commit**

```bash
git add deepagent-service/tests/test_requirements_sync.py
git commit -m "test(deepagent): 攔截 requirements.txt 與 uv.lock 的漂移"
```

---

### Task 7: 同步腳本與獨佔路徑清單

`sync-upstream.sh` 由公司側執行，但**檔案本身住在共用 repo、由家裡維護**，所以測試也得在家裡跑得起來。

**Files:**
- Create: `scripts/internal-owned-paths.txt`
- Create: `scripts/manual-merge-paths.txt`
- Create: `scripts/sync-upstream.sh`
- Test: `scripts/test-sync-upstream.sh`

**Interfaces:**
- Consumes: 無。
- Produces: `scripts/sync-upstream.sh`（需在 Azure clone 的 `develop` 上執行，remote `gl`＝GitHub 鏡像、`origin`＝Azure）。

- [ ] **Step 1: 建立兩份清單**

`scripts/internal-owned-paths.txt`：

```
internal/
backend/pom.xml
backend/src/internal
backend/src/main/resources/application-internal.yml
frontend/src/bootstrap/internal.impl.ts
deepagent-service/app/agent/runtime/internal_runtime.py
```

`scripts/manual-merge-paths.txt`：

```
backend/pom.xml
```

- [ ] **Step 2: 寫失敗測試**

建立 `scripts/test-sync-upstream.sh`（`chmod +x`）。它建三個拋棄式 repo（上游裸 repo、origin 裸 repo、工作 clone），bootstrap 一顆同步 commit，再逐項驗證守門：

```bash
#!/usr/bin/env bash
# sync-upstream.sh 的守門驗證。每個情境 MUST 讓腳本以非零碼中止——守門是整個同步流程
# 唯一的安全裝置，誤放行等於靜默資料遺失。
set -uo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
WORK_ROOT=$(mktemp -d)
trap 'rm -rf "$WORK_ROOT"' EXIT
FAILURES=0

expect_abort() {
  local caseName=$1
  shift
  if (cd "$WORK_ROOT/clone" && "$@" >/dev/null 2>&1); then
    echo "FAIL: $caseName —— 腳本應中止卻成功了"
    FAILURES=$((FAILURES + 1))
  else
    echo "ok: $caseName"
  fi
}

setup() {
  rm -rf "$WORK_ROOT"/{upstream,origin,clone}
  git init -q --bare "$WORK_ROOT/upstream"
  git init -q --bare "$WORK_ROOT/origin"

  git clone -q "$WORK_ROOT/upstream" "$WORK_ROOT/seed"
  cd "$WORK_ROOT/seed"
  git config user.email t@t; git config user.name t
  mkdir -p scripts backend
  cp "$SCRIPT_DIR/sync-upstream.sh" scripts/
  cp "$SCRIPT_DIR/internal-owned-paths.txt" scripts/
  cp "$SCRIPT_DIR/manual-merge-paths.txt" scripts/
  echo "<project/>" > backend/pom.xml
  echo shared > shared.txt
  git add -A && git commit -qm "init"
  git push -q origin HEAD:master
  cd /

  git clone -q "$WORK_ROOT/origin" "$WORK_ROOT/clone"
  cd "$WORK_ROOT/clone"
  git config user.email t@t; git config user.name t
  git remote add gl "$WORK_ROOT/upstream"
  git fetch -q gl
  git checkout -qb develop gl/master
  # bootstrap：第一顆同步 commit，之後的基準點由它提供。
  git commit -q --allow-empty -m "upstream-sync: bootstrap" \
    -m "Upstream-Commit: $(git rev-parse gl/master)"
  git push -q -u origin develop
  cd /
}

# 情境 ①：獨佔清單外有公司改動
setup
cd "$WORK_ROOT/clone" && echo tampered > shared.txt && git commit -qam "越界" && git push -q origin develop && cd /
expect_abort "① 獨佔清單外有公司改動" bash scripts/sync-upstream.sh

# 情境 ②：有野生 untracked 檔
setup
cd "$WORK_ROOT/clone" && echo stray > stray.txt && cd /
expect_abort "② 有野生 untracked 檔" bash scripts/sync-upstream.sh

# 情境 ③：不在 develop 上
setup
cd "$WORK_ROOT/clone" && git checkout -qb feature/x && cd /
expect_abort "③ 不在 develop 上" bash scripts/sync-upstream.sh

# 情境 ④：找不到基準同步 commit（未 bootstrap）
setup
cd "$WORK_ROOT/clone" && git checkout -qB develop gl/master && git push -qf origin develop && cd /
expect_abort "④ 未 bootstrap（無基準同步 commit）" bash scripts/sync-upstream.sh

expect_note() {
  local caseName=$1 shouldContain=$2
  local body
  body=$(cd "$WORK_ROOT/clone" && git log -1 --format=%B)
  if [ "$shouldContain" = yes ] && ! grep -q "需人工調和：backend/pom.xml" <<<"$body"; then
    echo "FAIL: $caseName —— commit body 少了待辦行"; FAILURES=$((FAILURES + 1)); return
  fi
  if [ "$shouldContain" = no ] && grep -q "需人工調和" <<<"$body"; then
    echo "FAIL: $caseName —— commit body 不該有待辦行"; FAILURES=$((FAILURES + 1)); return
  fi
  echo "ok: $caseName"
}

# 情境 ⑤：上游動過 pom.xml → commit body MUST 列出待辦
setup
cd "$WORK_ROOT/seed" && echo "<project><!--changed--></project>" > backend/pom.xml \
  && git commit -qam "上游改 pom" && git push -q origin HEAD:master && cd /
(cd "$WORK_ROOT/clone" && bash scripts/sync-upstream.sh >/dev/null 2>&1)
expect_note "⑤ 上游動過 pom.xml → 列出待辦" yes

# 情境 ⑥：錨點回歸——上游未動 pom 時不得列出待辦（用 $LAST_SYNC 當錨點會誤報）
setup
cd "$WORK_ROOT/seed" && echo other > other.txt && git add -A && git commit -qm "上游改別的" \
  && git push -q origin HEAD:master && cd /
(cd "$WORK_ROOT/clone" && bash scripts/sync-upstream.sh >/dev/null 2>&1)
expect_note "⑥ 上游未動 pom.xml → 不列待辦" no

# 情境 ⑦：基準點取自 origin/develop——同步 branch 已推但 PR 未合併時，基準不得前移
setup
BASE_BEFORE=$(cd "$WORK_ROOT/clone" && git log origin/develop --grep='^upstream-sync: ' -1 --format=%H)
cd "$WORK_ROOT/seed" && echo more > more.txt && git add -A && git commit -qm "上游再改" \
  && git push -q origin HEAD:master && cd /
(cd "$WORK_ROOT/clone" && bash scripts/sync-upstream.sh >/dev/null 2>&1)
BASE_AFTER=$(cd "$WORK_ROOT/clone" && git fetch -q origin \
  && git log origin/develop --grep='^upstream-sync: ' -1 --format=%H)
if [ "$BASE_BEFORE" = "$BASE_AFTER" ]; then
  echo "ok: ⑦ PR 未合併時基準不前移"
else
  echo "FAIL: ⑦ 基準前移了——基準必須取自 origin/develop 而非分支或 tag"
  FAILURES=$((FAILURES + 1))
fi

echo "---"
if [ "$FAILURES" -gt 0 ]; then echo "$FAILURES 項失敗"; exit 1; fi
echo "全部通過"
```

- [ ] **Step 3: 跑測試確認失敗**

Run: `bash scripts/test-sync-upstream.sh`
Expected: FAIL — `cp: scripts/sync-upstream.sh: No such file or directory`（腳本尚未存在）

- [ ] **Step 4: 實作同步腳本**

建立 `scripts/sync-upstream.sh`（`chmod +x`）：

```bash
#!/usr/bin/env bash
# 單向同步：把上游（GitHub 經公司 GitLab 鏡像）整棵樹取代進來，再還原公司獨佔路徑。
# 產出一條 sync/upstream-<sha> branch 供人工適配後發 PR，NEVER 直接推 develop。
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

# 清單是唯一事實來源：還原用它，守門的排除範圍也用它——兩者 MUST 同源，
# 否則清單一改就會漏守或誤報，而誤報會訓練人跳過守門。
OWNED=(); EXCLUDES=()
while read -r ownedPath; do
  [ -n "$ownedPath" ] || continue
  OWNED+=("$ownedPath"); EXCLUDES+=(":(exclude)$ownedPath")
done < scripts/internal-owned-paths.txt

git fetch -q gl origin

# 基準點＝origin/develop 上最後一顆已落地的同步 commit。用 commit 而非 tag：PR 可能被
# 放棄或擱置，tag 若在推分支時就移動，基準會指向從未落地的狀態。
LAST_SYNC=$(git log origin/develop --grep='^upstream-sync: ' -1 --format=%H || true)
if [ -z "$LAST_SYNC" ]; then
  echo "找不到基準同步 commit。首次同步 MUST 先人工 bootstrap（見 docs/internal-sync.md）。" >&2
  exit 1
fi
LAST_UPSTREAM=$(git log -1 --format=%B "$LAST_SYNC" | sed -n 's/^Upstream-Commit: //p')
if [ -z "$LAST_UPSTREAM" ]; then
  echo "基準 commit $LAST_SYNC 缺少 Upstream-Commit trailer——同步 PR 被 squash 了？" >&2
  exit 1
fi

# 前置守門——全部 MUST 通過，NEVER 為了讓同步跑完而跳過。
if [ "$(git rev-parse --abbrev-ref HEAD)" != develop ]; then
  echo "MUST 在 develop 上執行（腳本結束時會留在同步 branch）。" >&2
  exit 1
fi
if [ -n "$(git status --porcelain)" ]; then
  echo "worktree 不乾淨；read-tree --reset 會吃掉未提交的修改。" >&2
  exit 1
fi
if [ -n "$(git diff --name-only "$LAST_SYNC" develop -- . "${EXCLUDES[@]}")" ]; then
  echo "獨佔清單外有公司改動，同步會無聲抹掉它們：" >&2
  git diff --name-only "$LAST_SYNC" develop -- . "${EXCLUDES[@]}" >&2
  exit 1
fi
if [ -n "$(git ls-files --others --exclude-standard -- . "${EXCLUDES[@]}")" ]; then
  echo "有野生 untracked 檔，git add -A 會把它們永久收編成同步 commit 的一部分：" >&2
  git ls-files --others --exclude-standard -- . "${EXCLUDES[@]}" >&2
  exit 1
fi

UPSTREAM=$(git rev-parse gl/master)
UPSTREAM_SHORT=$(git rev-parse --short gl/master)

# 雙邊擁有檔：列出上游這次動過的，交給人工調和。錨點 MUST 是 $LAST_UPSTREAM；用 $LAST_SYNC
# 會拿公司版 pom 去比上游，永遠有差、每次都報。
MANUAL_NOTES=""
while read -r mergePath; do
  [ -n "$mergePath" ] || continue
  if ! git diff --quiet "$LAST_UPSTREAM" gl/master -- "$mergePath"; then
    MANUAL_NOTES="${MANUAL_NOTES}需人工調和：${mergePath}"$'\n'
  fi
done < scripts/manual-merge-paths.txt

SYNC_BRANCH="sync/upstream-${UPSTREAM_SHORT}"
git checkout -qb "$SYNC_BRANCH"
git read-tree -u --reset gl/master              # 整棵樹換成上游，含上游的刪除
git checkout develop -- "${OWNED[@]}"           # 還原公司獨佔路徑（相對切出點淨變更為零）
git add -A
git commit -q -m "upstream-sync: 同步至 ${UPSTREAM_SHORT}" \
  -m "${MANUAL_NOTES}" -m "Upstream-Commit: ${UPSTREAM}"
git push -q -u origin "$SYNC_BRANCH"

echo "已推出 $SYNC_BRANCH。接著人工完成："
echo "  1. 檢視 diff，確認上游改動"
echo "  2. 調和 commit body 列出的雙邊擁有檔"
echo "  3. 接縫適配（上游若改了 AgentRuntime 等介面，internal 實作要跟著改）"
echo "  4. 發 PR 進 develop，CI 綠燈後合併（MUST NOT squash）"
```

- [ ] **Step 5: 跑測試確認通過**

```bash
chmod +x scripts/sync-upstream.sh scripts/test-sync-upstream.sh
bash scripts/test-sync-upstream.sh
```
Expected: 七個情境皆 `ok:`，最後印出「全部通過」

- [ ] **Step 6: Commit**

```bash
git add scripts/
git commit -m "feat(sync): 單向同步腳本、獨佔路徑清單與守門測試"
```

---

### Task 8: 同步流程文件

腳本只在公司側執行，家裡的人不會天天看到；流程與規則 MUST 有一份寫下來的權威來源。

**Files:**
- Create: `docs/internal-sync.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: Task 7 的 `scripts/sync-upstream.sh`、兩份清單。
- Produces: 無程式介面。

- [ ] **Step 1: 撰寫文件**

建立 `docs/internal-sync.md`，內容 MUST 涵蓋下列六節（細節照 `docs/superpowers/specs/2026-08-03-internal-env-seams-design.md`）：

1. **拓撲**：GitHub → 公司 GitLab 鏡像（`gl`，唯讀）→ Azure 工作 repo（`origin`，主線 `develop`）；四步流程中只有第三步需要人。
2. **首次 bootstrap**：公司先把各獨佔檔 commit 到 `develop`（`git checkout develop -- <path>` 才有東西可還原），再建立第一顆帶 `Upstream-Commit:` trailer 的 `upstream-sync:` commit 作為基準點。
3. **每次同步**：在專用 clone／worktree 的 `develop` 上跑 `bash scripts/sync-upstream.sh`，然後在產出的 `sync/upstream-<sha>` branch 上完成三件事——調和 `pom.xml`、做接縫適配（上游改了 `AgentRuntime` 介面時 `internal_runtime.py` MUST 同 PR 跟上）、確認 diff，再發 PR。
4. **硬規則**：同步 PR **MUST NOT squash 合併**（會丟掉 trailer，下次同步找不到基準）；`git remote set-url --push gl no_push`；GitLab MUST 是真鏡像而非重新匯入（SHA 必須與 GitHub 相同，否則稽核線索歸零）。
5. **四類檔案**與兩份清單的意義；新增公司獨佔檔時 MUST 同步更新 `scripts/internal-owned-paths.txt`。
6. **守門的限制**：只觀察 `develop`，越界改動若還在未合併的 feature branch 上要等下次同步才攔得到——公司側 code review MUST 一併把關「共用檔不得修改」。

- [ ] **Step 2: 在 README 加入指路**

在 `README.md` 的文件連結區塊加入一行：

```markdown
- [公司環境同步流程](docs/internal-sync.md) — 單向同步腳本、四類檔案與守門規則
```

- [ ] **Step 3: 驗證文件內的指令可執行**

```bash
bash scripts/test-sync-upstream.sh    # 文件描述的守門行為與實作一致
grep -c "MUST NOT squash" docs/internal-sync.md   # Expected: >= 1
```

- [ ] **Step 4: Commit**

```bash
git add docs/internal-sync.md README.md
git commit -m "docs: 公司環境同步流程說明"
```

---

### Task 9: backend 身分接縫（`deptId` ＋ `tsso.enabled` 條件註冊）＋ trace log

家裡走 header ＋ `local-dev` fallback；公司改由 TSSO 提供身分，主線 interceptor 不註冊。

**Files:**
- Modify: `backend/src/main/java/com/erd/cowork/context/CurrentUser.java`
- Modify: `backend/src/main/java/com/erd/cowork/context/CurrentUserInterceptor.java`
- Modify: `backend/src/main/java/com/erd/cowork/config/WebConfig.java`
- Modify: `backend/src/main/resources/application.yml`
- Test: `backend/src/test/java/com/erd/cowork/context/CurrentUserInterceptorTest.java`

**Interfaces:**
- Produces: `CurrentUser.getDeptId()` / `setDeptId(String)`；設定鍵 `tsso.enabled`（預設 `false`）。
- Consumes: 無。

- [ ] **Step 1: 寫失敗測試**

建立 `backend/src/test/java/com/erd/cowork/context/CurrentUserInterceptorTest.java`：

```java
package com.erd.cowork.context;

import static org.assertj.core.api.Assertions.assertThat;

import jakarta.servlet.http.HttpServletResponse;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;

class CurrentUserInterceptorTest {

  private final CurrentUser currentUser = new CurrentUser();
  private final CurrentUserInterceptor interceptor = new CurrentUserInterceptor(currentUser);

  @Test
  void preHandle_headersPresent_populatesUserIdAndDeptId() {
    MockHttpServletRequest request = new MockHttpServletRequest();
    request.addHeader("X-User-Id", "user-1");
    request.addHeader("X-Dept-Id", "dept-9");
    HttpServletResponse response = new MockHttpServletResponse();

    assertThat(interceptor.preHandle(request, response, new Object())).isTrue();
    assertThat(currentUser.getUserId()).isEqualTo("user-1");
    assertThat(currentUser.getDeptId()).isEqualTo("dept-9");
  }

  @Test
  void preHandle_headersMissing_fallsBackToLocalDev() {
    MockHttpServletRequest request = new MockHttpServletRequest();
    HttpServletResponse response = new MockHttpServletResponse();

    interceptor.preHandle(request, response, new Object());

    assertThat(currentUser.getUserId()).isEqualTo("local-dev");
    assertThat(currentUser.getDeptId()).isEqualTo("local-dev");
  }

  @Test
  void preHandle_blankDeptHeader_fallsBackToLocalDev() {
    MockHttpServletRequest request = new MockHttpServletRequest();
    request.addHeader("X-User-Id", "user-2");
    request.addHeader("X-Dept-Id", "   ");
    HttpServletResponse response = new MockHttpServletResponse();

    interceptor.preHandle(request, response, new Object());

    assertThat(currentUser.getUserId()).isEqualTo("user-2");
    assertThat(currentUser.getDeptId()).isEqualTo("local-dev");
  }
}
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && ./mvnw test -Dtest=CurrentUserInterceptorTest`
Expected: 編譯失敗 — `cannot find symbol: method getDeptId()`

- [ ] **Step 3: `CurrentUser` 加欄位**

在 `CurrentUser.java` 的 `userId` 之後加入，並在 class Javadoc 的 async 警語補一句 `deptId` 同樣適用：

```java
  private String userId;

  /** 部門代碼。與 userId 同屬請求身分，由 interceptor 一併填入；async/SSE 邊界前同樣 MUST 值物件化。 */
  private String deptId;
```

- [ ] **Step 4: `CurrentUserInterceptor` 填 `deptId`、加條件註冊與 log**

改寫 `CurrentUserInterceptor.java`：

```java
package com.erd.cowork.context;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;
import org.springframework.web.servlet.HandlerInterceptor;

/**
 * Reads the {@code X-User-Id} / {@code X-Dept-Id} headers and populates the request-scoped {@link
 * CurrentUser}. Missing or blank headers fall back to {@code "local-dev"} so the v1 local
 * environment works without SSO.
 *
 * <p>Registered only when {@code tsso.enabled} is false or unset. In the company environment TSSO
 * supplies the identity and provides its own {@code WebMvcConfigurer}, so this bean is absent.
 */
@Component
@ConditionalOnProperty(name = "tsso.enabled", havingValue = "false", matchIfMissing = true)
@RequiredArgsConstructor
@Slf4j
public class CurrentUserInterceptor implements HandlerInterceptor {

  static final String DEFAULT_VALUE = "local-dev";
  static final String USER_HEADER = "X-User-Id";
  static final String DEPT_HEADER = "X-Dept-Id";

  private final CurrentUser currentUser;

  @Override
  public boolean preHandle(
      HttpServletRequest request, HttpServletResponse response, Object handler) {
    String userHeader = request.getHeader(USER_HEADER);
    String deptHeader = request.getHeader(DEPT_HEADER);
    boolean usedFallback = !StringUtils.hasText(userHeader) || !StringUtils.hasText(deptHeader);
    currentUser.setUserId(StringUtils.hasText(userHeader) ? userHeader : DEFAULT_VALUE);
    currentUser.setDeptId(StringUtils.hasText(deptHeader) ? deptHeader : DEFAULT_VALUE);
    // 公司環境的身分問題家裡重現不了，識別碼與是否走 fallback 是唯一線索（皆非使用者資料內容）。
    log.debug(
        "resolved identity userId={} deptId={} fallback={}",
        currentUser.getUserId(),
        currentUser.getDeptId(),
        usedFallback);
    return true;
  }
}
```

> 舊常數 `DEFAULT_USER_ID` / `HEADER` 已改名，若有其他檔案引用 MUST 一併更新：
> `cd backend && grep -rn "DEFAULT_USER_ID\|CurrentUserInterceptor.HEADER" src/`

- [ ] **Step 5: `WebConfig` 改為條件註冊並記 log**

改寫 `WebConfig.java`：

```java
package com.erd.cowork.config;

import com.erd.cowork.context.CurrentUserInterceptor;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.config.annotation.InterceptorRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

@Configuration
@RequiredArgsConstructor
@Slf4j
public class WebConfig implements WebMvcConfigurer {

  private final ObjectProvider<CurrentUserInterceptor> currentUserInterceptorProvider;

  @Override
  public void addInterceptors(InterceptorRegistry registry) {
    CurrentUserInterceptor interceptor = currentUserInterceptorProvider.getIfAvailable();
    if (interceptor == null) {
      // tsso.enabled=true：身分改由公司 TSSO 的 WebMvcConfigurer 提供。公司若尚未提供，
      // CurrentUser 會全空而症狀延後到第一次查詢才爆——故在啟動時就講明白。
      log.warn("CurrentUserInterceptor not registered (tsso.enabled=true); identity MUST come from the TSSO configurer");
      return;
    }
    log.info("CurrentUserInterceptor registered (tsso.enabled=false)");
    registry.addInterceptor(interceptor).excludePathPatterns("/actuator/**");
  }
}
```

- [ ] **Step 6: `application.yml` 加入設定鍵**

在 `backend/src/main/resources/application.yml` 的 `server:` 區塊之後、`erd:` 之前插入：

```yaml
tsso:
  # true＝身分由公司 TSSO 提供，主線 CurrentUserInterceptor 不註冊（公司側另提供 WebMvcConfigurer）。
  # 家裡維持 false：讀 X-User-Id / X-Dept-Id，缺值 fallback local-dev。
  enabled: ${TSSO_ENABLED:false}
```

- [ ] **Step 7: 跑 backend 測試**

Run: `cd backend && ./mvnw test`
Expected: PASS（全數綠燈，含新增的 3 個測試）

- [ ] **Step 8: 記錄環境變數並 commit**

在 `.env.example` 的 `[0] 共用設定` 加入：

```bash
# 身分來源。false（預設）＝讀 X-User-Id / X-Dept-Id header，缺值 fallback local-dev；
# true＝公司 TSSO 提供，主線 interceptor 不註冊，MUST 由公司側 WebMvcConfigurer 接手。
# TSSO_ENABLED=false
```

```bash
git add backend/src/main/java/com/erd/cowork/context/ backend/src/main/java/com/erd/cowork/config/WebConfig.java backend/src/main/resources/application.yml backend/src/test/java/com/erd/cowork/context/ .env.example
git commit -m "feat(backend): CurrentUser 加 deptId，interceptor 依 tsso.enabled 條件註冊"
```

---

### Task 10: deepagent-service 的接縫 trace log

接縫的分支點必須留下「走了哪一條路」的紀錄——公司環境的問題家裡重現不了。

**Files:**
- Modify: `deepagent-service/app/agent/runtime/__init__.py`
- Modify: `deepagent-service/app/agent/runtime/deepagents_runtime.py`
- Test: `deepagent-service/tests/test_runtime.py`

**Interfaces:**
- Consumes: Task 1／2 的 `load_runtime()`、`DeepAgentsRuntime`。
- Produces: 無新介面。

- [ ] **Step 1: 寫失敗測試**

在 `deepagent-service/tests/test_runtime.py` 末尾追加：

```python
def test_load_runtime_logs_selected_runtime(monkeypatch, caplog) -> None:
    monkeypatch.delenv("AGENT_RUNTIME", raising=False)
    with caplog.at_level("INFO", logger="app.agent.runtime"):
        load_runtime()
    assert "runtime=deepagents" in caplog.text
    assert "app.agent.runtime.deepagents_runtime" in caplog.text


def test_build_model_logs_config_without_api_key(monkeypatch, caplog) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "super-secret-key")
    monkeypatch.setenv("AGENT_MODEL", "test-model")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://internal.example/v1")
    with caplog.at_level("INFO", logger="app.agent.runtime.deepagents_runtime"):
        DeepAgentsRuntime().build_model()
    assert "model=test-model" in caplog.text
    # base-url 只記有無設定：值可能是內部位址，NEVER 落進 log 蒐集系統。
    assert "baseUrlSet=True" in caplog.text
    assert "super-secret-key" not in caplog.text
    assert "internal.example" not in caplog.text
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd deepagent-service && uv run pytest tests/test_runtime.py -v -k logs`
Expected: FAIL（`assert "runtime=deepagents" in caplog.text` — caplog 為空）

- [ ] **Step 3: 加上 log**

`app/agent/runtime/__init__.py`：頂端加 `import logging` 與 `logger = logging.getLogger(__name__)`，並在 `return` 之前插入：

```python
    logger.info("agent runtime selected runtime=%s module=%s", runtimeName, modulePath)
    return getattr(module, className)()
```

`app/agent/runtime/deepagents_runtime.py`：頂端加 `import logging` 與 `logger = logging.getLogger(__name__)`，並在 `build_model` 的 `return ChatOpenAI(...)` 之前插入：

```python
        # 只記「有無設定」不記 base-url 的值——它可能是公司內部位址。NEVER 記 api key。
        logger.info(
            "building chat model model=%s baseUrlSet=%s authMode=%s",
            os.environ.get("AGENT_MODEL", "qwen3.6-35b"),
            bool(os.environ.get("OPENAI_BASE_URL")),
            os.environ.get("AGENT_AUTH_MODE", "bearer"),
        )
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd deepagent-service && uv run pytest tests/test_runtime.py -v`
Expected: PASS（全數綠燈）

- [ ] **Step 5: 跑全部 Python 測試並 commit**

```bash
cd deepagent-service && uv run pytest
git add deepagent-service/app/agent/runtime/ deepagent-service/tests/test_runtime.py
git commit -m "feat(deepagent): 接縫分支點加上 trace log"
```

---

## 收尾驗收

- [ ] `cd deepagent-service && uv run pytest` 全綠
- [ ] `cd frontend && npm test` 全綠
- [ ] `cd backend && ./mvnw test` 全綠（含 Task 9 新增的 interceptor 測試）
- [ ] `bash scripts/test-sync-upstream.sh` 全綠
- [ ] `cd frontend && npm run build` 成功，且未設 `VITE_INTERNAL_SCRIPT_URL` 時 `dist/index.html` 不含任何 internal script 標籤
