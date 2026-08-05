# Deep Agent Dashboard 實驗 — 設計文件

- **日期**：2026-07-29
- **分支**：`feat/deepagent-service`（自 `feat/data-insight-agent` @ `ed6b447` 切出；目標未來可 merge，按正式工程標準開發）
- **狀態**：設計已與使用者逐段確認

## 1. 背景與目的

現有 `agent-service`（LangGraph 手建 StateGraph + declarative spec + 確定性 renderer）是 gpt-oss 前提下的品質策略。本實驗前提改變：**模型換為 qwen3.6-35B**，重新驗證 deep-agent harness 是否成立。

建立第二個 Python agent 服務 `deepagent-service/`，採用 **LangGraph deepagents** 套件：

- agent 具備資料分析能力（DuckDB SQL），使用者問分析問題（如「哪個系統最需要改善」），對話中以文字呈現結論、以 HTML dashboard 佐證；
- dashboard 知識放在 **skills**（蒸餾自 Java `openai/system-prompt.vm` ＋ dataviz skill），驗證「知識放 skills 而非 prompt 硬塞」在 35B 模型上是否成立；
- 分析過程落檔保存，作為未來 M3（從對話蒸餾分析手法 skill、每日免 LLM 重跑）的原料與容器格式；
- 接上現有 Java `LangGraphAnalysisProvider`，**Java 端零程式碼改動**。

## 2. 決策記錄（與使用者確認）

| 決策點 | 結論 | 理由摘要 |
|---|---|---|
| dashboard 產出路線 | 直寫 self-contained HTML（不走 spec+renderer） | 最貼近 deepagents 精神；skill 素材本來就是直寫 HTML 的教學；品質風險正是實驗要驗證的 |
| 服務形態 | 新資料夾獨立 service，與 agent-service 並列、互不 import | 實驗完全隔離；只共用 /chat wire 契約 |
| 分析工具 | DuckDB SQL 工具（get_schema/run_sql/preview）＋ deepagents 檔案系統 | SQL 可錄可重播、直接餵 M3 凍結；35B 寫 SQL 比寫任意 Python 可靠 |
| 過程落檔 | 持久化到 per-user/per-session 工作目錄 | 短期記憶（context offloading）兼 M3 蒸餾原料；重播功能本身留 M3 |
| 資料進 HTML 的方式 | **`window.__ERD_RESULTS__`（內嵌查詢結果）**，不用 `__ERD_DATA__`（全量原始資料） | 殺「文字與圖表數字對不上」整類 bug；不受原始資料量級限制（未來接 API/大 CSV）；瀏覽器 JS 只做笨渲染；每日重跑＝重跑凍結 SQL → 注入同一份 HTML，零 LLM |
| erd 主題注入 | **Python 端注入**（不靠 Java head-inject） | 服務產出自足可預覽（開發免起 Java）；M3 pipeline 重跑在 Python 側需要它；代價＝色票複製一份（MUST-sync 註解，先例 `charts.py`）＋ Java 冪等 double-inject（無害，多 2–3KB） |
| 個人 skill 與 k8s | 檔案是 agent 介面、持久層抽換（`WorkspaceStore`：v1 local passthrough / internal 環境 S3 lazy pull + turn 邊界 push） | pod 磁碟 ephemeral、replica 不共享；S3 是 internal 既定路線（`S3FileStorage`、`AGENT_S3_*`） |
| harness 方案 | deepagents 原廠 `create_deep_agent`、單一 agent＋skills（不用 subagent 分工、不手建 StateGraph） | 實驗訊號最純；subagent 委派對 35B 變因過多，留作成功後加碼 |
| 模型 | `$AGENT_MODEL` 預設 qwen3.6-35b，`OPENAI_BASE_URL` 同 agent-service env 模式；dev 走 OpenRouter | 設定即插拔，internal 環境換 base_url 即可 |

## 3. 架構與元件

