# Clarify + Table + Turn Duration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 三個獨立小功能：AI 回覆 markdown 表格美化（前端）、deepagent `ask_user` 反問工具（Python）、本輪耗時顯示（前端）。

**Architecture:** 反問走工具呼叫（不走文字協定，避免 TOKEN 串流漏 JSON）：per-request `QuestionHolder` 收集問題 → `/chat` stream 結束後在 ANSWER 前發 `QUESTION` wire 事件（欄位 camelCase 對齊 Java `ClarifyingQuestion`，Java 與前端零改動）。表格美化只動 `MessageBubble` 的 ReactMarkdown `components`。耗時純前端：`useAgentStream` 計時 → `ChatPanel` 本地 state 撐過 reset → 最後一則 AI 氣泡尾端顯示。

**Tech Stack:** React 18 + TypeScript + Vitest/RTL（frontend）；Python 3.12 + FastAPI + deepagents/LangChain + pytest（deepagent-service）。

**Spec:** `docs/superpowers/specs/2026-08-02-clarify-table-duration-design.md`

## Global Constraints

- 變數/參數 NEVER 用 1–2 字元名稱；一律描述性單詞
- 前端：`React.FC<Props>`、明確 return type、`import type`、NEVER `any`、event handler 用 `useCallback`
- 前端測試：Vitest + RTL，斷言元素級行為（NEVER snapshot-only）
- Python：參照 `.claude/skills/fastapi/SKILL.md`；工具/holder 比照 `ToolResultRecorder` 的 per-request + lock 紀律
- 註解精簡：1–2 行寫目的＋做法；NEVER spec 編號/commit hash/事故敘事
- NEVER log prompt/使用者資料內容
- 每個 task 結束：對應測試套件全綠後 commit（frontend：`npm test -- --run`；Python：`uv run pytest`，在 `deepagent-service/` 下）

---

### Task 1: MessageBubble markdown 表格樣式

**Files:**
- Modify: `frontend/src/components/chat/MessageBubble.tsx`（ReactMarkdown `components`，約 line 250–263）
- Test: `frontend/src/components/chat/MessageBubble.test.tsx`

**Interfaces:**
- Consumes: 既有 ReactMarkdown + remark-gfm 渲染管線
- Produces: 無對外介面變化（純視覺）

- [ ] **Step 1: 寫失敗測試**

在 `MessageBubble.test.tsx` 末尾加：

```tsx
test('AI markdown table renders with borders and horizontal-scroll container', () => {
  const markdownTable = [
    '| 系統 | 工單數 |',
    '| --- | --- |',
    '| CRM | 42 |',
    '| ERP | 17 |',
  ].join('\n');
  const { container } = render(<MessageBubble sender="AI" text={markdownTable} />);

  // remark-gfm 解析出真正的 table 元素，且格線/表頭樣式已套上
  const headerCell = screen.getByRole('columnheader', { name: '系統' });
  expect(headerCell.className).toContain('border');
  expect(headerCell.className).toContain('bg-gray-50');
  const dataCell = screen.getByRole('cell', { name: 'CRM' });
  expect(dataCell.className).toContain('border');
  // 表格外包 overflow-x-auto 容器，寬表格不撐破氣泡
  const scrollContainer = container.querySelector('.overflow-x-auto table');
  expect(scrollContainer).not.toBeNull();
});
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && npm test -- --run MessageBubble`
Expected: FAIL——`headerCell.className` 不含 `border`（目前 th 無 class）

- [ ] **Step 3: 實作**

`MessageBubble.tsx` 的 `<ReactMarkdown components={{...}}>` 內，在 `a` 之後加三個渲染元件：

