# Artifact Replay Phase 2a Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** recipe 逐版封存進 artifact＋deepagent `/replay`（零 LLM 確定性重放）＋owner「重新整理」端到端——驗通整條重放管線；分享網域（2b）與 user-token 不在本期。

**Architecture:** finalize 時把「該版的食譜切片」（引用的 qN SQL＋fetch 記錄 last-wins＋expectedColumns）組成 recipe，隨 `DASHBOARD_HTML` 事件上 wire；Java 在既有的 type 攔截點捕捉、存進 Artifact document（inline JSON，與 html 同生命週期）。`/replay` 照 `/repair` 模板：吃 `{recipe, html, viewerToken?, paramsOverride?}`，在臨時目錄重抓（bearer auth 沿用 config）→ expectedColumns 子集檢查 → 臨時鎖門 DuckDB 跑 qN SQL → strip 舊注入 → inject 新 results＋resolver → 隱藏 `data-erd-narrative`（CSS 注入，不做 HTML 手術）→ 回 html 或語意化 error。Java `POST /api/artifacts/{id}/refresh` 串起 owner 路徑（回 transient html，不產新版本）。

**Tech Stack:** deepagent（Python/FastAPI，基於 feat/api-connector 的 connector 機制）＋Java Spring Boot＋前端 React。**分支 `feat/artifact-replay` stacked 在 `feat/api-connector` 上**（PR base＝該分支，#62 merge 後自動落 master）。

**設計權威**：`docs/superpowers/specs/2026-08-23-artifact-replay-share-design.md`（§4 recipe/expectedColumns、§7 endpoint、§3.3 失敗路徑）。

## Global Constraints

- deepagent `app/engine/` 禁 langchain 家族 import；`/replay` 全程零 LLM、零 checkpointer、零 workspace 持久化（臨時目錄用完即棄）
- wire 契約 additive：`DASHBOARD_HTML` 只加欄位；Java 攔截點在 `LangGraphAnalysisProvider.toEventOrEmpty`（**刻意無 Jackson 類別，NEVER 新增**——見該檔註解）
- 錯誤碼語意化：`SOURCE_SCHEMA_CHANGED`／`SOURCE_GONE`／`FETCH_FAILED`；0 列**不是錯誤**（空資料照 render，圖表 empty state 是 skill 的事）
- expectedColumns 比對＝**子集**（recipe 需要的 ⊆ 現有），additive 升版不斷
- recipe 缺席（舊 artifact／純上傳源）→ Java refresh 回 409＋語意訊息，不呼叫 /replay
- Java：constructor injection／DTO record／@Tag/@Operation／@Valid；測試 @WebMvcTest＋@MockitoBean；`./mvnw test` 全綠（NEVER `| tail`）
- 前端：React.FC＋props interface、相對路徑 `/api`、vitest 行為測試
- 每 task 各側測試＋lint 全綠才 commit；commit trailer 照 session 慣例

---

### Task 1: deepagent——fetch 記錄補 columns＋recipe 組裝模組

**Files:**
- Modify: `deepagent-service/app/engine/api_fetch.py`（`record_fetch` 加 `columns: list[str]` 參數，記進每筆 record）
- Modify: `deepagent-service/app/agent/tools/data.py`（fetch 工具呼叫 `record_fetch` 時傳 schema 欄名——工具內已查 `schema_rows`，就地取材）
- Create: `deepagent-service/app/engine/recipe.py`
- Test: `deepagent-service/tests/test_recipe.py`（新）＋`test_api_fetch.py`／`test_data_tools.py` 既有 record 斷言補 columns

**Interfaces:**
- Produces: `build_recipe(workspace: SessionWorkspace, html: str) -> dict | None`——None＝無 API 源（fetches.json 空）；dict＝`{"schemaVersion": 1, "sources": [{connector, params, alias, expectedColumns}], "queries": {qN: {sql, intent}}}`
- 切片規則：`queries`＝`referenced_query_ids(html)` 命中的 qN，SQL/intent 從 workspace `queries/` 落檔讀回（讀法以 `app/engine/results.py` 的 `record_query` 寫入格式為準）；`sources`＝fetches.json **全量 last-wins per alias**（v1 簡化：不做 SQL→表名解析，超集無害——replay 多抓一個未用源只是小成本，正確性安全；docstring 記明此決策）；`expectedColumns`＝該筆 fetch 記錄的 columns
- 供 Task 2（finalize）與 Task 3（/replay 讀 recipe）