```
deepagent-service/
  app/
    main.py              # FastAPI:/chat SSE + /health（wire 契約與 agent-service 完全相同）
    agent/
      graph.py           # create_deep_agent(): 模型、工具、skills、filesystem backend 組裝（per-request）
      prompts.py         # 精簡 system prompt（角色+流程+何時讀 skill；細節都在 skills）
      tools/data.py      # get_schema / run_sql / preview（DuckDB，唯讀鎖定）
      events.py          # astream_events → SSE 事件橋接（含 15s heartbeat）
    engine/              # 純運算層，禁 import langchain*/langgraph/deepagents（ruff banned-api）
      duck.py            # DuckDB 連線（唯讀、memory_limit、S3 httpfs，仿 agent-service）
      workspace.py       # WorkspaceStore 抽象 + 工作目錄佈局 + 路徑逃逸防護
      results.py         # run_sql 結果落檔 results/{query_id}.json + __ERD_RESULTS__ 注入
      html_guard.py      # dashboard.html 確定性檢查與小修（§6）
      theme.py           # erd 8 色主題 script block（逐字複製自 head-inject.vm，MUST-sync）
  skills/
    dashboard/
      SKILL.md           # 入口：何時做 dashboard、工作流程、產出契約一頁摘要、預設版面
      references/
        html-contract.md # self-contained 規則、__ERD_RESULTS__ 讀資料、echarts.init(el,'erd')
        chart-rules.md   # form-first 選型、dataviz method、Hard NOs、單位格式化
        examples.md      # 1–2 個最小完整 dashboard 範例（35B 品質槓桿最大的一份）
  tests/
  pyproject.toml / Dockerfile
```

**工作目錄佈局**（`$AGENT_WORKSPACE_ROOT`，`AGENT_WORKSPACE_BACKEND=local|s3`）：

```
{userId}/
  skills/                # 該 user 的個人 skill（M3 蒸餾產物落點；載入邏輯第一天就支援）
  sessions/{sessionId}/  # deepagents 檔案系統掛載點：todo.md、queries/*.sql、
                         # results/*.json、notes.md、dashboard.html、sources.md
```

**要點**：

- wire 契約 100% 沿用：`/chat` 收 `sessionId/userId/message/history/sources/previousDashboardSpec`（最後者收下忽略——迭代狀態在 workspace 的 `dashboard.html`），SSE 吐 `STEP/TOKEN/ANSWER/TABLE/ERROR` ＋內部信號 `DASHBOARD_HTML {html, spec:null}`。MVP 不發 `QUESTION`（ask_user 反問不在範圍）。
- **agent per-request 組裝**：每次 `/chat` 依 `userId` 組 skill 路徑＝內建 `skills/` ＋ `{userId}/skills/`；graph 建構無 LLM 呼叫，成本可忽略。
- 對話記憶：`InMemorySaver` checkpointer、`thread_id=sessionId`；history 僅在 checkpoint 不存在時重建（同 agent-service 語意）。
- Java 端唯一動作：`ERD_AGENT_ANALYSIS_BASE_URL` 指向 `deepagent-service:8000`。docker-compose 新增 `profiles:["deepagent"]` 的 service 定義，預設不啟動。
- deepagents 內建 planning（todo）、檔案工具、skill 漸進揭露一律用原廠，不自造。

## 4. 資料流

單次 `/chat` 生命週期：

1. **進場**：`WorkspaceStore` 確保（必要時自 S3 pull）`{userId}/sessions/{sessionId}/`；`sources[]` 寫成 `sources.md`；DuckDB 以唯讀 view 掛上各檔案。
2. **跑 agent**：`astream_events(version="v2")` 串流。
3. **事件橋接**（`events.py`）：工具起訖→`STEP`（deepagents 內建工具也給有意義的中文步驟名：`write_todos`→「規劃分析步驟」、寫 `dashboard.html`→「組裝儀表板」、讀 skill→「載入繪圖技法」、SQL→「查詢資料」）；user-facing token→`TOKEN`；`run_sql` 結果→`TABLE`；終局→`ANSWER`；15s 無事件→heartbeat STEP。
4. **結果落檔**：每次 `run_sql` 成功，結果自動寫 `results/{query_id}.json`（含 columns/rows/truncated），SQL 原文寫 `queries/{query_id}.sql`。
5. **dashboard 偵測**：stream 結束後以 mtime 判斷 `dashboard.html` 本 turn 是否被寫入——**發不發 dashboard 由檔案決定，不靠模型宣告**。
6. **注入與回傳**：`html_guard` 檢查（§6）→ `results.py` 把**被引用的**查詢結果注入成 `<script>window.__ERD_RESULTS__ = {...}</script>`（含 `</script>` escape）＋ `theme.py` 注入 erd 主題 block → 發 `DASHBOARD_HTML`。
7. **turn 收尾**：`WorkspaceStore` 把本 turn 寫入的檔案 push 回持久層（s3 backend 時）。
8. **Java 端（零改動）**：攔截 DASHBOARD_HTML → `ArtifactAssembler.assemble()`（HTML 無 `__ERD_DATA__` 標記→跳過原始資料注入；有 `echarts` 標記→再注入一次 head-inject，冪等無害）→ 存 FileStorage → 發 `ARTIFACT`。

