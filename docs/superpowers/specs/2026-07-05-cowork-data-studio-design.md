# Cowork · Data Studio — 設計文件

日期：2026-07-05（rev.2）
Mockup 依據：`docs/eRDWorkspaceonline.html` 的 Cowork tab（僅此畫面，其餘 tab 不做）

## 1. 目標與範圍

打造一個 agent chatbot：使用者上傳 CSV/Excel + 輸入 prompt，agent 產出 HTML dashboard，UI 完整還原 mockup 的 Cowork tab。

**第一版範圍（in scope）**
- 三欄式版面：Chat 歷史側欄（可收合）、對話面板、Artifact 面板
- 檔案上傳：拖放 + 瀏覽、附件 popover 管理（限制見 §7）
- 對話：user/AI 氣泡、打字串流效果、可收合的步驟軌跡（ThoughtChain）、artifact 參照卡
- 快速指令 chips（SPC analysis / Defect pareto / Trend report）：前端獨立設定檔 `config/quickPrompts.ts`，點擊即送出對應 preset prompt（mockup 中的 Generate slides chip 隨 slides 功能延後）
- Dashboard artifact：iframe 內渲染、Regenerate、開新分頁全螢幕檢視
- Provider 抽象層；**OpenAICompatibleProvider（預設）與 AnthropicProvider 完整實作**，InternalCodegenProvider 僅骨架 + stub
- Docker Compose 一鍵啟動（前端 + 後端 + Oracle + TryCloudflare tunnel）
- Git pre-commit hooks：前端 format + lint、後端 google-java-format

**不做（out of scope / 延後）**
- Slides 分頁與 .pptx 產出（UI 保留分頁佔位）
- mockup 的其他 tab；「Select data source」實際外部資料庫接入（UI 保留選單）
- InternalCodegenProvider 的實際 HTTP 呼叫細節（介面、mapper、偽串流骨架先做好）
- 登入/認證 UI（使用者識別見 §8「使用者識別」：v1 用 X-User-Id header + 匿名 UUID，公司環境由 SSO/gateway 注入同一 header）、多人共編

## 2. 技術選型

| 層 | 選型 |
|---|---|
| 前端 | React 18 + TypeScript + Vite、antd + @ant-design/x（Bubble/Sender/ThoughtChain/Attachments）、Tailwind CSS、ECharts |
| 後端 | Spring Boot 3.x（Java 17+）、Spring Web + WebFlux（SSE 用 `Flux`）、Spring Data JPA |
| 資料庫 | **Oracle**（公司環境）；本機開發用 `gvenzl/oracle-free` 容器；單元測試用 H2 Oracle 相容模式 |
| 檔案解析 | Apache Commons CSV（串流解析）、Apache POI + excel-streaming-reader（xlsx SAX 串流讀取） |
| LLM | Anthropic Java SDK（預設 `claude-haiku-4-5`，與公司 gpt-oss 同級距，config 可換 `claude-sonnet-4-6`）；公司 LLM 走 **OpenAI-compatible API**（`/v1/chat/completions`，`stream:true` SSE） |
| 格式化 | 前端 Prettier + ESLint（lint-staged）；後端 Spotless + google-java-format；git pre-commit hook 統一觸發 |

## 3. 系統架構

```
React (Vite)                      Spring Boot 3
┌───────────────────────┐        ┌──────────────────────────────────────┐
│ CoworkPage（app 根頁）  │  REST  │ SessionController                    │
│ ├ ChatHistorySidebar   │◄──────►│ FileController（上傳/解析/刪除）       │
│ ├ ChatPanel            │        │ MessageController（POST → SSE）       │
│ │  ├ MessageList       │  SSE   │ ArtifactController（GET html）        │
│ │  ├ ThoughtChain      │◄───────│                                      │
│ │  └ Sender + chips    │        │ AgentOrchestrator                    │
│ └ ArtifactPanel        │        │  └ DashboardAgentProvider (介面)      │
│    ├ iframe (sandbox)  │        │     ├ AnthropicProvider（完整）       │
│    └ open-in-new-tab ──┼──GET──►│     ├ OpenAICompatibleProvider（完整） │
│ /artifacts/:id 全螢幕頁 │        │     └ InternalCodegenProvider（stub） │
└───────────────────────┘        │ FileParsingService（串流解析+profile）  │
                                 │ ArtifactAssembler（資料注入+抽樣）      │
                                 │ JPA(Oracle): Session/Message/File/    │
                                 │              Artifact                 │
                                 └──────────────────────────────────────┘
```

