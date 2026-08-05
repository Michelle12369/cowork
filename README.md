# eRD Cowork · Data Studio

Agent chatbot：上傳 CSV/Excel + prompt → 自成一頁的 HTML dashboard，在對話右欄以 iframe 即時渲染（SSE 串流進度），支援全螢幕、版本鏈，以及迭代微調（`baseArtifactId` 指定基底 artifact，縮小 diff 範圍）。

**兩條 provider 線**，由 `ERD_AGENT_PROVIDER` 切換：

| 線 | provider 值 | 產法 | 資料怎麼進 HTML |
|---|---|---|---|
| **llm api**（未設時的預設） | `openai-compatible` | LLM 直寫 HTML | 後端注入**全量原始資料**到 `window.__ERD_DATA__`，統計由瀏覽器 JS 現算 |
| **analysis** | `langgraph-analysis` | 經 `deepagent-service`，模型用 DuckDB 工具查資料 | 只注入**被引用到的查詢結果**到 `window.__ERD_RESULTS__`，瀏覽器只做笨渲染 |

Tailwind 與 ECharts 走 repo 自帶的 `/vendor/` 靜態資產——serve 時由 `ArtifactCdnRewriter` 把 CDN URL 改寫成同源路徑，瀏覽器**不連外部 CDN**（因應內網封鎖）。

架構說明：[docs/architecture.md](docs/architecture.md)

- [Internal 環境同步流程](docs/internal-sync.md) — 單向同步腳本、四類檔案與守門規則
- [Internal 環境實作指引](docs/internal-implementation-guide.md) — 三個接縫要填什麼、怎麼驗收

---

# 一、本機開發（localhost，不經 docker）

**最快的開發迴圈**——backend 走 H2（Oracle 相容模式）+ 本機檔案儲存，不需要 Oracle 容器；前端 vite dev server 有 HMR。日常開發建議用這條。

**環境變數：本機開發吃 `.env.local`**（docker 吃 `.env`，見第二節）。首次設定：`cp .env.example .env.local` 後填值。

## 0. 前置需求（要先裝什麼）

版本以各服務 Dockerfile／建置設定為準——**照著裝就與容器一致**：

