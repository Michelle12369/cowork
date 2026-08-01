# M6 迭代回餵與互動 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox syntax.
>
> **Worker model policy:** implementer/task reviewer 用 sonnet（複雜整合 opus）、終審 opus。

**Goal:** 前版 HTML 回餵（微調不走樣）+ 動態 steps + QUESTION 選項卡 + THINKING 串流。
**Spec:** `docs/superpowers/specs/2026-07-06-m6-artifact-stability-design.md`（v2）
**Base branch:** `feat/m6-iteration`（自 master）

## Global Constraints

- CLAUDE.md rules 全部適用；新事件走既有 `@JsonTypeInfo` sealed 家族模式（型別名大寫）
- 全部協定為**增強型**：模型不遵循 = 現行為（fallback 不可劣化既有行為，測試需釘住）
- Transformer 擴充沿用跨 token 緩衝狀態機模式；新標記抽取不得破壞既有 fence 抽取測試
- 事件契約新增：`{"type":"QUESTION","questions":[{"text":"...","options":["..."],"multiSelect":false}]}`、`{"type":"THINKING","delta":"..."}`
- 動態 step 事件：stepKey 格式 `d1`,`d2`…；stepsJson 持久化含 dynamic steps 最終狀態
- 環境：Java 21 portable（JAVA_HOME workaround 同前）；docker credential workaround 同前；.env 勿印

---

### Task 1: V5 migration + raw_html 儲存 + 回餵鏈（後端）

**Files:**
- Create: `backend/src/main/resources/db/migration/V5__artifact_raw_html_message_questions.sql`
  ```sql
  ALTER TABLE artifact ADD raw_html CLOB;
  ALTER TABLE chat_message ADD questions_json CLOB;
  ```
- Modify: `Artifact` entity（rawHtml，@Lob）；`ChatMessage` entity（questionsJson，@Lob；本 task 只加欄位，寫入在 Task 4）
- Modify: `AgentRequest`：新欄位 `String previousArtifactHtml`（record 重排——所有建構呼叫點同步）
- Modify: `AgentOrchestrator`：finalize 存 artifact 時 setRawHtml(抽取到的原始 html)；prepare 載入 `artifactRepository.findFirstBySessionIdOrderByCreatedAtDesc` 的 rawHtml（null 安全）進 AgentRequest
- Modify: `AgentProperties`：`OpenAiCompatible`/`Anthropic` 各加 `int contextWindow`（yml 預設 131072 / 200000，env 佔位 `ERD_AGENT_OPENAI_COMPATIBLE_CONTEXT_WINDOW` 等）
- Modify: `PromptBuilder.userPrompt`：previousArtifactHtml 非 null 且預算內時附「## previous dashboard html」段 + 最小變更指令（spec §2 文案照抄）；**預算 guard**：`estimateTokens(整體 prompt 含回餵) = 總字元/3.5 > contextWindow*0.5` → 略過回餵 + log.warn。PromptBuilder 需取得 contextWindow——signature 改 `userPrompt(AgentRequest request, int contextWindow)` 或 PromptBuilder 注入 AgentProperties + provider 名判斷（**擇一並全鏈一致**，providers 呼叫點同步）
- Tests：migration 由既有測試覆蓋；PromptBuilderTest 加：回餵段落出現/最小變更指令錨句/超預算略過（小 contextWindow 構造）/null 不附；MessageControllerTest 加：第二問後 artifact rawHtml 非空、第二問的 provider 收到的 prompt 含前版（FakeProvider 可捕捉 request——記錄 AgentRequest 斷言 previousArtifactHtml）

**Commit:** `feat(backend): previous dashboard html feedback loop with budget guard`

---

### Task 2: Transformer 擴充——動態 step 標記 + questions block（後端核心）

**Files:**
- Modify: `HtmlExtractingTransformer`（或抽出協作類 `StreamProtocolExtractor`——若檔案過大擇後者，報告說明）
- Modify: `ExtractionResult`：加 `List<Question> questions`（null/empty 語意明確）；`Question(String text, List<String> options, boolean multiSelect)` record 放 `agent/event`（Task 3 的 QuestionEvent 共用）
- Tests: transformer 測試擴充

**行為規格（全部要測試）：**
1. `[[step: 標題]]`：**行首**標記（前導空白容忍）、跨 token 切割容忍（沿用緩衝策略，保留尾端 ≤7 字元掃描窗擴至涵蓋 `[[step:`——注意與 ```html 掃描窗並存）；抽出 → 輸出 `StepEvent("d{n}", title, null, RUNNING)`（n 從 1 遞增）；**同時**前一個 dynamic step（若有）補發 SUCCESS；標記本身不出現在 TOKEN/answerText
2. title trim、`]]` 前內容含 `[` 容忍；未閉合標記（流結束仍未見 `]]`）→ 當純文字流出（不吞字）
3. ```` ```questions ```` fenced block：JSON array `[{"text":"...","options":["..."],"multiSelect":false}]`；抽出（不外流 TOKEN）→ 存入 result().questions；JSON 解析失敗 → 整塊當純文字流出 + log.debug；一個流最多取第一個 questions block
4. html fence 抽取行為完全不變（既有測試全綠）；questions 與 html 可共存（先問後生成不會同流出現，但實作不得互斥炸掉）
5. 流結束時最後一個 dynamic step 的 SUCCESS 由誰發？——**transformer 在流完成時補發**（onComplete 尾端 concat，模式同 flushBuffer）

**Commit:** `feat(backend): stream protocol extraction — dynamic steps and questions`

---

### Task 3: 新事件型別 + THINKING 串流 + orchestrator 整合（後端）