## 4. Artifact 契約（核心設計）

所有 provider 產出的 dashboard 一律是 **self-contained HTML**（Tailwind CDN + ECharts CDN），遵守同一個資料注入契約：

- HTML 內的 JS 從 `window.__ERD_DATA__[alias]` 讀取資料（alias = 上傳檔案的識別 key，對應 codegen API `fileMeta.alias`）：
  ```js
  window.__ERD_DATA__ = {
    "file1": { "columns": ["lot", "vt", ...], "rows": [[95, 0.419], ...] }
  };
  ```
- 後端 `ArtifactAssembler` 在存檔前把真實資料以 `<script>` 前置注入 HTML。這就是「把使用者上傳的資料與 codegen 產出的 HTML append 在一起」的實作機制。
- **注入抽樣上限**：預設每檔最多 200,000 列或 30MB JSON（config 可調）；超過時後端做均勻抽樣注入，並在注入的 metadata 標記 `sampled: true, totalRows: M`，讓 dashboard 可顯示「抽樣 N/M 列」。
- 統計計算（μ、σ、Cpk、管制界限等）由 artifact 內的 JS 在瀏覽器端計算——三種 provider 行為一致。
- 前端以 `<iframe sandbox="allow-scripts" src="/api/artifacts/{id}">` 渲染；「開新分頁」直接開同一 URL（回傳 `text/html`）。**存取控制**：iframe/新分頁無法帶 `X-User-Id` header，v1 採 capability URL（UUID 不可猜、無 user 檢查）；正式環境 hardening 待辦（M4 終審登錄）：(a) signed URL 或 cookie 身分；(b) 「開新分頁」在 app 同源載入 LLM HTML，可讀 localStorage/呼叫 API——需獨立 origin 或 CSP header；(c) `RetentionCleanupService` 未清理 artifact/message rows（每次 Regenerate 累積 ~30MB CLOB）——需 cascade 清理。

## 5. LLM 資料供給策略（讓產出效果好的關鍵）

LLM 不吃完整資料，但要給它足夠的「資料形狀」資訊，它寫出的 JS 才會正確：

**Assistant 行為規範（system prompt，兩個 LLM provider 共用）**：
1. 預設**繁體中文**回答（使用者用其他語言則跟隨）；技術名詞保留英文
2. 招呼/寒暄：簡短回應並引導上傳資料提問
3. 與資料分析/dashboard 無關的問題一律婉拒（說明服務範圍），不作答
4. 有檔案但需求模糊：先釐清——依欄位 schema 主動提議合適圖表（管制圖/直方圖/Pareto/趨勢/散佈等）詢問使用者想要哪些，不急著產 HTML
5. 無檔案卻要求分析：請使用者先上傳 CSV/Excel
6. 僅在明確要產出 dashboard 時輸出 ```html fenced block；聊天/釐清回應不得含 html block

**AnthropicProvider 與 OpenAICompatibleProvider（prompt 內容相同）** — system prompt 說明 artifact 契約（讀 `__ERD_DATA__`、輸出 ```html fenced block）與上述行為規範，user prompt 附上每個檔案的：
1. 欄位 schema：欄名 + 推斷型別（string/number/date）
2. rowCount、colCount
3. 數值欄統計摘要：min / max / mean / std / null 數
4. 類別欄的 top-N distinct 值
5. **前 20 列樣本資料**（markdown table）——讓模型看到真實值的格式（小數位、單位、命名慣例）

**InternalCodegenProvider** — 依公司 API 格式：`fileMeta`（schema/rowCount/columns）+ **`fileData` 欄位帶少量樣本資料（JSON 格式）**，樣本列數與序列化格式先以 config 佔位（`erd.agent.codegen.sample-rows`，預設 20），待公司 API 細節確認後調整。