**資料通道**：agent 用 SQL 決定「說什麼、畫什麼」；HTML 圖表從 `__ERD_RESULTS__[query_id]` 拿聚合好的列做**笨渲染**，不在瀏覽器算統計。文字與圖表數字同源（同一份 SQL 結果），結構性消除抄錯。

**迭代 turn**：agent 從 checkpoint 有對話、從 workspace 讀回 `notes.md`/`dashboard.html`；修改＝`edit_file` 局部改而非整份重吐（35B 品質關鍵）。

## 5. Skills 設計

格式＝deepagents 原生慣例：`SKILL.md`（frontmatter `name`+`description`＋精簡正文）＋`references/*.md`。三層漸進揭露（description → SKILL.md → reference）控制 context 消耗。**此目錄格式即 M3 蒸餾手法的容器**：未來個人分析劇本＝往 `{userId}/skills/` 放資料夾，機制零新增。

| 檔案 | 內容 | 來源 |
|---|---|---|
| `SKILL.md` | 何時做 dashboard、流程（先 SQL 後畫圖、結果落檔、寫 dashboard.html、迭代用 edit_file）、契約一頁摘要、預設版面（KPI 上／圖中／表下） | `system-prompt.vm` 流程段＋GATHER_PROMPT 版面段改寫 |
| `references/html-contract.md` | self-contained（Tailwind+ECharts CDN、單檔）、`__ERD_RESULTS__[query_id]` 讀資料、`echarts.init(el,'erd')` 不自帶色票、resize handler、繁中文案 | `system-prompt.vm` 契約段按 `__ERD_RESULTS__` 重寫 |
| `references/chart-rules.md` | form-first 選型、encoding 職責、emphasis 模式、series ladder、Hard NOs（禁 dual y-axis／pie>6／截斷 bar 軸）、單位與格式化 | `system-prompt.vm` §121–189 ＋ dataviz skill 原文補強 |
| `references/examples.md` | 基本盤（KPI+bar+table）與進階（`__ERD_RESULTS__` 綁定+erd 主題）各一個最小完整範例 | 新寫 |

**明確不搬**：`[[step:]]` 進度標記（進度由工具 STEP 事件承載）、九種圖型 spec schema（declarative 路專用）、色票 hex（`theme.py` 單一落點，skill 只教「用 `'erd'` 主題」）。

**System prompt 保持薄**：角色（資料分析師）、語言（繁中）、基本流程、誠實原則。所有「怎麼畫」在 skill——這是實驗核心命題。

## 6. 錯誤處理與 35B 護欄

每層皆確定性程式碼，不靠模型自律：

**工具層**
- `run_sql`：唯讀連線＋`memory_limit`＋timeout（照抄 `duck.py` 鎖定參數）；wire 截斷同 `TABLE_EVENT_MAX_ROWS`，落檔另設較高上限並標記 `truncated`。
- 查詢結果餵回模型前過 framing（沿用 `framing.py` 概念：資料內容不可信，防欄位藏注入）。
- 檔案工具 root 釘死 session 工作目錄，正規化後拒絕 `../` 逃逸；單檔大小與總數上限。