- [ ] **Step 1: failing tests**（要點：columns 進 record 的往返；build_recipe 的 None 分支／切片正確性——html 只引 q1 時 queries 只含 q1；last-wins；expectedColumns 帶出）
- [ ] **Step 2: 實作**——`recipe.py` 為 engine 純模組（stdlib＋workspace/results 讀檔）；`record_fetch` 簽名 additive、舊呼叫全改
- [ ] **Step 3: 全套＋ruff → commit** `feat(deepagent): recipe 組裝——fetch 記錄補 columns,切片=引用 qN+last-wins 源+expectedColumns`

### Task 2: deepagent——DASHBOARD_HTML 事件加 recipe 欄位＋finalize 接線

**Files:**
- Modify: `deepagent-service/app/api/events.py`（`DashboardHtmlEvent` 加 `recipe: dict | None = None`、`hasUploadSources: bool = False`）
- Modify: `deepagent-service/app/agent/chat_turn.py`（finalize：dashboard 有更新時 `build_recipe(workspace, themed_html)`；`hasUploadSources = bool(request.sources)`；一併塞進事件）
- Test: `tests/test_chat.py` 追加（有 fetch 的 e2e 斷言事件含 recipe 且 queries 切片正確；無 fetch → recipe=None；上傳源 → hasUploadSources=true）

**Interfaces:** wire additive——Java 未更新前多出的欄位被攔截點忽略（Task 4 才消費），跨版安全。

- [ ] Steps: failing tests → 實作 → 全套＋ruff → commit `feat(deepagent): DASHBOARD_HTML 附 recipe——逐版封存食譜上 wire`

### Task 3: deepagent——`/replay` endpoint（零 LLM 重放）

**Files:**
- Create: `deepagent-service/app/engine/replay.py`
- Modify: `deepagent-service/app/main.py`（照 `/repair` 模板加 `@app.post("/replay")`）
- Modify: `deepagent-service/app/api/schemas.py`（`ReplayRequest`：`recipe: dict`、`html: str`、`viewerToken: str | None = None`（2a 不用，簽名先在）、`paramsOverride: dict | None = None`（同）；`ReplayResponse`：`html: str | None`、`error: {code, message} | None`）
- Test: `deepagent-service/tests/test_replay.py`（新）

**Interfaces:**
- `run_replay(recipe: dict, html: str, registry: ConnectorRegistry) -> ReplayOutcome`（engine 純函式化，endpoint 薄殼）
- 管線（全確定性）：
  1. 逐 source：connector 名 → registry 解析（缺→`SOURCE_GONE`）→ `execute_fetch`（bearer；失敗→`FETCH_FAILED`，訊息不含 URL）→ 落**臨時目錄** snapshot
  2. `open_locked_connection(json sources, api_snapshots_dir=臨時目錄)` → 對每源 `DESCRIBE` → **expectedColumns 子集檢查**（缺欄→`SOURCE_SCHEMA_CHANGED`＋指名缺欄）
  3. 逐 qN 跑 recipe SQL（`normalize_rows` 正規化，record 形狀對齊 `load_all_results` 輸出——`inject_results` 直接可吃）；SQL 錯→`SOURCE_SCHEMA_CHANGED` 兜底
  4. `strip_injected_blocks(html)` → `inject_results` → `inject_bind_resolver` → 注入敘事隱藏樣式：`<style data-erd-replay-hide>[data-erd-narrative]{display:none}</style>`（CSS 注入不做 DOM 手術；附一行 HTML 註解說明供 debug）
  5. 回 html；任何內部例外 → fail-closed error（never-raise 慣例）
- 臨時目錄 `tempfile.TemporaryDirectory`，finally 保證清掉；0 列照 render 不報錯