## 6. Provider 抽象層

```java
public interface DashboardAgentProvider {
    boolean supportsStreaming();
    Flux<AgentEvent> generate(AgentRequest request);
}
```

`AgentEvent`：

| type | 內容 | 用途 |
|---|---|---|
| `STEP` | stepKey, title, description, status(pending/running/success/error) | ThoughtChain 即時進度 |
| `TOKEN` | delta 文字 | 打字效果 |
| `ANSWER` | 完整回覆文字 | 訊息定稿 |
| `ARTIFACT` | artifactId, title | 右欄載入 dashboard |
| `ERROR` | code, message | 錯誤氣泡 |

**AnthropicProvider（完整實作）**：Anthropic Java SDK streaming；文字 delta 轉 `TOKEN`，HTML 以 ```html fenced block 抽出（串流中偵測起訖標記，fenced 內容不發 TOKEN）轉為 `ARTIFACT`。

**OpenAICompatibleProvider（完整實作，預設）**：OpenAI-compatible `/v1/chat/completions`，`stream:true`，WebClient 解析 SSE（`data: {"choices":[{"delta":{"content":"..."}}]}`），逐 token 轉 `TOKEN`；HTML 抽取邏輯與 AnthropicProvider 共用（抽到 `HtmlExtractingTransformer` 共用元件）。base-url / api-key / model 走 config。

**InternalCodegenProvider（骨架 + stub）**：`extends AbstractCodegenProvider`。
- Request mapping（`CodegenRequestMapper`）：
  ```json
  {"params": {"inputData": {
      "sessionId": "...",
      "fileMeta": [{"name","alias","type":"table",
                    "metadata":{"rowCount","colCount","columns":[{"colType","colName"}]}}],
      "fileData": { "<alias>": [ {"lot": 95, "vt": 0.419}, ... ] },
      "conversation": {"question": "<md string>", "history": [{"sender","text"}]}
  }}}
  ```
  （`fileData` 為少量樣本資料，JSON 格式；實際欄位結構待公司 API 確認後在 mapper 內調整）
- Response：`answer`（一次性完整回覆，含 HTML）與 `error` 欄位。
- **偽串流**：API 無 SSE，後端拿到完整 `answer` 後切塊重播——`Flux.fromIterable(chunks).delayElements(30ms)` 逐塊發 `TOKEN`，前端打字效果與其他 provider 一致。
- `STEP` 事件由骨架在「準備請求 → 呼叫 API → 組裝 artifact」階段發出。
- **長回應時間（30s–1min）**：瀏覽器到後端的 SSE 連線在送出 prompt 當下就建立並保持開啟；等待公司 API 回覆期間，骨架顯示「呼叫生成服務」的 running STEP 並**每 15 秒發 SSE heartbeat**（註解行 `:ka`）防止代理逾時，UI 維持 mockup 的「Working on it…」狀態。WebClient 對 codegen API 的 timeout 設 120s。使用者感受：先看步驟進度轉圈約 30–60 秒，回覆到手後開始打字 + 出 dashboard。

Provider 由 `application.yml` 的 `erd.agent.provider=openai-compatible|anthropic|internal-codegen` + `@ConditionalOnProperty` 選擇（預設 `openai-compatible`）。

## 7. 檔案限制與大檔策略

| 項目 | 限制（config 可調） | 理由 |
|---|---|---|
| 每 session 檔數 | 5 | 同 mockup |
| session 總量 | 5GB | 同 mockup |
| CSV 單檔 | 2GB | 串流解析（Commons CSV），不進記憶體 |
| xlsx 單檔 | **200MB** | POI 解析 xlsx 記憶體膨脹數倍，2GB 會 OOM；用 excel-streaming-reader SAX 讀取仍建議設上限 |
| 注入 dashboard | 200,000 列 / 30MB JSON per file | 瀏覽器無法承受 GB 級 JSON；超過做均勻抽樣（見 §4） |

上傳採 multipart 串流直接落地磁碟（不經記憶體）；解析同樣走串流。統計 profile 在解析時單趟計算。

## 8. 後端 API 與資料模型

**使用者識別（multi-user）**

每個使用者有自己的 sessions/chats。後端一律以 `X-User-Id` header 識別使用者：controller 解析後傳入 service，**所有 session 查詢以 userId 過濾**；存取不屬於自己的 session 一律回 404（不洩漏資源存在性）。v1 前端在 localStorage 產生匿名 UUID（key `erd_user_id`），由 axios interceptor 自動附加到每個請求；公司環境部署時改由 SSO/reverse proxy 注入同名 header，前後端程式皆不需修改。

**REST API**

| Method | Path | 說明 |
|---|---|---|
| POST | `/api/sessions` | 建立會話 |
| GET | `/api/sessions` | 目前使用者的會話列表（標題 + 更新時間） |
| GET | `/api/sessions/{id}` | 會話明細（messages + files + artifacts） |
| POST | `/api/sessions/{id}/files` | multipart 上傳；驗限制、串流落地、解析 metadata |
| DELETE | `/api/sessions/{id}/files/{fileId}` | 移除附件 |
| POST | `/api/sessions/{id}/messages` | 送出 prompt，回應 SSE（`text/event-stream`）串 AgentEvent |
| GET | `/api/artifacts/{id}` | 回傳完整 HTML（`text/html`），iframe 與新分頁共用 |

**Session 標題規則**：建立時為「New analysis」；session 的**第一則 user 訊息**送出時，以該問題文字截斷（約 30 字）作為標題並更新 `updatedAt`。

**JPA Entities（Oracle）**

- `ChatSession(id, userId, title, createdAt, updatedAt)`
- `ChatMessage(id, session, sender[USER/AI], text CLOB, stepsJson CLOB, artifactId, createdAt)`
- `UploadedFile(id, session, name, alias, storagePath, sizeBytes, type, metadataJson CLOB)`
- `Artifact(id, session, title, html CLOB, createdAt)`

檔案本體經 `FileStorage` 介面（`store / read / delete`）存取，`erd.storage.type=local|s3` 切換。**v1 用 `LocalDiskStorage`**（docker compose volume 掛載，local 開發/demo 環境）；公司環境部署時切換 **`S3FileStorage`**（公司有 S3）：AWS SDK v2、endpoint/credentials/bucket 走 config、path-style access、2GB 大檔 multipart 串流直傳。DB 只存 metadata 與 storage key。ID 用 Hibernate `@UuidGenerator`；`createdAt/updatedAt` 用 JPA Auditing（`@CreatedDate`/`@LastModifiedDate`）。Health 檢查用 Spring Boot Actuator（`/actuator/health`）。

**清理策略**（防磁碟爆量，session 上限 5GB 累積很快）：
- 刪 session / 刪附件時同步刪除實體檔案
- `@Scheduled` 排程清理超過 N 天未活動 session 的檔案（`erd.storage.retention-days`，預設 30；保留 DB metadata，附件列表標記「已過期清除」）

## 9. 前端結構

此專案前端**只有 Cowork 一個畫面**，不做多 feature 分層，結構保持扁平：

```
frontend/src/
├── App.tsx                     # Router：/ → CoworkPage、/artifacts/:id → ArtifactFullPage
├── CoworkPage.tsx              # 三欄 layout
├── config/quickPrompts.ts      # 快速指令 chips 集中設定（label + preset prompt），改文案/增減只動此檔
├── components/
│   ├── ChatHistorySidebar.tsx
│   ├── ChatPanel.tsx / MessageBubble.tsx / StepChain.tsx
│   ├── UploadModal.tsx / AttachmentsPopover.tsx / FileChips.tsx
│   ├── QuickChips.tsx / PromptSender.tsx（含 + 選單）
│   └── ArtifactPanel.tsx（tabs + iframe + 工具列）
├── pages/ArtifactFullPage.tsx
├── hooks/useAgentStream.ts     # fetch + ReadableStream 解析 SSE、事件分派
├── api.ts / types.ts
└── index.css                   # Tailwind
```

- SSE 用 `fetch` + ReadableStream（POST body 需求，EventSource 不支援 POST）。
- API base 一律用相對路徑 `/api`（開發時 Vite proxy，部署時 nginx proxy），tunnel 場景不需設定跨域網址。

## 10. Docker Compose 與 TryCloudflare Tunnel

`docker-compose.yml`（**定位為 local 開發/demo 用**）一鍵啟動：

| service | 內容 |
|---|---|
| `oracle` | `gvenzl/oracle-free:23-slim`，healthcheck，volume 持久化 |
| `backend` | Spring Boot（multi-stage Dockerfile），depends_on oracle healthy，掛檔案儲存 volume（LocalDiskStorage） |
| `frontend` | Vite build 產物 + nginx；**nginx 將 `/api` 反向代理到 backend**（SSE 需 `proxy_buffering off` + `proxy_read_timeout 300s`，涵蓋 codegen 30s–1min 等待） |
| `tunnel-frontend` | `cloudflare/cloudflared` quick tunnel：`tunnel --no-autoupdate --url http://frontend:80`，log 印出隨機 `*.trycloudflare.com` URL |
| `tunnel-backend` | 同上指向 `http://backend:8080`（直接測 API 用） |

