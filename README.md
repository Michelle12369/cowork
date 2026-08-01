# eRD Cowork · Data Studio

Agent chatbot：上傳 CSV/Excel + prompt → HTML dashboard。對話右欄自動渲染 dashboard（iframe），資料由後端注入 `window.__ERD_DATA__`，圖表引擎 ECharts CDN，支援 Regenerate 與全螢幕。SSE 串流新增 `QUESTION`（釐清問題選項卡）、`THINKING`（模型推理展開面板）、`d*`（LLM 動態任務步驟）事件，以及迭代微調（`baseArtifactId` 指定基底 artifact，縮小 diff 範圍）。

架構說明：[docs/architecture.md](docs/architecture.md)

## 一鍵啟動

    docker compose up -d --build

- 前端：http://localhost:3000
- 後端 health：http://localhost:8080/actuator/health（預設 8080；可在 `.env` 設 `BACKEND_PORT` 覆寫，見 `.env.example`）
- Oracle 首次啟動需 2–4 分鐘（healthcheck 過了 backend 才會啟動）
- 上傳檔案存在 `cowork-files` volume（local disk；公司環境改 `ERD_STORAGE_TYPE=s3`）

## 對外分享（TryCloudflare，免帳號、URL 每次隨機）

    docker compose logs tunnel-frontend | grep trycloudflare

開該 URL 即可完整使用（nginx 已把 /api 代理到 backend）。
`tunnel-backend` 的 URL 供單獨測 API 用。

## 切換 LLM provider / OpenRouter

預設使用 OpenAI-compatible provider（`OpenAICompatibleProvider`）。若要對接 OpenRouter（例：`minimax/minimax-m3`），在 `.env` 設以下變數後重建後端容器：