```tsx
table: ({ children }) => (
  <div className="my-2 overflow-x-auto">
    <table className="w-full border-collapse text-xs [&_tbody_tr:nth-child(even)]:bg-gray-50">
      {children}
    </table>
  </div>
),
th: ({ children }) => (
  <th className="border border-gray-200 bg-gray-50 px-2 py-1 text-left font-medium text-gray-600">
    {children}
  </th>
),
td: ({ children }) => <td className="border border-gray-200 px-2 py-1">{children}</td>,
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd frontend && npm test -- --run MessageBubble`
Expected: PASS（含既有測試）

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/chat/MessageBubble.tsx frontend/src/components/chat/MessageBubble.test.tsx
git commit -m "feat(frontend): style markdown tables in AI replies"
```

---

### Task 2: `ask_user` 工具與 QuestionHolder（Python）

**Files:**
- Create: `deepagent-service/app/agent/tools/clarify.py`
- Test: `deepagent-service/tests/test_clarify_tool.py`

**Interfaces:**
- Consumes: `langchain_core.tools.tool`、`pydantic.BaseModel`
- Produces（Task 3 依賴，簽名精確如下）:
  - `class ClarifyQuestion(BaseModel)`：欄位 `text: str`、`options: list[str]`（default 空）、`multi_select: bool`（default False）；方法 `to_wire(self) -> dict` 回傳 `{"text": …, "options": […], "multiSelect": …}`
  - `class QuestionHolder`：`add(self, questions: list[ClarifyQuestion]) -> None`、`questions(self) -> list[ClarifyQuestion]`
  - `def build_ask_user_tool(holder: QuestionHolder) -> BaseTool`：tool 名 `ask_user`，args schema `questions: list[ClarifyQuestion]`
  - 常數 `MAX_QUESTIONS = 3`、`ASK_USER_TOOL_RESULT: str`

- [ ] **Step 1: 寫失敗測試**

新檔 `deepagent-service/tests/test_clarify_tool.py`：

```python
from app.agent.tools.clarify import (
    ASK_USER_TOOL_RESULT,
    ClarifyQuestion,
    QuestionHolder,
    build_ask_user_tool,
)


def _question(text: str) -> dict:
    return {"text": text, "options": ["選項A", "選項B"], "multi_select": False}


def test_ask_user_records_questions_and_returns_stop_instruction() -> None:
    holder = QuestionHolder()
    ask_user = build_ask_user_tool(holder)

    result = ask_user.invoke({"questions": [_question("想分析哪個指標?")]})

    assert result == ASK_USER_TOOL_RESULT
    recorded = holder.questions()
    assert len(recorded) == 1
    assert recorded[0].text == "想分析哪個指標?"
    assert recorded[0].options == ["選項A", "選項B"]
    assert recorded[0].multi_select is False


def test_holder_caps_total_questions_at_three() -> None:
    holder = QuestionHolder()
    ask_user = build_ask_user_tool(holder)

    ask_user.invoke({"questions": [_question("Q1"), _question("Q2")]})
    ask_user.invoke({"questions": [_question("Q3"), _question("Q4")]})

    assert [question.text for question in holder.questions()] == ["Q1", "Q2", "Q3"]


def test_to_wire_uses_camel_case_multi_select() -> None:
    question = ClarifyQuestion(text="範圍?", options=["全部"], multi_select=True)
    assert question.to_wire() == {"text": "範圍?", "options": ["全部"], "multiSelect": True}


def test_options_default_to_empty_list() -> None:
    question = ClarifyQuestion(text="請說明需求")
    assert question.options == []
    assert question.to_wire()["options"] == []
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd deepagent-service && uv run pytest tests/test_clarify_tool.py -v`
Expected: FAIL——`ModuleNotFoundError: app.agent.tools.clarify`

- [ ] **Step 3: 實作**

新檔 `deepagent-service/app/agent/tools/clarify.py`：

```python
"""`ask_user` 反問工具——需求模糊時的結構化出口。走 tool args 而非文字協定：deepagent 在
工具啟動前的模型文字會以 TOKEN 直接串流上畫面，文字協定會讓使用者看到原始 JSON。

`QuestionHolder` 比照 `ToolResultRecorder`：per-request 建立（app.main.chat）、tool 在
executor thread 寫入、SSE handler 在 stream 結束後讀取，同一把 lock 保護。"""

