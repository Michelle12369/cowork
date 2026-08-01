# deepagent-service 手動實驗驗收（spec §7）

不進 CI，人工跑。目的：判斷「deepagents harness + skills + qwen3.6-35B」是否可取代已退役的
`agent-service`（LangGraph 手建 + declarative spec + gpt-oss；該服務已於 2026-07-30 退役移出
repo，git tag `pre-agent-service-removal` 可復原重跑）的品質策略——結論寫回
`.superpowers/sdd/progress.md` 與
[spec](../../docs/superpowers/specs/2026-07-29-deepagent-dashboard-design.md) 附錄。

## 前置

- 範例 CSV：同一份用於 `agent-service` 對照過的資料集（例如既有 M2.5 live 驗收用的異常/系統
  資料），確保 Q1–Q5 與 agent-service 的既有（退役前記錄的）結果可直接比較。
- 模型：`qwen3.6-35b` via OpenRouter（`OPENAI_BASE_URL=https://openrouter.ai/api/v1`）。
- 開 Langfuse tracing（設好 `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY`/`LANGFUSE_HOST`）——
  判準 2、4 需要看 trace。
- 每題跑在**獨立 session**（`sessionId` 不重複），Q4/Q5 除外——這兩題本身就是測「同一
  session 內多輪」，見各自小節。

## 判準（spec §7，五項）

1. **流程完成率**：schema → SQL → 結論 → dashboard 全程跑完，`recursion_limit`（預設 50）內
   不空轉、不卡死在無效工具呼叫迴圈。
2. **skill 有效性**：Langfuse trace 能看到 `read_file` 讀了 `.skills/dashboard/SKILL.md`（或
   其 `references/*.md`），且產出的 HTML 遵守 `html-contract.md` 的契約（self-contained、
   `__ERD_RESULTS__[query_id]` 讀資料、`echarts.init(el, 'erd')`）。
3. **數字一致**：dashboard 上呈現的數字（KPI、圖表數值、文字結論引用的數字）與
   `{userId}/sessions/{sessionId}/results/*.json` 的對應查詢結果**逐一核對相符**。
4. **迭代品質**：追問/修改請求是否讓模型改用 `edit_file` 局部修改既有 `dashboard.html`（而非
   整份重寫），且改後 `check_dashboard_html` guard 仍過（不觸發修復迴路，或修復迴路一輪內
   收斂）。
5. **與退役 renderer 版對照**：比對該題在已退役 `agent-service`（gpt-oss + declarative
   renderer，git tag `pre-agent-service-removal` 可復原重跑取得即時結果）上的既有結果或記錄，
   主觀評比洞察品質與圖表正確性（結論是否等價、圖表選型是否合理、有無 deepagent-service
   特有的多花/漏花現象）。

判準欄位記法：`✅ 通過` / `⚠️ 部分`（附一行原因）/ `❌ 不通過`（附一行原因）/ `N/A`（該題不適用，
如 Q1–Q3 首輪無迭代，判準 4 記 N/A）。

## 題目

### Q1（固定題）：哪個系統最需要改善？

整體診斷型問題，測試模型能否自主決定要查哪些欄位、如何綜合多個指標排序。不指定分析角度，
最貼近使用者真實會問的模糊問題。

| 判準 | 結果 | 備註 |
|---|---|---|
| 1. 流程完成率 | | |
| 2. skill 有效性 | | |
| 3. 數字一致 | | |
| 4. 迭代品質 | N/A（首輪） | |
| 5. 與退役 renderer 版對照 | | |

### Q2：趨勢分析

例：「近三個月異常數量的變化趨勢如何？有沒有明顯上升或下降的時段？」

測試模型是否會用 `date_trunc`/時間分組 SQL、並選擇合理的趨勢圖型（折線／面積），而非把時間
當類別軸畫長條圖。

| 判準 | 結果 | 備註 |
|---|---|---|
| 1. 流程完成率 | | |
| 2. skill 有效性 | | |
| 3. 數字一致 | | |
| 4. 迭代品質 | N/A（首輪） | |
| 5. 與退役 renderer 版對照 | | |

### Q3：分組比較

例：「各系統／各類別的異常次數相比如何？哪幾個明顯偏高？」

測試分組聚合 SQL（`GROUP BY` + 排序）與對應圖型（長條圖排序、避免圓餅圖類別過多）選型是否
遵守 skill 的 `chart-rules.md`（form-first、Hard NOs）。

| 判準 | 結果 | 備註 |
|---|---|---|
| 1. 流程完成率 | | |
| 2. skill 有效性 | | |
| 3. 數字一致 | | |
| 4. 迭代品質 | N/A（首輪） | |
| 5. 與退役 renderer 版對照 | | |

### Q4：迭代修改 dashboard（同一 session 兩輪）

- **Turn 1**：「幫我畫出各系統的異常次數分布」（產出 dashboard）
- **Turn 2**：「圖表改成用嚴重度分層上色，並依數值由高到低排序」

Turn 2 是本題重點——檢查模型是否用 `edit_file` 對既有 `dashboard.html` 做局部修改（而非
`write_file` 整份重吐），且改動後 guard 仍過、`__ERD_RESULTS__` 綁定沒被破壞。

| 判準 | Turn 1 結果 | Turn 2 結果 | 備註 |
|---|---|---|---|
| 1. 流程完成率 | | | |
| 2. skill 有效性 | | | |
| 3. 數字一致 | | | |
| 4. 迭代品質 | N/A | | Turn 2 是否用 edit_file 局部改；guard 是否觸發修復迴路 |
| 5. 與退役 renderer 版對照 | | | |

### Q5：跨 turn 追問（同一 session 兩輪，承接 Q1 結論深挖）

- **Turn 1**：同 Q1「哪個系統最需要改善？」
- **Turn 2**：「那個系統的問題主要集中在哪個時間段或類別？」（不指名系統，測試模型是否能從
  對話記憶正確回溯 turn 1 結論中點名的系統，重新下 SQL 深挖，而非要求使用者重複資訊）

測試 checkpoint 對話記憶（`thread_id=sessionId`）是否讓模型正確承接上一輪結論，並產生**新的**
分析（新 SQL/新 dashboard 區塊），而非單純複誦。

| 判準 | Turn 1 結果 | Turn 2 結果 | 備註 |
|---|---|---|---|
| 1. 流程完成率 | | | |
| 2. skill 有效性 | | | |
| 3. 數字一致 | | | |
| 4. 迭代品質 | N/A | | Turn 2 是否正確承接 turn 1 的系統結論、是否需要使用者重複資訊 |
| 5. 與退役 renderer 版對照 | | | |

## 總結（跑完 5 題後填）

- **整體結論**：deep agent harness 在 qwen3.6-35B 上是否成立？
- **主要失敗模式**（如有）：
- **與已退役的 agent-service（renderer 版）相比的取捨**：
- **決定**：深化 deep agent ／ 退回 skills-only ／ 其他