| 元件 | 要裝 | 版本 | 說明 |
|---|---|---|---|
| **backend** | JDK | **17** | `pom.xml` 的 `maven.compiler.release=17`、Dockerfile `eclipse-temurin:17` |
| | ~~Maven~~ | **不用裝** | repo 自帶 wrapper `./mvnw`（Maven 3.9.9，首次執行自動下載） |
| **frontend** | Node.js | **22** | 對齊 Dockerfile `node:22-alpine` |
| | npm | 隨 Node 附帶 | — |
| **deepagent-service** | [uv](https://docs.astral.sh/uv/) | 最新 | 唯一需要裝的 Python 工具 |
| | ~~Python / pip~~ | **不用另外裝** | uv 自己管 Python（本專案 `requires-python = ">=3.11"`，容器用 3.11） |
| **docker 版**（第二節） | Docker（含 compose v2） | — | 只有走第二節才需要 |

三件容易踩到的事：

- **JDK 用 18+ 也能建置**（`maven.compiler.release=17` 會把 API 面鎖在 17），但**程式碼 NEVER 使用 18+ API**——internal 環境是 17，用了會在那邊爆。
- **Python 一律走 `uv run`，不要用 `pip install`**——相依由 `pyproject.toml` + `uv.lock` 鎖定，`uv sync` 才會裝到正確版本。
- Node 沒有 `.nvmrc`／`engines` 釘選；**22** 是對齊 Dockerfile 的版本（前端用 Vite 8 + TypeScript 6，太舊的 Node 會起不來）。

驗證裝好了：

    java -version     # 17（或 17+，但程式碼鎖 17 API）
    node -v           # v22.x
    uv --version
    docker compose version

## 1. Backend（Java / Spring Boot）

用 **`local` profile** 跑——它會自動載入 `.env.local`：

    cd backend && ./mvnw spring-boot:run -Dspring-boot.run.profiles=local

- **`local` profile ↔ `.env.local` 的連動**：`application-local.properties` 裡的
  `spring.config.import=optional:file:.env.local[.properties],optional:file:../.env.local[.properties]`
  ——兩個路徑都 optional，所以工作目錄在 repo 根或 `backend/` 都載得到（IntelliJ Run Config
  設 Active profiles: `local` 亦同）。
- **MUST 是 `.env.local` 不是 `.env`**：`.env` 是 docker 專用，值為**容器內視角**
  （`LANGFUSE_HOST=http://lf-web:3000`、`ERD_AGENT_ANALYSIS_BASE_URL=http://deepagent-service:8000`），
  本機直跑吃到那組會連不上。
- 不加 profile 也能跑（`./mvnw spring-boot:run`），但**不會**載入 `.env.local`，只吃
  `application.properties` 的預設值與 shell 既有環境變數。
- 預設 **H2**（Oracle 相容模式）+ local file storage，零外部相依；`local` profile 另開
  h2-console：http://localhost:8080/h2-console（JDBC URL `jdbc:h2:mem:local`、user `sa`、密碼空白）
- health：http://localhost:8080/actuator/health
- 測試：`./mvnw test`

## 2. Frontend（React / Vite）

    cd frontend && npm install     # 首次
    cd frontend && npm run dev

- http://localhost:3000（`vite.config.ts` 寫死 `port: 3000` + `strictPort: true`）
- `/api` 自動 proxy 至 `http://localhost:8080`；backend 不在 :8080 時設 `BACKEND_URL`
- 測試：`npm test`（Vitest）；lint：`npm run lint`

## 3. deepagent-service（Python，**analysis 模式才需要**）

跑 llm api 版（`openai-compatible`，預設）**不需要**這個服務，可整段跳過。

首次安裝（需 [uv](https://docs.astral.sh/uv/)；`pyproject.toml` + `uv.lock` 已進版控）：

    cd deepagent-service && uv sync

**建議：用 `--env-file` 直接吃 `.env.local`**，不必每次手打一長串變數：

    cd deepagent-service
    uv run --env-file ../.env.local fastapi dev --port 8000

也可以逐項指定（`--env-file` 之外的變數會覆蓋檔案值，適合臨時換模型試）：

    cd deepagent-service
    OPENAI_BASE_URL=https://openrouter.ai/api/v1 \
    OPENAI_API_KEY=sk-or-... \
    AGENT_MODEL=<OpenRouter 上的 model id> \
    AGENT_WORKSPACE_ROOT=/tmp/deepagent-workspace \
    uv run fastapi dev --port 8000

> `OPENAI_BASE_URL` **必須含 `/v1` 後綴**——與 Java 端的 `ERD_AGENT_OPENAI_COMPATIBLE_BASE_URL`
> 不可互換，該變數不含 `/v1`，詳見 `.env.example`。

再把 backend 切到 analysis 模式（不設即為預設的 openai-compatible）：

    cd backend
    ERD_AGENT_PROVIDER=langgraph-analysis \
    ERD_AGENT_ANALYSIS_BASE_URL=http://localhost:8000 \
    ./mvnw spring-boot:run -Dspring-boot.run.profiles=local

測試與 lint（所有 Python 指令一律走 `uv run`）：

    cd deepagent-service && uv run pytest && uv run ruff check .

> **疑難排解**：啟動前先 `lsof -ti:8000` 確認埠淨空——若埠被舊實例佔住，`fastapi dev` 只會在
> log 裡留下 `Address already in use`，而 `/health` 仍會由**舊代碼**回 200，測起來像新改動沒生效。

## 本機開發的埠一覽

| 服務 | 埠 | 備註 |
|---|---|---|
| frontend（vite dev） | 3000 | `/api` proxy 至 8080 |
| backend | 8080 | H2，無需 Oracle |
| deepagent-service | 8000 | 僅 analysis 模式 |

---

# 二、Docker（兩個 stack）

需要**完整環境**（真 Oracle、CloudBeaver、Langfuse）或要對外分享時才用。compose 已拆成兩個 stack，靠 external network `erd-cowork-net` 互通——app 可獨立重建而不動到 DB。

| Stack | 檔案 / project | 服務 | profile 開關 |
|---|---|---|---|
| **app** | `docker-compose.app.yml`<br>`erd-cowork-app` | `backend`、`frontend` | 預設即起（＝ **llm api 版**） |
| | | `deepagent-service` | `--profile deepagent`（analysis 版才需要） |
| | | `tunnel-frontend`、`tunnel-backend` | `--profile tunnel` |
| **infra** | `docker-compose.infra.yml`<br>`erd-cowork-infra` | `oracle`、`cloudbeaver`、`dozzle` | 預設即起 |
| | | `lf-web`/`lf-worker`/`lf-postgres`/`lf-clickhouse`/`lf-redis`/`lf-minio` | `--profile observability` |
| | | `tunnel-cloudbeaver`、`tunnel-dozzle`、`tunnel-langfuse` | `--profile tunnel` |

**跨 stack 的兩個後果**：① `backend` 不能再 `depends_on` oracle（compose 的 depends_on 不跨 project），改用 `restart: unless-stopped` 自動重試——Oracle 首啟的 2–4 分鐘內 backend 會 restart 數次，屬預期；② 服務名 DNS（`oracle`、`lf-web`）靠共用 network 解析，兩個 stack 都必須接上 `erd-cowork-net`。

## 啟動

一次性建立共用網路（只需一次）：

    docker network create erd-cowork-net

啟動 infra（先起，Oracle 需 2–4 分鐘）：

    docker compose -f docker-compose.infra.yml up -d

啟動 app（**llm api 版**：backend + frontend，不含 agent）：

    docker compose -f docker-compose.app.yml up -d --build

- 前端：http://localhost:3001 ← **注意與本機開發的 :3000 不同**
- 後端 health：http://localhost:8080/actuator/health（`BACKEND_PORT` 可覆寫）
- 上傳檔案存在 `erd-cowork-app_cowork-files` volume（`ERD_STORAGE_LOCAL_DIR` 可覆寫路徑）

改跑 analysis 版（加上 deepagent-service，並把 `.env` 的 `ERD_AGENT_PROVIDER` 改為 `langgraph-analysis`）：

    docker compose -f docker-compose.app.yml --profile deepagent up -d --build

## 環境變數：`.env`（docker）vs `.env.local`（本機）

**`.env` 是 docker 專用**——compose 會**自動載入**同目錄的 `.env`，所以指令**不需要**帶 `--env-file`。
本機直跑用的是另一份 `.env.local`（見第一節），兩份互不干擾。

| 變數 | `.env.local`（本機直跑） | `.env`（docker） |
|---|---|---|
| `LANGFUSE_HOST` | `http://localhost:3010`（host 對外埠） | `http://lf-web:3000`（docker 內部 DNS） |
| `ERD_AGENT_ANALYSIS_BASE_URL` | `http://localhost:8000` | `http://deepagent-service:8000`（服務名） |
| `ERD_AGENT_OPENAI_COMPATIBLE_BASE_URL` | 同左（本機直跑也可設） | 由 `OPENAI_BASE_URL` 去掉 `/v1` 推導（Java 端格式不含 `/v1`） |
| `CLOUDBEAVER_PASSWORD` | 用不到 | 必填（compose 用 `:?` 強制） |

⚠️ **兩份不可互換**：`.env` 的值是**容器內視角**（服務名 DNS），本機直跑吃到會連不上；反之亦然。
backend 的 `local` profile 因此明確只 import `.env.local`（見第一節）。

## 重建特定 image

compose 已無單一預設檔，**每個指令都要指定 `-f`**。下面沿用這兩個前綴：

    APP="docker compose -f docker-compose.app.yml"
    INFRA="docker compose -f docker-compose.infra.yml"

改了某個服務的程式碼後，只重建該服務（其他容器不受影響）：

    DOCKER_CONFIG=$(mktemp -d) $APP up -d --build <service>     # 例：backend、frontend

屬於 profile 的服務（如 deepagent-service）必須帶 `--profile`，否則 compose 看不到它：

    DOCKER_CONFIG=$(mktemp -d) $APP --profile deepagent up -d --build deepagent-service

只想重啟、沒改程式碼時用 `restart`（**不會**吃到新程式碼——code 是 build 時 COPY 進 image 的）：

    $APP restart <service>      # 或 $INFRA restart <service>

> docker build/pull 卡住不動時，多半是 credential helper 掛住——加 `DOCKER_CONFIG=$(mktemp -d)` 前綴用乾淨設定繞過。

## 對外分享（TryCloudflare，免帳號、URL 每次隨機）

tunnel 為 opt-in，且與其服務放在同一個 stack：

    # 前端/後端 tunnel（app stack）
    $APP --profile tunnel up -d
    $APP logs tunnel-frontend | grep trycloudflare

    # cloudbeaver/dozzle/langfuse tunnel（infra stack）
    $INFRA --profile tunnel up -d

開前端 URL 即可完整使用（nginx 已把 `/api` 代理到 backend）。`tunnel-backend` 的 URL 供單獨測 API 用。

## Compose 環境概覽（容器邊界與對外連線）

> 這節只描述 docker compose 環境；internal 環境（prod）走 K8s，見 [docs/architecture.md](docs/architecture.md)。

**邊界定義**：「本系統」＝ compose 內的自家容器群（backend / deepagent-service / frontend nginx / oracle / cloudbeaver / dozzle / lf-*）。下表列出每一條**跨出**這個邊界的連線。

| # | 發起方 → 目的地 | 協定 | 用途 | 何時發生 | dev / internal 環境差異 |
|---|---|---|---|---|---|
| 1 | 瀏覽器 → frontend nginx | HTTPS/HTTP | **唯一使用者入口**：`/api` reverse proxy（含 SSE）、`/vendor`/`/fonts` 靜態資產、SPA shell | 每次頁面載入與操作 | dev：`localhost:3001` 或本機 cloudflared quick tunnel；internal：內部網域／gateway |
| 2 | **deepagent-service → LLM API** | HTTPS | `astream_events` 驅動的每輪對話（工具呼叫＋文字生成），`OPENAI_BASE_URL` | `ERD_AGENT_PROVIDER=langgraph-analysis` 時，每次使用者送出訊息 | dev＝OpenRouter（`https://openrouter.ai/api/v1`）；internal＝內部 gateway。**這是常態運行時唯一的真正 internet egress** |
| 3 | backend → LLM API | HTTPS | `OpenAICompatibleProvider` 的 `/v1/chat/completions` SSE；internal 環境另含 token-exchange j1→j2 交換端點 | 僅 `ERD_AGENT_PROVIDER=openai-compatible` 時啟用 | dev＝OpenRouter；internal＝內部 gateway＋token-exchange（j1→j2，TTL 快取，401 自動重試） |
| 4 | deepagent-service → Langfuse | HTTP | 每輪 trace 上報（`langfuse.langchain.CallbackHandler`），未設 `LANGFUSE_PUBLIC_KEY` 即完全 no-op | 每次 `/chat` 呼叫（`observability` profile 啟用且金鑰已設時） | dev＝本機 `lf-web`（`--profile observability`，`:3010`）；internal **MUST** 指向內部位址，NEVER 雲端 Langfuse SaaS |
| 5 |（選配）cloudflared tunnels → Cloudflare | HTTPS | `tunnel-*` 對外曝露本機服務供臨時測試 | `--profile tunnel` 啟用時 | quick tunnel URL 每次重啟即換 |
| 6 | dashboard HTML 內的 CDN 參照（瀏覽器發起） | — | 模型輸出的 HTML 字面上寫標準 CDN URL（`cdn.tailwindcss.com`、`cdn.jsdelivr.net/npm/echarts@5`） | 生成當下寫入 rawHtml；**serve 時**由 `ArtifactCdnRewriter` 依 asset profile 正則改寫為 `/vendor/...` 本地資產 | 瀏覽器實際載入的是同源 `/vendor/` 檔案，**不連外部 CDN**（因應內網封鎖 `cdn.tailwindcss.com`）。deepagent 線的 `html_guard.ALLOWED_SCRIPT_SRC_PREFIXES` 白名單逐字複製自同一份 system prompt 的 CDN 寫法規範，兩者只是「生成期允許寫什麼」與「serve 期改寫成什麼」的一體兩面，不衝突 |
| 7 | Oracle / CloudBeaver / dozzle | — | 純內部元件：DB、DB 管理 UI、log 檢視 | — | **無對外連線**（各自只在 docker 內部網路被存取；有選配 tunnel，見第 5 列） |

**結論**：常態運行時真正的 internet egress **只有 deepagent-service → LLM API**（第 2 列）；backend → LLM API（第 3 列）只在 `openai-compatible` provider 時啟用；其餘皆為容器間內網流量或選配的臨時 tunnel。上傳檔、artifact、workspace 皆落地於本地 volume / RWX PVC，不再有對外儲存連線（見 [docs/architecture.md](docs/architecture.md) 的「儲存後端決策」節）。

---

# 三、切換 LLM provider / OpenRouter

預設使用 OpenAI-compatible provider（`OpenAICompatibleProvider`，即 **llm api 線**）。若要對接 OpenRouter，設以下變數（本機直跑放 `.env`；docker 放 `.env`）：

| 環境變數 | 值 |
|---|---|
| `ERD_AGENT_PROVIDER` | `openai-compatible`（未設時的預設值） |
| `ERD_AGENT_OPENAI_COMPATIBLE_BASE_URL` | `https://openrouter.ai/api`（**不含** `/v1`） |
| `ERD_AGENT_OPENAI_COMPATIBLE_MODEL` | OpenRouter 上的 model id |
| `ERD_AGENT_OPENAI_COMPATIBLE_API_KEY` | 從 [openrouter.ai](https://openrouter.ai) 取得的 API key |

`OpenAICompatibleProvider` 為 OpenAI-compatible SSE 實作（POST `{baseUrl}/v1/chat/completions`、Bearer auth），可直接對接任何相容端點。改完後：本機直跑重啟 `./mvnw spring-boot:run`；docker 用上面的重建指令。

---

# 四、開發工具（docker only）

## CloudBeaver（Oracle DB 視覺化）

- 本機：http://localhost:8978
- Tunnel URL（需 `--profile tunnel`）：`$INFRA logs tunnel-cloudbeaver | grep trycloudflare`
- 登入帳號：`admin`（或 `.env` 的 `CLOUDBEAVER_ADMIN`）；密碼見 `.env` 的 `CLOUDBEAVER_PASSWORD`（compose 用 `:?` 強制必填）
- **首次登入後建立 Oracle 連線**（UI 左上 New Connection > Oracle）：

  | 欄位 | 值 |
  |---|---|
  | Host | `oracle` |
  | Port | `1521` |
  | Service name | `FREEPDB1` |
  | Username | `cowork` |
  | Password | `cowork_dev` |

  儲存後點 Test Connection，成功即可。

## Dozzle（container log 檢視）

- 本機：http://localhost:8888
- Tunnel URL（需 `--profile tunnel`）：`$INFRA logs tunnel-dozzle | grep trycloudflare`
- 登入帳號：`admin`；密碼見 `.env` 的 `# DOZZLE_PASSWORD` 註解行
- 只顯示名稱含 `erd-cowork` 的容器（`DOZZLE_FILTER`），兩個 stack 都涵蓋
- 驗證設定：simple auth，users hash 存於 `dozzle-data/users.yml`（gitignored）

## 只起開發工具（不動既有服務）

    DOCKER_CONFIG=$(mktemp -d) $INFRA up -d cloudbeaver dozzle
    DOCKER_CONFIG=$(mktemp -d) $INFRA --profile tunnel up -d tunnel-cloudbeaver tunnel-dozzle

---

# 五、Langfuse 觀測（自架，spec §14）

deepagent-service 的 `/chat` LLM 呼叫、每個工具呼叫（含參數/耗時）可送進自架的 Langfuse 看 trace；資料全在本機容器，NEVER 連外部/雲端 Langfuse。

> ⚠️ **只有 deepagent-service 會送 trace**（Java backend 不送）。跑 llm api 版（`openai-compatible`，不啟 deepagent-service）時 Langfuse 會是空的，屬預期。

Langfuse 服務本身一律跑在 docker（infra stack 的 `observability` profile；首次會拉 6 個 image，較久）：

    DOCKER_CONFIG=$(mktemp -d) $INFRA --profile observability up -d

首次啟動即透過 `LANGFUSE_INIT_*`（見 `docker-compose.infra.yml`）headless 建好 dev org/project/使用者/API key，不需手動點 UI 建立。

**本機直跑 deepagent-service** 要接上它時，export 這三個變數（`LANGFUSE_HOST` 用 host 對外埠）：

    export LANGFUSE_PUBLIC_KEY=pk-lf-erd-cowork-dev
    export LANGFUSE_SECRET_KEY=sk-lf-erd-cowork-dev
    export LANGFUSE_HOST=http://localhost:3010

**docker 內的 deepagent-service** 則走內部 DNS（`.env` 已設 `LANGFUSE_HOST=http://lf-web:3000`）。

看 trace：http://localhost:3010 → 用 dev 帳密登入（`dev@erd-cowork.local` / `erd-cowork-dev-pw123`）→ 左側 Tracing → Traces。