import threading

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

# 單輪反問題數硬上限——與 system prompt 的約束一致，超出部分靜默捨棄。
MAX_QUESTIONS = 3

# 回給模型的固定指示：結束回合，不做分析。
ASK_USER_TOOL_RESULT = (
    "Questions delivered to the user. End your turn NOW with one short Traditional-Chinese "
    "sentence asking the user to answer them; do NOT call any more tools and do NOT start "
    "the analysis."
)


class ClarifyQuestion(BaseModel):
    """單一反問題目——欄位即 LLM 可見的 args schema；wire 端 multiSelect 為 camelCase。"""

    text: str = Field(description="The question, in Traditional Chinese.")
    options: list[str] = Field(
        default_factory=list,
        description="2-4 short answer choices in Traditional Chinese; [] for free-form.",
    )
    multi_select: bool = Field(
        default=False, description="Whether the user may pick multiple options."
    )

    def to_wire(self) -> dict:
        return {"text": self.text, "options": self.options, "multiSelect": self.multi_select}


class QuestionHolder:
    """non-bean: instantiate per /chat request."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._questions: list[ClarifyQuestion] = []

    def add(self, questions: list[ClarifyQuestion]) -> None:
        with self._lock:
            remaining_capacity = MAX_QUESTIONS - len(self._questions)
            if remaining_capacity > 0:
                self._questions.extend(questions[:remaining_capacity])

    def questions(self) -> list[ClarifyQuestion]:
        with self._lock:
            return list(self._questions)


def build_ask_user_tool(holder: QuestionHolder) -> BaseTool:
    @tool("ask_user")
    def ask_user_tool(questions: list[ClarifyQuestion]) -> str:
        """Ask the user clarifying questions BEFORE starting any analysis.

        Call this when the request is ambiguous or has several reasonable interpretations
        (unclear metric, scope, time range, grouping, or chart preference). At most 3
        questions per turn; write text and options in Traditional Chinese. After calling,
        end your turn -- do not run other tools.
        """
        holder.add(questions)
        return ASK_USER_TOOL_RESULT

    return ask_user_tool
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd deepagent-service && uv run pytest tests/test_clarify_tool.py -v`
Expected: PASS（4 tests）

- [ ] **Step 5: Commit**

```bash
git add deepagent-service/app/agent/tools/clarify.py deepagent-service/tests/test_clarify_tool.py
git commit -m "feat(deepagent): add ask_user clarify tool with per-request QuestionHolder"
```

---

### Task 3: 反問接線——graph、main.py、STEP 標題、prompt

**Files:**
- Modify: `deepagent-service/app/agent/graph.py`（`build_agent` 簽名與 tools 組裝，line 111–137）
- Modify: `deepagent-service/app/main.py`（holder 建立、QUESTION 事件、fallback 文案，line 262–404）
- Modify: `deepagent-service/app/agent/events.py`（`step_title_for`，line 20–38）
- Modify: `deepagent-service/app/agent/prompts.py`
- Modify: `deepagent-service/tests/test_graph.py`（`build_agent` 呼叫點 line 24、47 加參數）
- Test: `deepagent-service/tests/test_chat.py`、`deepagent-service/tests/test_events.py`

**Interfaces:**
- Consumes（Task 2）: `QuestionHolder`、`build_ask_user_tool(holder)`、`ClarifyQuestion.to_wire()`
- Produces: wire 事件 `{"type": "QUESTION", "questions": [{"text", "options", "multiSelect"}]}`（在 ANSWER 之前發出）；`main.ASK_USER_EMPTY_ANSWER_FALLBACK_MESSAGE`

- [ ] **Step 1: 寫失敗測試**

`tests/test_chat.py` 加 fixture 與測試（放在既有 fixture 區之後；`_question` 字典 args 對齊 pydantic schema）：

```python
@pytest.fixture()
def scripted_flow_ask_user(tmp_path, monkeypatch):
    """需求模糊回合:模型只呼叫 ask_user 後以一句話收尾,不跑任何分析工具。"""
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "ws"))
    scripted = ScriptedChatModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "ask_user",
                        "id": "ask1",
                        "args": {
                            "questions": [
                                {
                                    "text": "想分析哪個指標?",
                                    "options": ["工單數", "處理時長"],
                                    "multi_select": False,
                                }
                            ]
                        },
                    }
                ],
            ),
            AIMessage(content="請先回答上面的問題,我再開始分析。"),
        ]
    )
    monkeypatch.setattr(main_module, "build_model", lambda: scripted)
    return scripted


@pytest.fixture()
def scripted_flow_ask_user_empty_answer(tmp_path, monkeypatch):
    """ask_user 後模型最終文字為空——ANSWER 應用反問專屬 fallback,不是「請再問一次」。"""
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "ws"))
    scripted = ScriptedChatModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "ask_user",
                        "id": "ask1",
                        "args": {
                            "questions": [
                                {"text": "想分析哪個指標?", "options": [], "multi_select": False}
                            ]
                        },
                    }
                ],
            ),
            AIMessage(content=""),
        ]
    )
    monkeypatch.setattr(main_module, "build_model", lambda: scripted)
    return scripted


async def test_chat_ask_user_emits_question_before_answer(
    tmp_path, scripted_flow_ask_user
) -> None:
    events = await _post_chat(tmp_path)
    types = [event["type"] for event in events]

    assert types.index("QUESTION") < types.index("ANSWER")
    question_event = next(event for event in events if event["type"] == "QUESTION")
    assert question_event["questions"] == [
        {"text": "想分析哪個指標?", "options": ["工單數", "處理時長"], "multiSelect": False}
    ]
    assert events[-1] == {"type": "ANSWER", "text": "請先回答上面的問題,我再開始分析。"}
    # ask_user 的 STEP 用人話標題
    assert any(
        event["type"] == "STEP" and event["title"] == "向使用者確認需求" for event in events
    )


async def test_chat_ask_user_empty_answer_uses_clarify_fallback(
    tmp_path, scripted_flow_ask_user_empty_answer
) -> None:
    events = await _post_chat(tmp_path)
    assert events[-1] == {
        "type": "ANSWER",
        "text": main_module.ASK_USER_EMPTY_ANSWER_FALLBACK_MESSAGE,
    }


async def test_chat_without_ask_user_emits_no_question(tmp_path, scripted_flow) -> None:
    events = await _post_chat(tmp_path)
    assert not [event for event in events if event["type"] == "QUESTION"]
```

`tests/test_events.py` 加：

```python
def test_step_title_for_ask_user() -> None:
    assert step_title_for("ask_user", {}) == "向使用者確認需求"
```

（若 `step_title_for` 未 import，在該檔 import 區補上。）

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd deepagent-service && uv run pytest tests/test_chat.py -k ask_user tests/test_events.py -v`
Expected: FAIL——`ask_user` 工具不存在（unknown tool）、無 QUESTION 事件、`ASK_USER_EMPTY_ANSWER_FALLBACK_MESSAGE` AttributeError

- [ ] **Step 3: 實作**

**`app/agent/graph.py`**——import 加 `from app.agent.tools.clarify import QuestionHolder, build_ask_user_tool`；`build_agent` 簽名加最後一個參數 `clarify_holder: QuestionHolder`，`tools=` 改為：

```python
tools=[*build_data_tools(connection, workspace, recorder), build_ask_user_tool(clarify_holder)],
```

**`app/agent/events.py`**——`step_title_for` 在 `write_todos` 分支後加：

```python
if tool_name == "ask_user":
    return "向使用者確認需求"
```

**`app/main.py`**——
1. import 加 `from app.agent.tools.clarify import QuestionHolder`
2. 常數區（`EMPTY_ANSWER_FALLBACK_MESSAGE` 附近）加：

```python
# 本輪發過 QUESTION（ask_user 反問）且模型最終文字為空時的兜底文案——反問是正常流程,
# 不可落入「請再問一次」的一般空回應 fallback。
ASK_USER_EMPTY_ANSWER_FALLBACK_MESSAGE = "請回答以上問題,以便我繼續分析。"
```

3. `chat()` 內 `recorder = ToolResultRecorder()` 之後加 `clarify_holder = QuestionHolder()`，`build_agent(...)` 呼叫尾端加傳 `clarify_holder`
4. ANSWER 組裝前（`final_answer_text = ...` 那行之前）加 QUESTION 發送：

```python
clarify_questions = clarify_holder.questions()
if clarify_questions:
    yield ServerSentEvent(
        data={
            "type": "QUESTION",
            "questions": [question.to_wire() for question in clarify_questions],
        }
    )
```

5. answer 選擇鏈在 `elif dashboard_html_emitted:` 之後、`else:` 之前插入：

```python
elif clarify_questions:
    answer_text = ASK_USER_EMPTY_ANSWER_FALLBACK_MESSAGE
```

**`app/agent/prompts.py`**——Working principles 的 scope 條目後加一條：

```
- If the request is ambiguous or has several reasonable interpretations (unclear metric, \
scope, time range, grouping, or chart preference), call ask_user FIRST -- at most 3 \
questions, each with Traditional-Chinese text and short options -- then end the turn \
without running any analysis. When the request is clear, do NOT ask; NEVER re-ask what \
the user already answered in this conversation.
```

**`tests/test_graph.py`**——line 24、47 的 `build_agent(...)` 呼叫尾端加 `QuestionHolder()`（import `from app.agent.tools.clarify import QuestionHolder`）。

- [ ] **Step 4: 跑全套測試確認通過**

Run: `cd deepagent-service && uv run pytest`
Expected: 全綠（含既有 test_chat/test_graph/test_events 回歸）

- [ ] **Step 5: Commit**

```bash
git add deepagent-service/app/agent/graph.py deepagent-service/app/agent/events.py deepagent-service/app/main.py deepagent-service/app/agent/prompts.py deepagent-service/tests/test_chat.py deepagent-service/tests/test_events.py deepagent-service/tests/test_graph.py
git commit -m "feat(deepagent): emit QUESTION wire event from ask_user clarify turns"
```

---

### Task 4: `useAgentStream` 回合計時

**Files:**
- Modify: `frontend/src/types.ts`（`AgentStreamState`，line 46–62）
- Modify: `frontend/src/hooks/useAgentStream.ts`
- Modify: `frontend/src/components/chat/ChatPanel.test.tsx`（`IDLE_STATE` fixture）、`frontend/src/components/chat/MessageList.test.tsx`（`IDLE_LIVE` fixture）、`frontend/src/CoworkPage.test.tsx`（AgentStreamState literal）
- Test: `frontend/src/hooks/useAgentStream.test.tsx`

**Interfaces:**
- Produces（Task 5 依賴）: `AgentStreamState.durationMs: number | null`——串流中為 `null`，DONE/ERROR/NETWORK_ERROR（含使用者停止的 abort 路徑）後為毫秒數

- [ ] **Step 1: 寫失敗測試**

`useAgentStream.test.tsx`：既有 initial-state 斷言物件加 `durationMs: null`；再加兩個測試：

```tsx
it('records durationMs when the stream completes', async () => {
  const qc = freshClient();
  stubFetch({
    ok: true,
    body: makeStream(['data: {"type":"ANSWER","text":"done"}\n\n']),
  });

  const { result } = renderHook(() => useAgentStream('s1'), { wrapper: makeWrapper(qc) });

  await act(async () => {
    await result.current.send('question');
  });

  expect(result.current.state.durationMs).toBeGreaterThanOrEqual(0);
});

it('records durationMs when the stream fails with a network error', async () => {
  const qc = freshClient();
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('network down')));

  const { result } = renderHook(() => useAgentStream('s1'), { wrapper: makeWrapper(qc) });

  await act(async () => {
    await result.current.send('question');
  });

  expect(result.current.state.networkError).toBe(true);
  expect(result.current.state.durationMs).toBeGreaterThanOrEqual(0);
});
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && npm test -- --run useAgentStream`
Expected: FAIL——initial-state 物件不含 `durationMs`；新測試 `durationMs` 為 `undefined`

- [ ] **Step 3: 實作**

**`types.ts`**——`AgentStreamState` 加欄位（`tables` 之後）：

```ts
/** Elapsed wall-clock ms for the finished turn; null while idle or streaming. */
durationMs: number | null;
```

**`useAgentStream.ts`**——
1. `initialState` 加 `durationMs: null`
2. `Action` 型別：`DONE`、`NETWORK_ERROR`、`ERROR` 三個 action 都加 `durationMs: number`：

```ts
| { type: 'DONE'; durationMs: number }
| { type: 'NETWORK_ERROR'; error: { code: string; message: string }; durationMs: number }
| { type: 'ERROR'; error: { code: string; message: string }; durationMs: number }
```

3. reducer 對應分支寫入：

```ts
case 'DONE':
  return { ...state, isStreaming: false, durationMs: action.durationMs };

case 'NETWORK_ERROR':
  return {
    ...state,
    isStreaming: false,
    networkError: true,
    error: action.error,
    durationMs: action.durationMs,
  };

case 'ERROR':
  return { ...state, isStreaming: false, error: action.error, durationMs: action.durationMs };
```

4. `send()` 開頭 `dispatch({ type: 'START' })` 之前加 `const startedAt = Date.now();`；四個結束點都帶上耗時——正常完成與 AbortError 的 `dispatch({ type: 'DONE' })` 改為 `dispatch({ type: 'DONE', durationMs: Date.now() - startedAt })`；`ERROR` 與 `NETWORK_ERROR` dispatch 同樣加 `durationMs: Date.now() - startedAt`。

**Fixture 更新**（`AgentStreamState` 為 required 欄位，不更新會 type error）：`ChatPanel.test.tsx` 的 `IDLE_STATE`、`MessageList.test.tsx` 的 `IDLE_LIVE`、`CoworkPage.test.tsx` 的 AgentStreamState literal，各加 `durationMs: null`。

- [ ] **Step 4: 跑前端全套測試確認通過**

Run: `cd frontend && npm test -- --run`
Expected: 全綠

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types.ts frontend/src/hooks/useAgentStream.ts frontend/src/hooks/useAgentStream.test.tsx frontend/src/components/chat/ChatPanel.test.tsx frontend/src/components/chat/MessageList.test.tsx frontend/src/CoworkPage.test.tsx
git commit -m "feat(frontend): track turn duration in useAgentStream state"
```

---

### Task 5: 耗時顯示——formatDuration、MessageBubble、MessageList、ChatPanel

**Files:**
- Create: `frontend/src/utils/formatDuration.ts`
- Modify: `frontend/src/components/chat/MessageBubble.tsx`、`frontend/src/components/chat/MessageList.tsx`、`frontend/src/components/chat/ChatPanel.tsx`
- Test: `frontend/src/utils/formatDuration.test.ts`、`frontend/src/components/chat/MessageBubble.test.tsx`、`frontend/src/components/chat/MessageList.test.tsx`、`frontend/src/components/chat/ChatPanel.test.tsx`

**Interfaces:**
- Consumes（Task 4）: `AgentStreamState.durationMs: number | null`
- Produces: `formatDuration(durationMs: number): string`；`MessageBubble` prop `durationMs?: number | null`；`MessageList` prop `lastTurnDurationMs?: number | null`

- [ ] **Step 1: 寫失敗測試**

新檔 `frontend/src/utils/formatDuration.test.ts`：

```ts
import { formatDuration } from './formatDuration';

test('formats sub-minute durations as seconds', () => {
  expect(formatDuration(45_000)).toBe('45 秒');
});

test('formats minute-plus durations as minutes and seconds', () => {
  expect(formatDuration(83_000)).toBe('1 分 23 秒');
});

test('exact minutes keep the zero-second part', () => {
  expect(formatDuration(60_000)).toBe('1 分 0 秒');
});

test('sub-second durations floor to 1 second', () => {
  expect(formatDuration(400)).toBe('1 秒');
});
```

`MessageBubble.test.tsx` 加：

```tsx
test('AI bubble shows turn duration after streaming ends', () => {
  render(<MessageBubble sender="AI" text="done" durationMs={83_000} />);
  expect(screen.getByText(/耗時 1 分 23 秒/)).toBeInTheDocument();
});

test('AI bubble hides duration while streaming', () => {
  render(<MessageBubble sender="AI" text="partial" streaming durationMs={5_000} />);
  expect(screen.queryByText(/耗時/)).toBeNull();
});
```

`MessageList.test.tsx`——該檔把 `MessageBubble` mock 掉了，斷言改驗證 prop 傳遞：先在檔頭的 MessageBubble mock 加曝光 `durationMs`（props 型別加 `durationMs?: number | null;`，mock div 加 `data-duration-ms={durationMs ?? ''}`），再加兩個測試（沿用該檔既有 `makeUserMsg`/`makeAiMsg`/`IDLE_LIVE`）：

```tsx
test('tail AI history bubble receives lastTurnDurationMs when no live bubble', () => {
  render(
    <MessageList
      messages={[makeUserMsg('u1', '問題'), makeAiMsg('a1', '答案')]}
      live={null}
      lastTurnDurationMs={45_000}
    />,
  );
  expect(screen.getByTestId('bubble-AI')).toHaveAttribute('data-duration-ms', '45000');
});

test('finished live bubble receives lastTurnDurationMs; history bubbles do not', () => {
  render(
    <MessageList
      messages={[makeUserMsg('u1', '問題')]}
      live={{ ...IDLE_LIVE, questions: null, isStreaming: false }}
      lastTurnDurationMs={45_000}
    />,
  );
  const aiBubbles = screen.getAllByTestId('bubble-AI');
  expect(aiBubbles[aiBubbles.length - 1]).toHaveAttribute('data-duration-ms', '45000');
});
```

`ChatPanel.test.tsx` 加（沿用該檔 mock `useAgentStream` 的模式）：

```tsx
it('captures durationMs when streaming flips false and shows it on the tail bubble', async () => {
  const qc = freshClient();
  const streamingState: AgentStreamState = { ...IDLE_STATE, isStreaming: true };
  vi.mocked(useAgentStream).mockReturnValue({
    state: streamingState,
    send: vi.fn(),
    stop: vi.fn(),
    reset: vi.fn(),
  });
  vi.mocked(useSessionDetail).mockReturnValue(
    makeSession([makeMessage('m1', null, null, '分析完成')]),
  );

  const { rerender } = render(<ChatPanel sessionId="s1" />, { wrapper: makeWrapper(qc) });

  vi.mocked(useAgentStream).mockReturnValue({
    state: { ...IDLE_STATE, durationMs: 45_000 },
    send: vi.fn(),
    stop: vi.fn(),
    reset: vi.fn(),
  });
  rerender(<ChatPanel sessionId="s1" />);

  expect(await screen.findByText(/耗時 45 秒/)).toBeInTheDocument();
});
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && npm test -- --run formatDuration MessageBubble MessageList ChatPanel`
Expected: FAIL——`formatDuration` 模組不存在；`durationMs`/`lastTurnDurationMs` prop 不存在

- [ ] **Step 3: 實作**

**`frontend/src/utils/formatDuration.ts`**：

```ts
/** Formats a turn duration for display: sub-minute as 「N 秒」, otherwise 「M 分 S 秒」.
 *  Sub-second durations floor to 1 second so the label never reads 「0 秒」. */
export function formatDuration(durationMs: number): string {
  const totalSeconds = Math.max(1, Math.round(durationMs / 1000));
  if (totalSeconds < 60) {
    return `${totalSeconds} 秒`;
  }
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes} 分 ${seconds} 秒`;
}
```

**`MessageBubble.tsx`**——Props 加：

```ts
/** Elapsed ms of the turn that produced this bubble; shown as a footer after streaming ends. */
durationMs?: number | null;
```

import `formatDuration`；在 stopped indicator（`{stopped && ...}`）之前加：

```tsx
{!streaming && durationMs != null && (
  <div className="mt-1 text-[11px] text-gray-400">⏱ 耗時 {formatDuration(durationMs)}</div>
)}
```

**`MessageList.tsx`**——Props 加：

```ts
/** Duration of the most recent finished turn; attached to the tail AI bubble (live or history). */
lastTurnDurationMs?: number | null;
```

- live bubble 的 `<MessageBubble>` 加 `durationMs={live.isStreaming ? null : lastTurnDurationMs}`
- history map 的 `<MessageBubble>` 加：

```tsx
durationMs={
  live == null && idx === displayMessages.length - 1 && msg.sender === 'AI'
    ? lastTurnDurationMs
    : null
}
```

**`ChatPanel.tsx`**——
1. state 加 `const [lastTurnDurationMs, setLastTurnDurationMs] = useState<number | null>(null);`
2. 既有 streaming-edge effect（`prevStreamingRef` 那個）的 `if (prevStreamingRef.current && !state.isStreaming)` 區塊內加 `setLastTurnDurationMs(state.durationMs);`（在 `reset()` 判斷之前），並把 `state.durationMs` 加進該 effect 依賴陣列
3. 既有 `if (state.isStreaming) { setQuestionsAnswered(false); }` effect 內加 `setLastTurnDurationMs(null);`
4. `<MessageList>` 加 `lastTurnDurationMs={lastTurnDurationMs}`

- [ ] **Step 4: 跑前端全套測試確認通過**

Run: `cd frontend && npm test -- --run`
Expected: 全綠

- [ ] **Step 5: Commit**

```bash
git add frontend/src/utils/formatDuration.ts frontend/src/utils/formatDuration.test.ts frontend/src/components/chat/MessageBubble.tsx frontend/src/components/chat/MessageBubble.test.tsx frontend/src/components/chat/MessageList.tsx frontend/src/components/chat/MessageList.test.tsx frontend/src/components/chat/ChatPanel.tsx frontend/src/components/chat/ChatPanel.test.tsx
git commit -m "feat(frontend): show turn duration on the tail AI bubble"
```

---

## 完成後驗證

- `cd deepagent-service && uv run pytest`——全綠
- `cd frontend && npm test -- --run`——全綠
- 手動驗證（docker compose 起服務）：
  1. 上傳 CSV 後送出模糊需求（例：「幫我分析一下」）→ 應出現反問卡片、STEP 顯示「向使用者確認需求」、點選項後下一輪正常分析
  2. 明確需求 → 不反問、直接分析；回覆若含 markdown 表格 → 有框線/表頭底色/橫向捲動
  3. 每輪跑完 → 最後一則 AI 氣泡尾端顯示「⏱ 耗時 X」；按停止也顯示；重新整理後消失（預期行為）