說明：因 nginx 已代理 `/api`，**只開 frontend tunnel 就能完整使用**（前端經 tunnel 打相對路徑 `/api` 會回到同一個 origin）；backend tunnel 是為了單獨測試 API 保留，兩者皆為 TryCloudflare 免帳號 quick tunnel（每次啟動 URL 隨機）。

## 11. 開發工具：Claude Code format hooks（已設定）

`.claude/settings.json` 的 PostToolUse hook（Claude 每次 Write/Edit 後自動執行）：
- **前端**（`frontend/**/*.{ts,tsx,css}`）：`npx --no-install prettier --write` + `eslint --fix`（frontend 專案建立並安裝套件後自動生效）
- **後端**（`backend/**/*.java`）：`google-java-format --replace`（已透過 brew 安裝，已驗證會觸發）

Spotless（google-java-format）仍會加進 Maven build 供 CI `spotless:check` 驗證；prettier/eslint 設定隨前端 scaffold 建立。

## 12. 錯誤處理

- 上傳超限（檔數/大小/型別）：後端 400 + 前端 upload modal 警告條（同 mockup 樣式）。
- 檔案解析失敗：400 + 可讀訊息，檔案不入列。
- Provider 失敗 / codegen 回 `error`：發 `ERROR` 事件，UI 錯誤氣泡，步驟軌跡標記失敗。
- SSE 中斷：前端顯示重試提示；每輪生成 stateless 可重送。
- Anthropic `stop_reason=="refusal"`、內部 LLM 非 200、rate limit：統一映射為 `ERROR` 事件，訊息友善化。