| 環境變數 | 值 |
|---|---|
| `ERD_AGENT_PROVIDER` | `openai-compatible`（預設值，可省略） |
| `ERD_AGENT_OPENAI_COMPATIBLE_BASE_URL` | `https://openrouter.ai/api` |
| `ERD_AGENT_OPENAI_COMPATIBLE_MODEL` | `minimax/minimax-m3`（或其他 OpenRouter model id） |
| `ERD_AGENT_OPENAI_COMPATIBLE_API_KEY` | 從 [openrouter.ai](https://openrouter.ai) 取得的 API key |

若要切換為 Anthropic，在 `.env` 設 `ERD_AGENT_PROVIDER=anthropic` 並填入 `ANTHROPIC_API_KEY`。

`OpenAICompatibleProvider` 為 OpenAI-compatible SSE 實作（POST `{baseUrl}/v1/chat/completions`、Bearer auth），可直接對接任何相容端點。重建指令：

    DOCKER_CONFIG=$(mktemp -d) docker compose up -d --build backend

## 重建特定 docker image

改了某個服務的程式碼後，只重建該服務（其他容器不受影響）：

    DOCKER_CONFIG=$(mktemp -d) docker compose up -d --build <service>
    # 例：backend、frontend

屬於 profile 的服務（如 deepagent-service）必須帶 `--profile`，否則 compose 看不到它：

    DOCKER_CONFIG=$(mktemp -d) docker compose --profile deepagent up -d --build deepagent-service

只想重啟、沒改程式碼時用 `restart`（不會吃到新程式碼——code 是 build 時 COPY 進 image 的）：

    docker compose restart <service>

## 開發工具

### CloudBeaver（Oracle DB 視覺化）

- 本機：http://localhost:8978
- Tunnel URL：每次重啟隨機，執行下方指令查閱：

      docker compose logs tunnel-cloudbeaver | grep trycloudflare

- 登入帳號：`admin`（或 `.env` 的 `CLOUDBEAVER_ADMIN`）；密碼見 `.env` 的 `CLOUDBEAVER_PASSWORD`
- **首次登入後建立 Oracle 連線**（UI 左上 New Connection > Oracle）：

  | 欄位 | 值 |
  |---|---|
  | Host | `oracle` |
  | Port | `1521` |
  | Service name | `FREEPDB1` |
  | Username | `cowork` |
  | Password | `cowork_dev`（compose 的 `APP_USER_PASSWORD`） |

  儲存後點 Test Connection，成功即可。

### Dozzle（container log 檢視）

- 本機：http://localhost:8888
- Tunnel URL：每次重啟隨機，執行下方指令查閱：

      docker compose logs tunnel-dozzle | grep trycloudflare

- 登入帳號：`admin`；密碼見 `.env` 的 `# DOZZLE_PASSWORD` 註解行
- 只顯示名稱含 `erd-cowork` 的容器（`DOZZLE_FILTER`）
- 驗證設定：simple auth，users hash 存於 `dozzle-data/users.yml`（gitignored）

### 啟動開發工具（不重啟既有服務）

    DOCKER_CONFIG=$(mktemp -d) docker compose up -d cloudbeaver dozzle tunnel-cloudbeaver tunnel-dozzle

## 本機開發（不經 docker）

    cd backend && ./mvnw spring-boot:run      # 預設 H2（Oracle 相容模式）+ local file storage
    cd frontend && npm run dev                # http://localhost:3000，/api 已 proxy（後端非 :8080 時設 BACKEND_URL）

### deepagent-service（Python，analysis 模式才需要）

Docker 啟動（見 `deepagent-service/README.md` 完整說明）：

    docker compose --profile deepagent up -d --build deepagent-service

首次安裝（需 [uv](https://docs.astral.sh/uv/)；`pyproject.toml` + `uv.lock` 已進版控）：

    cd deepagent-service && uv sync

Dev 直跑（LLM 端點必填；注意 `OPENAI_BASE_URL` **必須含 `/v1` 後綴**——與 Java 端的
`ERD_AGENT_OPENAI_COMPATIBLE_BASE_URL` 不可互換，該變數不含 `/v1`，詳見 `.env.example`）：

    cd deepagent-service
    OPENAI_BASE_URL=https://openrouter.ai/api/v1 \
    OPENAI_API_KEY=sk-or-... \
    AGENT_MODEL=<OpenRouter 上的 model id> \
    AGENT_WORKSPACE_ROOT=/tmp/deepagent-workspace \
    uv run fastapi dev --port 8000

搭配後端切到 analysis 模式（兩種 provider 二選一；不設即為預設的 openai-compatible）：

    cd backend
    ERD_AGENT_PROVIDER=langgraph-analysis \
    ERD_AGENT_ANALYSIS_BASE_URL=http://localhost:8000 \
    ./mvnw spring-boot:run

測試與 lint（所有 Python 指令一律走 `uv run`）：

    cd deepagent-service && uv run pytest && uv run ruff check .

疑難排解：啟動前先 `lsof -ti:8000` 確認埠淨空——若埠被舊實例佔住，`fastapi dev` 只會在
log 裡留下 `Address already in use`，而 `/health` 仍會由**舊代碼**回 200，測起來像新改動沒生效。

### Langfuse 觀測（本機自架，spec §14）

deepagent-service 的 `/chat` LLM 呼叫、每個工具呼叫（含參數/耗時）可送進本機自架的 Langfuse
看 trace；資料全在本機容器，NEVER 連外部/雲端 Langfuse。

啟動（首次會拉 6 個 image，較久；macOS pull 卡住見上方 `DOCKER_CONFIG` 疑難排解手法）：

    DOCKER_CONFIG=$(mktemp -d) docker compose --profile observability up -d

首次啟動即透過 `LANGFUSE_INIT_*`（見 `docker-compose.yml`）headless 建好 dev org/project/
使用者/API key，不需手動點 UI 建立。

本機跑 deepagent-service（不經 docker）時，另外 export 這三個變數即可接上（值見
`.env.example` 的 Langfuse 區塊，`LANGFUSE_HOST` 用「模式二」的 `localhost:3010`）：

    export LANGFUSE_PUBLIC_KEY=pk-lf-erd-cowork-dev
    export LANGFUSE_SECRET_KEY=sk-lf-erd-cowork-dev
    export LANGFUSE_HOST=http://localhost:3010

看 trace：http://localhost:3010 → 用 `.env.example` 註解裡的 dev 帳密登入（`dev@erd-cowork.local`
/ `erd-cowork-dev-pw123`）→ 左側 Tracing → Traces。

macOS docker build/pull 卡住時：`DOCKER_CONFIG=$(mktemp -d) docker compose ...`