**Files:**
- Create: `agent/event/QuestionEvent.java`（`List<Question> questions`）、`agent/event/ThinkingEvent.java`（`String delta`）——sealed 家族擴充（AgentEvent permits + @JsonSubTypes：QUESTION/THINKING）
- Modify: `OpenAICompatibleProvider`：SSE 解析擴充——`choices[0].delta.reasoning`（string，缺/null 跳過）→ ThinkingEvent 混入事件流（在 TOKEN 之外的獨立分支；與 content 同 chunk 並存時兩者皆發）
- Modify: `AgentOrchestrator`：finalize——extraction.questions 非空 → concat `QuestionEvent`（在 ANSWER 之後、s4 之前的順位皆可，定一個並測試）；AI 訊息存 questionsJson（ObjectMapper 序列化）；stepsJson 改為含 dynamic steps（transformer 需暴露收集到的 dynamic steps——`ExtractionResult` 加 `List<StepEvent> dynamicSteps` 最終狀態，或 orchestrator 以 doOnNext 收集 StepEvent——**擇 doOnNext 收集**（事件已在流上，不需重複狀態））；s3 fallback 語意：dynamic steps 出現過 → s3 in stepsJson 記 SUCCESS（照舊）+ dynamic steps 接續記錄
- Tests：AgentEventJsonTest 加兩型別序列化；provider 測試加 reasoning delta 案例（MockWebServer）；MessageControllerTest：FakeProvider 回含 `[[step:...]]` 與 ```questions 的流 → SSE 斷言 STEP d1/QUESTION 事件、DB 斷言 stepsJson 含 d1、questionsJson 非空；純 html 流 → 行為不變（回歸）

**Commit:** `feat(backend): question and thinking events, dynamic steps in orchestration`

---

### Task 4: 前端事件消化——types/parser/hook（前端）

**Files:**
- Modify: `types.ts`：AgentEvent union 加 QUESTION/THINKING；`Question` interface；`Message` 加 `questionsJson: string | null`（後端 MessageDto 同步——**注意**：MessageDto 加欄位屬 Task 3 漏項則本 task 補後端 DTO/mapper（@Mapping ignore + service 填充或直接 mapper 映射 entity 欄位），報告註明）
- Modify: `useAgentStream`：state 加 `thinking: string`（THINKING 累積）、`questions: Question[] | null`（QUESTION 事件）；reset 清空；STEP 已有 upsert 天然支援 d1/d2…
- Tests：parser/hook 測試加兩事件型別案例（thinking 累積、questions 設置、未知型別忽略回歸）

**Commit:** `feat(frontend): consume question and thinking events`

---

### Task 5: 前端 UI——動態 ThoughtChain + 思考展開 + 選項卡（前端）

**Files:**
- Modify: `StepChain`/`MessageBubble`：steps 含 d* 動態項照常渲染（ThoughtChain 已 data-driven，應近零改動——驗證即可）；「Worked through N steps」數字含 dynamic
- Create: `ThinkingPanel`（或併入 StepChain）：串流中顯示可展開「思考中…」（收合預設、等寬小字、自動捲底）；完成後保留可展開；歷史訊息不顯示（不持久化）
- Create: `QuestionCards.tsx`：`{ questions: Question[]; onAnswer: (text: string) => void; disabled?: boolean; answered?: string[] | null }`——單選：選項卡點擊即 `onAnswer(option)`；多選：checkbox 卡 + 確認鈕（answer = 選項以「、」join）；已回答（answered 非 null）→ 標記選中項 + 全體 disable
- Modify: `ChatPanel`/`MessageList`/`MessageBubble`：live questions（state.questions）渲染於串流 AI 氣泡尾部 → onAnswer 走既有 handleSend；歷史訊息 questionsJson parse 後渲染（answered 判定：該訊息之後存在 USER 訊息 → 視為已回答，answered 顯示該 USER 訊息文字匹配的選項或僅 disable——**從簡：僅 disable，不標記選中**，報告註明）
- Tests：QuestionCards（單選點擊回呼/多選確認/disabled）；ChatPanel 整合（QUESTION 事件 → 卡片出現 → 點擊觸發 send）；ThinkingPanel 展開收合

**Commit:** `feat(frontend): dynamic thought chain, thinking panel and question cards`

---

### Task 6: e2e + 收尾

- 後端全綠 + 前端全綠 + build
- Live e2e（minimax，.env 已設）：(1) 上傳 csv 模糊提問「幫我分析」→ 期望 QUESTION 選項卡（模型遵循度非保證——如實記錄；不出卡則驗純文字 fallback）；(2) 明確提問 → 觀察 dynamic steps 與 THINKING 是否出現於 SSE；(3) **迭代實測**：產出 v1 → 「把標題改成 XXX，其他都不要動」→ 人工比對 v1/v2 HTML diff 範圍（報告附 diff 行數與評估）
- compose 重建 backend+frontend、既有功能 smoke（上傳/對話/artifact/版本下拉）
- README/architecture.md 補新事件型別一句
- **Commit:** `test: M6 e2e verification` （+ docs）

---

## M6 完成定義

- [ ] 迭代微調：非目標區塊 diff 顯著縮小（e2e 報告佐證）；raw_html NULL 舊資料不炸
- [ ] 動態 steps 出現且重載重現；模型不配合 → 四步 fallback（測試釘住）
- [ ] QUESTION 選項卡：live 點選送出 + 歷史重現（disable 態）；純文字 fallback 不劣化
- [ ] THINKING 可展開；不持久化（as designed）
- [ ] 預算 guard：小 contextWindow 配置下回餵被略過（測試）
- [ ] 全既有測試綠（backend 144+ / frontend 94+）
