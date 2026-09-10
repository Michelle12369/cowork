# deepagent-service

**analysis 線的分析服務**（`ERD_AGENT_PROVIDER=langgraph-analysis` 時由 Java 端呼叫）。基於
[deepagents](https://github.com/langchain-ai/deepagents) harness 的 `create_deep_agent`——單一
agent + skills 漸進揭露 + 內建 planning/檔案工具——模型用 DuckDB 工具自行查資料，再直寫
self-contained HTML dashboard。目標模型 **qwen3.6-35B**。

與 llm api 線最大的差別在**資料怎麼進 HTML**：模型用 SQL 把統計算好，只把**被引用到的查詢結果**
注入 `window.__ERD_RESULTS__`，瀏覽器只做笨渲染（不像 llm api 線注入全量原始資料、由瀏覽器 JS 現算）。
文字結論與圖表數字因此同源，結構性消除抄錯。

圖表知識放在 **skills**（`skills/dashboard/`，漸進揭露），不是硬塞進 system prompt。

完整設計脈絡見 spec：
[`docs/superpowers/specs/2026-07-29-deepagent-dashboard-design.md`](../docs/superpowers/specs/2026-07-29-deepagent-dashboard-design.md)。

Java 端 `LangGraphAnalysisProvider` 只認 `/chat`（SSE）與 `/health` 兩個端點的 wire 契約，
`ERD_AGENT_ANALYSIS_BASE_URL` 指向哪個 host 就連哪個。`engine/` 層（`duck.py`、`theme.py` 等）
刻意是**複本而非共用套件**，與 backend 對應檔案同步，見各檔頭的 `MUST-sync` 註解。

## 前置需求

| 要裝 | 版本 | 說明 |
|---|---|---|
| [uv](https://docs.astral.sh/uv/) | 最新 | 唯一需要裝的 Python 工具 |
| ~~Python / pip~~ | **不用另外裝** | uv 自行管理 Python（`pyproject.toml` 的 `requires-python = ">=3.11"`；容器用 `python:3.11-slim`） |

⚠️ **所有 Python 指令一律走 `uv run`，NEVER 用 `pip install`**——相依由 `pyproject.toml` + `uv.lock`
鎖定（皆已進版控），只有 `uv sync` 會裝到正確版本。驗證：`uv --version`。

## 啟動 ①：本機直跑（localhost，建議）

首次安裝：

```bash
cd deepagent-service && uv sync
```

**建議用 `--env-file` 直接吃 `.env.local`**（repo 根目錄那份，本機開發專用）：

```bash
cd deepagent-service
uv run --env-file ../.env.local fastapi dev --port 8000 --reload-dir app
```

> `--reload-dir app` 把 auto-reload 的監看範圍限縮在 `app/` 原始碼——沒有它，agent 往
> workspace（如 `.local-workspace/`）寫出任何 `.py` 檔都會觸發 reload，殺掉進行中的 run。

也可以逐項指定（會覆蓋 `--env-file` 的值，適合臨時換模型試）：

```bash
cd deepagent-service
OPENAI_BASE_URL=https://openrouter.ai/api/v1 \
OPENAI_API_KEY=<your-openrouter-key> \
AGENT_MODEL=<OpenRouter 上的 qwen3.6-35b model id> \
AGENT_WORKSPACE_ROOT=/tmp/deepagent-workspace \
uv run fastapi dev --port 8000 --reload-dir app
```

> `OPENAI_BASE_URL` **必須含 `/v1` 後綴**（Python openai SDK 慣例）——與 Java 端的
> `ERD_AGENT_OPENAI_COMPATIBLE_BASE_URL`（**不含** `/v1`）格式不可互換。

服務會在 `http://localhost:8000` 起來，`/health` 應回 `{"status": "ok"}`。

再讓**本機直跑的 backend** 連上（`local` profile 會載入 `.env.local`）：

```bash
cd backend
ERD_AGENT_PROVIDER=langgraph-analysis \
ERD_AGENT_ANALYSIS_BASE_URL=http://localhost:8000 \
./mvnw spring-boot:run -Dspring-boot.run.profiles=local
```

> **疑難排解**：啟動前先 `lsof -ti:8000` 確認埠淨空——若埠被舊實例佔住，`fastapi dev` 只會在
> log 裡留下 `Address already in use`，而 `/health` 仍會由**舊代碼**回 200，測起來像新改動沒生效。

## 啟動 ②：docker（app stack 的 `deepagent` profile）

compose 會自動載入 repo 根目錄的 `.env`（docker 專用那份），不需要 `--env-file`：

```bash
docker compose -f docker-compose.app.yml \
  --profile deepagent up -d --build deepagent-service
```

再讓 backend 連上（改 `.env`）——注意 host 是**服務名**不是 localhost：

```bash
ERD_AGENT_PROVIDER=langgraph-analysis
ERD_AGENT_ANALYSIS_BASE_URL=http://deepagent-service:8000
```

其餘環境變數（`AGENT_MODEL`、`LANGFUSE_*` 等）見 `docker-compose.app.yml` 的
`deepagent-service` service 定義；`DEEPAGENT_MODEL` 可覆寫預設模型（`qwen3.6-35b`）。

## Workspace 佈局

`$AGENT_WORKSPACE_ROOT`（docker 內固定 `/data/workspace`，掛 named volume
`deepagent-workspace`；本機直跑用任意路徑）下，每個 user 一個目錄：

```
{userId}/
  skills/                       # 該 user 的個人 skill（M3 蒸餾產物落點；load 邏輯第一天就支援，
                                 # 蒸餾動作本身留 M3）
  sessions/
    {sessionId}/
      queries/{query_id}.sql    # 每次 run_sql 的 SQL 原文
      results/{query_id}.json   # 對應查詢結果（columns、以欄名為 key 的物件列 rows、truncated）
      dashboard.html            # 模型直寫的 self-contained dashboard（迭代一律整份 write_file 重寫）
      sources.md                # 本 turn 可用的資料來源（alias + fileType，供模型讀）
      notes.md                  # 模型自行維護的分析筆記（deepagents 內建檔案工具）
      todo.md                   # deepagents 內建 planning（write_todos）
      .skills/                  # 每 turn 重新 stage：builtin skills/ + {userId}/skills/（後者覆寫前者）
```

實際落地路徑另包一層 write-once generation 快照（`gen-{epochMillis}-{hex}/`）——
`local`／`s3`（`STORAGE_BACKEND`）兩種後端現在共用同一套機制，只差底層物件 client；完整位址
與 turn 生命週期見 `docs/architecture.md`「deepagent-service Workspace：檔案地圖與 Turn 生命週期」節。

`dashboard.html` 是否要發 `DASHBOARD_HTML` 事件，由 turn 結束時檔案 mtime 是否變動決定——
**不靠模型自己宣告**（見 `app/agent/chat_turn.py` 的 `ChatTurn` 內 mtime 快照比對）。

送出前沒有驗證關卡——只做主題改寫（`apply_erd_theme`，`app/engine/theme_rewrite.py`）與結果注入
（`inject_results`，物件列外包一層 Proxy，未知欄名/index 存取直接 throw）。真正的品質防線是
使用者觸發的瀏覽器修復（`POST /repair`），詳見 `docs/architecture.md`「deepagent-service
品質防線（注入契約 + 瀏覽器修復）」節。

## `POST /tool-call`——connector dashboard 檢視期的單次 MCP 呼叫

connector 模式的 dashboard 在檢視時透過 `mcp()` 現抓資料（不注入資料）；`/tool-call` 是
Java 代理（hop ③，尚未實作）最終會呼叫的那一站，跟 chat 模式共用同一條 `mcp_adapter.call_tool`
路徑，但不跑模型、不開 workspace、不碰 DuckDB、不落 `connector_calls.jsonl`。

```
POST /tool-call
Authorization: Bearer <AGENT_API_BEARER_TOKEN>     跟 /chat 一樣; 失敗回 401 {"error": "unauthorized"}
<SSO_TOKEN_HEADER>, <SSO_URL_HEADER>                跟 /chat 一樣的 header 名(Settings)
Body: { "connector": ConnectorSpec, "tool": "<name>", "args": {...} }
      ConnectorSpec 跟 /chat 的 connectors[] 元素同形: { id, name, url, bearerTokenKey? }
```

過了 bearer 驗證後一律回 200，只有 body 形狀不對才回 422（Java 折成 `INVALID_CALL`）：

```
200: { "data": <structured_content 原封不動> }
     | { "error": { "code": "AUTH"|"RETRYABLE"|"TOOL_ERROR"|"INVALID_CALL"|"CONNECTOR_UNAVAILABLE", "message": "<string>" } }
```

五個 code 誰能處理：

| `code` | 誰能動作 | 意思 |
|---|---|---|
| `AUTH` | viewer | 重新登入/重新整理 |
| `RETRYABLE` | viewer | 重試同一個呼叫 |
| `TOOL_ERROR` | model/editor | 呼叫的參數值有問題, `message` 是 MCP server 自己的錯誤文字 |
| `INVALID_CALL` | model/editor | 呼叫本身畸形(名字空的/args 不是物件) |
| `CONNECTOR_UNAVAILABLE` | connector owner | connector 端設定或形狀變了, 頁面與模型都做不了什麼 |

完整分類表（每一種失敗對到哪個 code、`message` 模板）見
[`docs/superpowers/specs/2026-09-10-mcp-error-codes-design.md`](../docs/superpowers/specs/2026-09-10-mcp-error-codes-design.md)。
每種 code 各一個真實範例（含一個成功範例）在
[`tests/fixtures/mcp_result_examples.json`](tests/fixtures/mcp_result_examples.json)——
Java 與前端可以直接載入這份 fixture，核對自己的折疊/顯示邏輯跟這裡的字串是不是同一套。

檢視時 dashboard 呼叫 `mcp()` 用的執行期程式碼（定義 `window.mcp`，把呼叫透過
`postMessage` 轉給宿主）是 `app/engine/results.py` 在生成／修復時注入的 `erd-mcp-runtime`
區塊（connector 模式才注入），跟結果注入用的 `erd-results-data` 區塊一樣：每次迭代或修復都先
從前一版 HTML 剝掉再重新注入一份乾淨的，不會進到 `check_dashboard` 檢查的 workspace 檔案裡。

## 測試

```bash
cd deepagent-service
uv run pytest -q       # 單元 + 事件橋接 + FastAPI 契約測試（fake chat model，不打真 LLM）
uv run ruff check .    # 含 engine/ 層 banned-api 檢查（禁止 import langchain*/langgraph/deepagents）
```