- [ ] Steps: failing tests（happy path mock fetch／三種 error code／0 列 render／敘事隱藏樣式存在／臨時目錄無殘留）→ 實作 → 全套＋ruff → commit `feat(deepagent): /replay——零 LLM 確定性重放,expectedColumns 子集檢查+敘事隱藏`

### Task 4: Java——recipe 捕捉與持久化

**Files:**
- Modify: `backend/src/main/java/com/erd/cowork/domain/Artifact.java`（加 `private String recipeJson;`、`private Boolean hasUploadSources;`——inline，幾 KB 與 html 同生命週期；Javadoc 一句）
- Modify: `.../agent/provider/analysis/LangGraphAnalysisProvider.java`（`toEventOrEmpty` 的 DASHBOARD_HTML 攔截：多捕 `recipe` 節點（以 raw JSON 字串保存，`JsonNode.toString()`）與 `hasUploadSources`；新 AtomicReference 傳遞，樣式照 `capturedDashboardHtml`）
- Modify: `.../agent/AgentConversationWriter.java`（`persistHtmlResult` 加 `String recipeJson, Boolean hasUploadSources` 參數→set 進 Artifact；呼叫鏈 `AgentOrchestrator` 同步）
- Test: 對應 slice/單元測試（攔截解析：含 recipe 的 payload 捕到、無 recipe 的舊 payload 不炸——**向後相容必測**；writer 落庫欄位）

- [ ] Steps: failing tests → 實作 → `./mvnw test` 全綠 → commit `feat(backend): DASHBOARD_HTML 捕捉 recipe——逐版食譜入 artifact document`

### Task 5: Java——owner refresh 端點

**Files:**
- Create: `.../agent/provider/analysis/AnalysisReplayClient.java`（照 `AnalysisBrowserRepairClient` 模板：POST deepagent `/replay`，plain JSON 非 SSE，timeout 沿用 repair 的設定慣例）
- Modify: `.../web/ArtifactController.java`（`POST /api/artifacts/{id}/refresh`：owner 驗證沿用該 controller 既有 ownership 檢查慣例；無 recipeJson 或 hasUploadSources=true → **409**＋訊息「此版本不支援重新整理（無 API 資料源配方）」；有 → 讀 html（FileStorage）＋recipeJson 呼 replay client → 200 回 `{html}`；deepagent error → 502 帶 `{code, message}` 透傳）
- Test: `@WebMvcTest`＋`@MockitoBean`（404 非 owner／409 無 recipe／200 happy／502 透傳四條）

**Interfaces:** refresh 回 **transient html**——不產新 artifact 版本（重新整理是視圖操作不是創作操作）。

- [ ] Steps: failing tests → 實作 → `./mvnw test` → commit `feat(backend): artifact refresh 端點——owner 用配方重放取最新資料,transient 不產新版`

### Task 6: 前端——重新整理按鈕

**Files:**
- Modify: `frontend/src/api/artifactApi.ts`（`refreshArtifact(id): Promise<{html}>`）
- Modify: artifact 預覽面板元件（按鈕＋loading 態＋成功替換 iframe srcdoc＋失敗 antd message 顯示後端語意訊息；409 隱藏按鈕的判斷可由 artifact detail 的 `hasRecipe` 旗標——若 detail DTO 未帶，Java Task 5 順帶在 artifact DTO 加 boolean（additive））
- Test: vitest 行為測試（按下→呼 API→srcdoc 更新；失敗→message 顯示）

- [ ] Steps: failing tests → 實作 → `npm test` → commit `feat(frontend): dashboard 重新整理——owner 一鍵配方重放`

### Task 7: 驗收——三側全綠＋文件＋終審

- [ ] deepagent `uv run pytest`＋ruff；backend `./mvnw test`；frontend `npm test`——三側全綠（各自顯式 exit code）
- [ ] Phase 2 spec 從 untracked 進本分支 commit（`docs/superpowers/specs/2026-08-23-artifact-replay-share-design.md`）＋本 plan 勾稽
- [ ] opus 全分支終審 → 修整波 → PR（**base＝feat/api-connector**，stacked；描述註明依賴 #62 與終審結論）
