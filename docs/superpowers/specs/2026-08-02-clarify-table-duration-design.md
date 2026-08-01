# Clarify + Table + Turn Duration — 設計文件

**日期**：2026-08-02
**分支**：`feat/clarify-table-duration`（基於 `fix/deepagent-trace-findings`）
**範圍**：三個獨立小功能，皆針對 deepagent（LangGraph analysis）路徑為主線。

## 背景

1. AI 回覆由 `MessageBubble.tsx` 以 ReactMarkdown + remark-gfm 渲染；markdown 表格已被解析成 `<table>` 但零樣式，視覺上像純文字擠在一起。
2. QUESTION 事件鏈路 Java backend（`QuestionEvent`/`ClarifyingQuestion`、`LangGraphAnalysisProvider` 捕捉與持久化）與前端（`QuestionCards`）皆已完整；唯獨 deepagent-service 從不發 QUESTION，反問功能在主線上是死的。
3. 全系統沒有任何回合耗時欄位；訊息只有 `createdAt`。

## 功能 1：AI 回覆 markdown 表格美化（純前端）

**做法**：在 `MessageBubble.tsx` 的 ReactMarkdown `components` 加 `table`/`th`/`td` 自訂渲染。

- `table` 外包一層 `overflow-x-auto` 容器——寬表格橫向捲動，不撐破氣泡（氣泡 `max-w-[85%]`）。
- 樣式對齊 `ResultTable` 視覺語言：細框線（`border-gray-200`）、表頭淺灰底（`bg-gray-50`）、斑馬紋、`text-xs`。
- 不用 antd Table：markdown 表格是 ReactMarkdown 產出的現成元素樹，包 antd 需要重新解析資料，得不償失。

**測試**：`MessageBubble` 行為測試——AI text 含 GFM 表格時，斷言 `<th>`/`<td>` 內容與 `overflow-x-auto` 容器存在；純文字回覆不受影響。

## 功能 2：deepagent 反問（`ask_user` 工具）

**關鍵決定：用工具、不用文字協定。** deepagent 在尚未呼叫任何工具前（`tool_started == False`），模型文字會以 TOKEN 即時串流上畫面——反問正是發生在「不跑工具」的回合，若走 OpenAI provider 的 ` ```questions ` 文字區塊協定，使用者會看到原始 JSON 滾過。工具呼叫的參數不進文字流，天然乾淨。

### Python（deepagent-service）

- **新工具 `ask_user`**：參數為問題清單，每題 `text: str`、`options: list[str]`、`multi_select: bool = False`（Pydantic 驗證）。呼叫時把問題寫入 per-request holder（隨 `build_agent` 閉包綁定，比照 `ToolResultRecorder` 的 per-request 紀律），回傳固定指示字串：問題已送出，請以一句簡短繁中訊息結束本回合，不要繼續分析。
- **wire 事件**（`main.py`）：stream 結束後 holder 有問題 → 在 ANSWER 之前 yield `{"type": "QUESTION", "questions": [{"text": …, "options": […], "multiSelect": …}]}`——欄位名對齊 Java `ClarifyingQuestion`（camelCase `multiSelect`），Jackson `@JsonSubTypes` 已認得 `QUESTION`。
- **fallback 文案**：本輪發過 QUESTION 且模型最終文字為空時，ANSWER 用「請回答以上問題,以便我繼續分析。」——不可落入既有的「請再問一次」fallback。
- **STEP 標題**：`ask_user` → 「向使用者確認需求」（`events.py step_title_for`）。
- **prompt**（`prompts.py`）新增 working principle：需求模糊或有多種合理解讀時，先呼叫 `ask_user` 問清楚再分析；一次最多 3 題；問題與選項用繁體中文；使用者已回答過的內容不重複問；需求明確時不反問。

### 邊界行為

- 模型呼叫 `ask_user` 後仍繼續跑分析工具：接受（prompt 約束，不做硬 gate）。若同輪也產出 dashboard，QUESTION 與 ANSWER/DASHBOARD_HTML 照常各自發出，前端本可並存。
- 使用者回答後：答案以下一則使用者訊息進入同一 checkpoint thread（內含 ask_user 工具紀錄），模型自然接續，無需額外狀態。
- Java backend 與前端 `QuestionCards`：**零改動**。

**測試**：tool 單元測試（驗證 holder 寫入與回傳指示）；`/chat` 整合測試（假 agent 呼叫 ask_user → 斷言 QUESTION 事件在 ANSWER 前、欄位 camelCase、空答案 fallback 文案）。

## 功能 3：本輪耗時顯示（純前端、不持久化）

- `useAgentStream.send()` 起跑時以 ref 記 `Date.now()`；串流結束（DONE、ERROR、NETWORK_ERROR、使用者停止）時計算 `durationMs` 放入 `AgentStreamState`。
- `ChatPanel` 在 `isStreaming` 翻 false 時把 `durationMs` 複製進本地 state（撐過 `reset()`），經 `MessageList` 傳到**最後一則 AI 氣泡**（含 questions 停留的 live 氣泡）。
- `MessageBubble` 新增 `durationMs?: number | null` prop，串流結束後於氣泡尾端顯示灰色小字「⏱ 耗時 1 分 23 秒」；60 秒內顯示「45 秒」。
- 使用者按停止也顯示（跑到中斷為止的耗時）。重新整理頁面後消失（已確認接受，不做後端持久化）。

**測試**：`useAgentStream` 的 durationMs 計算（fake timers）；`MessageBubble` 顯示格式測試（秒/分秒兩種）。

## 範圍外

- 耗時持久化（無後端/DB/migration 改動）。
- OpenAI provider 路徑的反問（既有 ` ```questions ` 協定照舊）。
- `exp/custom-chart-only` 實驗分支不動。
