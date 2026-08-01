# deepagent-service

**實驗服務**——驗證 [deepagents](https://github.com/langchain-ai/deepagents) harness（原廠
`create_deep_agent`：單一 agent + skills 漸進揭露 + 內建 planning/檔案工具）在模型換成
**qwen3.6-35B** 前提下，是否能取代已退役的 `agent-service`（LangGraph 手建 StateGraph +
declarative spec + 確定性 renderer）的品質策略。`agent-service` 已於 2026-07-30 退役移出
repo，git tag `pre-agent-service-removal` 可復原對照；下方對照表記錄的是退役前兩服務並列
時的架構比較。

完整設計脈絡見 spec：
[`docs/superpowers/specs/2026-07-29-deepagent-dashboard-design.md`](../docs/superpowers/specs/2026-07-29-deepagent-dashboard-design.md)。

與已退役的 `agent-service` 的關鍵差異：

| | `agent-service`（已退役，tag `pre-agent-service-removal`） | `deepagent-service`（本服務，實驗） |
|---|---|---|
| harness | 手建 LangGraph StateGraph（gather/synthesize 節點） | deepagents `create_deep_agent`（單一 agent） |
| dashboard 產出 | declarative spec（9 種圖型 schema）→ 確定性 renderer | 模型直寫 self-contained HTML |
| 圖表知識放哪 | system prompt 硬塞（`openai/system-prompt.vm`） | **skills**（`skills/dashboard/`，漸進揭露） |
| 資料進 HTML | `window.__ERD_DATA__`（全量原始資料，瀏覽器算統計） | `window.__ERD_RESULTS__`（agent 用 SQL 算好的聚合結果，瀏覽器只做笨渲染） |
| 目標模型 | gpt-oss | qwen3.6-35B |

`/chat` SSE 與 `/health` 的 wire 契約與已退役的 `agent-service` **完全相同**——這也是為什麼
Java 端 `LangGraphAnalysisProvider` 當初能**零程式碼改動**切換，只需把
`ERD_AGENT_ANALYSIS_BASE_URL` 指向這個服務。兩個服務刻意互不 `import`（`engine/duck.py`、
`engine/theme.py` 等有 `MUST-sync` 註解的複本，而非共用套件；同步對象隨 agent-service 退役
已改為與 backend 對應檔案同步，見各檔頭註解）。

## 啟動（docker compose）

```bash
docker compose --profile deepagent up -d --build deepagent-service
```

再讓 backend 連上這個服務（`.env` 或環境變數）：

```bash
ERD_AGENT_PROVIDER=langgraph-analysis
ERD_AGENT_ANALYSIS_BASE_URL=http://deepagent-service:8000
```

`deepagent-service` 是目前唯一的 analysis-mode 服務（已退役的 `agent-service` 曾與其走同一個
wire 契約，`ERD_AGENT_ANALYSIS_BASE_URL` 指向哪個 host，backend 就連哪個——這個機制原樣保留）。

其餘環境變數（`AGENT_MODEL`、`LANGFUSE_*`）比照已退役的 `agent-service`，見
`docker-compose.yml` 的 `deepagent-service` service 定義；`DEEPAGENT_MODEL` 覆寫預設模型
（`qwen3.6-35b`），與 `agent-service` 的 `AGENT_MODEL` 分開命名——歷史原因是曾用於兩服務
同時起、各自指定不同模型比較（`agent-service` 現已退役，命名維持不變）。

## Dev 直跑（不進 docker，走 OpenRouter）

```bash
cd deepagent-service
OPENAI_BASE_URL=https://openrouter.ai/api/v1 \
OPENAI_API_KEY=<your-openrouter-key> \
AGENT_MODEL=<OpenRouter 上的 qwen3.6-35b model id> \
AGENT_WORKSPACE_ROOT=/tmp/deepagent-workspace \
uv run fastapi dev
```

服務會在 `http://localhost:8000` 起來，`/health` 應回 `{"status": "ok"}`。

## Workspace 佈局

`$AGENT_WORKSPACE_ROOT`（docker 內固定 `/data/workspace`，掛 named volume
`deepagent-workspace`；dev 直跑用任意本機路徑）下，每個 user 一個目錄：

```
{userId}/
  skills/                       # 該 user 的個人 skill（M3 蒸餾產物落點；load 邏輯第一天就支援，
                                 # 蒸餾動作本身留 M3）
  sessions/
    {sessionId}/
      queries/{query_id}.sql    # 每次 run_sql 的 SQL 原文
      results/{query_id}.json   # 對應查詢結果（columns/rows/truncated）
      dashboard.html            # 模型直寫的 self-contained dashboard（迭代用 edit_file 局部改）
      sources.md                # 本 turn 可用的資料來源（alias + fileType，供模型讀）
      notes.md                  # 模型自行維護的分析筆記（deepagents 內建檔案工具）
      todo.md                   # deepagents 內建 planning（write_todos）
      .skills/                  # 每 turn 重新 stage：builtin skills/ + {userId}/skills/（後者覆寫前者）
```

`dashboard.html` 是否要發 `DASHBOARD_HTML` 事件，由 turn 結束時檔案 mtime 是否變動決定——
**不靠模型自己宣告**（見 `app/main.py` 的 `dashboard_mtime_before/after` 比對）。

## 測試

```bash
cd deepagent-service
uv run pytest -q       # 單元 + 事件橋接 + FastAPI 契約測試（fake chat model，不打真 LLM）
uv run ruff check .    # 含 engine/ 層 banned-api 檢查（禁止 import langchain*/langgraph/deepagents）
```

## 手動實驗驗收

3–5 題代表性分析題（真跑 qwen3.6-35B via OpenRouter，開 Langfuse tracing 對照 trace），
判準與記錄表見 [`eval/questions.md`](eval/questions.md)。結論回寫
`.superpowers/sdd/progress.md` 與 spec 附錄，決定「深化 deep agent」或「退回 skills-only」。