**迴圈層**
- `recursion_limit` env 可調（預設 50；deep agent 步數較多，實驗期觀察再調）。
- 首輪空回應重試（上限 2，照 `GATHER_RETRY_MAX_RUNS` 先例）。
- 15s heartbeat 維持 Java Reactor `.timeout()`（事件間隔制）不斷線；整體 wall time 由 `ERD_AGENT_ANALYSIS_REQUEST_TIMEOUT_SECONDS` env 調整，不改 Java。

**產出層（`html_guard.py`）**
- 引用完整性：HTML 引用的每個 `__ERD_RESULTS__["qN"]` 必須有本 turn 落檔結果；未引用的結果不注入。
- 結構底線：非空、有 `<div`、體積上限；`<script src>` 白名單（僅 Tailwind/ECharts 既定 CDN）。
- 主題小修：用了 echarts 但 `echarts.init` 未帶 `'erd'` → 確定性改寫補上。
- 失敗＝有限修復迴路：guard 錯誤餵回 agent 修 1 輪（先例 `SYNTHESIZE_REPAIR_MAX_RUNS`）；再失敗→放棄 dashboard、只出文字 ANSWER＋`STEP ERROR`「儀表板組裝失敗」。寧可沒圖不出壞圖。

**事件層**
- 未捕捉例外→`ERROR {code, message}` 繁中；`GraphRecursionError`→「分析步驟過多」友善文案。
- ANSWER 空白但有 dashboard→Java `DEFAULT_DASHBOARD_ANSWER` 兜底（現有機制）。

## 7. 測試與驗收

**自動化（pytest，merge gate 全綠）**
- 單元：`html_guard` 每條規則正反例；`workspace.py`（逃逸拒絕、目錄佈局、local passthrough）；`tools/data.py`（唯讀、截斷、framing）；`results.py`（注入 block、escape、只注入被引用者）。
- 事件橋接：fake `astream_events` 序列 → 斷言 SSE 映射（STEP/TOKEN/TABLE/ANSWER/heartbeat）。
- 契約（end-to-end）：scripted fake chat model（預錄 tool-call 序列）打真 FastAPI app，斷言完整事件流與 `DASHBOARD_HTML`（結果+主題已注入、spec=null）。
- 分層：ruff banned-api——`engine/` 禁 langchain*/langgraph/deepagents。
- S3 backend 測試留待 internal 整合（v1 只測 local）。
- Java/前端零改動；docker-compose 用 profile 隔離。

**實驗驗收（手動 eval，不進 CI）**：範例 CSV、3–5 題代表性分析題、qwen3.6-35B via OpenRouter、Langfuse tracing。判準：

1. 流程完成率（schema→SQL→結論→dashboard，recursion limit 內不空轉）；
2. skill 有效性（trace 證明讀了 skill 且 HTML 遵守契約）；
3. 數字一致（圖上數字＝`results/*.json`）；
4. 迭代品質（追問修改走 `edit_file` 局部改、改後仍過 guard）；
5. 與現有 agent-service（gpt-oss+declarative）同題對照，主觀評洞察與圖表正確性。

結論寫回 progress.md／本 spec 附錄，決定「深化 deep agent」或「退回 skills-only」。

## 8. 範圍外（明確不做）與 M3 交接注意

**MVP 不做**：重播/每日 pipeline 端點（`/run-pipeline`）、ask_user 反問（QUESTION 事件）、subagent 分工、S3 WorkspaceStore 實作與測試（介面先留）、記憶壓縮（compaction；session 過長先靠 recursion limit 擋，實驗觀察後決定）。

**M3 交接注意**：
- 蒸餾原料＝`{userId}/sessions/*/`（queries/results/notes/dashboard.html）；容器＝`{userId}/skills/{skill-name}/`。
- 每日重跑＝重跑凍結 SQL → `results.py` 注入同一份 HTML（含主題），零 LLM——注入邏輯已為此設計成獨立 engine 模組。
- 跨 session 併發寫同一 user skills 的一致性，蒸餾功能實作時處理（目前 turn 邊界 last-write-wins）。
- `theme.py` 色票與 `head-inject.vm`／`charts.py` 三處 MUST-sync（槽位順序 NEVER 重排）。