## 13. 測試策略

- **後端**：FileParsingService（csv/xlsx/型別推斷/大檔抽樣/壞檔）；CodegenRequestMapper（domain → 公司 API 格式，含 fileData 樣本）；ArtifactAssembler（注入、alias 對應、抽樣標記）；HtmlExtractingTransformer（串流中 fenced block 抽取）；provider 以假 LLM client 測 event 順序；偽串流切塊測試；controller 用 WebTestClient。DB 測試用 H2 Oracle 模式。
- **前端**：useAgentStream 事件分派（mock SSE）；UploadModal 限制邏輯、ThoughtChain 狀態（Vitest + Testing Library）。
- **端對端冒煙**：docker compose 起服務後一條 happy path（上傳範例 csv → prompt → dashboard 出現）。

## 14. 里程碑

1. **M1 骨架**：前後端專案初始化、docker compose（oracle/backend/frontend/tunnel）、pre-commit hooks、三欄 UI 靜態版、session/message API + Oracle schema
2. **M2 檔案流**：串流上傳/解析/profile/附件管理（含限制與警告）
3. **M3 Agent 流**：provider 抽象層 + AnthropicProvider + InternalLlmProvider + SSE + ThoughtChain/打字效果
4. **M4 Artifact**：契約注入（含抽樣）、iframe 渲染、全螢幕頁、Regenerate、session 標題規則
5. **M5 Codegen 骨架**：AbstractCodegenProvider + mapper（fileMeta/fileData）+ 偽串流（stub 實作）
