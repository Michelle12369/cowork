# M6 Artifact 迭代與互動 — Design Spec（v2）

**日期**：2026-07-06（v2 修訂：使用者裁決 DSL 降級為備援，先驗證 HTML 回餵路線）
**狀態**：核准方向，執行中
**前置**：M1–M4 已合入 master；M5（codegen 骨架）暫停
**目標一句話**：以「前版 HTML 回餵」解迭代走樣、以「動態 steps + 結構化提問 + 思考串流」解互動呆板——輕量路線先行，效果不足再啟動 DSL milestone。

## 1. 痛點與 v2 對策

| # | 痛點 | v2 對策 | 若效果不足 |
|---|------|---------|-----------|
| P1 | 產出漏東西 | M4 已上 prompt hardening + bare-html fallback；本輪不加碼 | DSL milestone（見 §7 備援） |
| P2 | 微調整份走樣 | **前版 raw HTML 回餵** + 最小變更指令 | DSL spec 回餵 |
| P3 | 小 context 模型爆量 | 回餵時的簡易預算 guard（超限自動略過回餵） | 完整 PromptBudgeter |
| P4 | 互動呆板：固定四步、純文字提問、無思考過程 | **動態 steps 標記協定 + QUESTION 事件 + THINKING 串流** | — |

## 2. 前版 HTML 回餵（P2）

- **V5 migration**：`artifact` 加 `raw_html CLOB NULL`（注入前的原始 HTML；既有 rows 為 NULL → 迭代退化為現行為）
- finalize 存 artifact 時同時存 raw_html（fence 軌與 bare-html fallback 軌皆存；codegen 軌未來自理）
- **prepare 階段**：載入該 session 最新 artifact 的 raw_html → `AgentRequest` 新欄位 `previousArtifactHtml`（nullable）
- **PromptBuilder**：previousArtifactHtml 非 null 時，user prompt 附段落：

  ```
  ## previous dashboard html (the user is iterating on this)
  ```html
  {previousArtifactHtml}
  ```
  Modify ONLY what the user asked for. Keep all other markup, layout, colors,
  chart configs and text byte-identical. Output the complete updated HTML.
  ```

- **預算 guard**：config `erd.agent.openai-compatible.context-window`（預設 131072）、`erd.agent.anthropic.context-window`（預設 200000）；估算（字元/3.5）prompt 總量 > window×0.5 時**略過回餵**並 log.warn（v1 不做完整降級鏈）
- Regenerate 沿用此機制（帶前版 → 「重生」自然變成保守重生）

## 3. 動態 steps（P4：取代呆板四步）

**標記協定**：system prompt 指示模型在生成過程中，於**獨立一行**輸出進度標記：

```
[[step: 分析 Vt 分佈與管制界線]]
```

- 指示：開始實質工作前發 2–5 個標記，各標記代表接下來要做的事（繁中、≤20 字）；標記行不算在說明文字內
- **Transformer 擴充**（沿用跨 token 緩衝狀態機）：偵測行首 `[[step:` … `]]` → 抽出 title，不外流 TOKEN → 發 `StepEvent("d{n}", title, null, RUNNING)`，同時把前一個 dynamic step 改發 SUCCESS
- **Orchestrator**：固定步驟縮為 `s1 Reading imported files`、`s2 Analyzing data profile`（真實後端動作，保留）；`s3 Generating dashboard` 改為「模型無標記時的 fallback」——收到第一個 dynamic step 前先發 s3 RUNNING，若整流有 dynamic steps 則 s3 於第一個標記時標 SUCCESS（title 改「Planning」語意不變更，實作從簡：s3 保留原題）、dynamic steps 接續；結尾 `s4 Rendering dashboard` 照舊。stepsJson 持久化含 dynamic steps（重載完整重現）
- 模型不配合 → 零標記 → 行為與今天完全相同

## 4. QUESTION 事件（P4：結構化釐清）

- `AgentEvent` 新增 `QuestionEvent(List<Question> questions)`；`Question(String text, List<String> options, boolean multiSelect)`
- 協定：釐清模式輸出 ```` ```questions ```` fenced block（JSON array，few-shot 範例進 system prompt）；transformer 抽取（不外流 TOKEN）→ QuestionEvent；AI 訊息存 `questionsJson`（V5 一併加 `chat_message.questions_json CLOB NULL`；重載重現選項卡）
- 前端：選項渲染為可點卡片（antd）；單選點擊即以選項文字送出、多選加確認鈕；已回答標記選擇並 disable；答案走既有 send 流
- 模型不輸出 block → 純文字提問照舊（增強型協定）

## 5. THINKING 事件（P4：思考過程）

- OpenAI-compatible SSE `choices[].delta.reasoning`（OpenRouter reasoning 模型）→ 新增 `ThinkingEvent(String delta)`
- 前端：ThoughtChain 掛可展開「思考中…」節點，串流累積、預設收合；**不持久化**（重載不重現；spec 明文）
- Anthropic provider v1 不接（延後）

## 6. 相容性

| 項目 | 處理 |
|---|---|
| 既有 artifacts（raw_html NULL） | 迭代無回餵 = 現行為 |
| 事件契約 | 新增 QUESTION/THINKING 型別；前端 parser 未知型別本就忽略（向後安全） |
| codegen 軌 | 不受影響；dynamic steps/questions 對其為 no-op |
| 弱模型標記/JSON 遵循度 | 全部增強型協定：不遵循 = 今天的行為 |

## 7. 備援：DSL 渲染管線（本輪不做）

v1 spec 的完整 DSL 設計（spec JSON + Schema 驗證 + DashboardRenderer + 修復迴圈）保留於 git 歷史（本檔 v1 版本）。**啟動條件**：HTML 回餵實測後「微調走樣」或「漏東西」仍不可接受 → 開獨立 milestone 實作。屆時 P3 的完整 PromptBudgeter 一併納入。

## 8. Milestone 切分與完成定義

- **6a 迭代回餵**：V5 + raw_html 儲存 + AgentRequest/PromptBuilder 回餵 + 預算 guard
- **6b 互動**：transformer 標記/questions 抽取 + 三個新事件 + orchestrator 動態步驟 + 前端（ThoughtChain 動態化、思考展開、選項卡）

完成定義：
- [ ] 「把 X 改成 Y」微調實測（minimax）：非目標區塊視覺穩定（人工比對兩版）
- [ ] 生成過程出現模型自述的動態步驟；不配合時 fallback 四步
- [ ] 模糊需求 → 選項卡；點選自動送出並續流
- [ ] reasoning 過程可展開；重載後 steps/questions 重現、thinking 不重現（as designed）
- [ ] 全數既有測試綠（backend 144+、frontend 94+）
