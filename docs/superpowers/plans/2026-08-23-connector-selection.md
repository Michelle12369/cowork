# Per-session 資料源選擇（§11）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`).

**Goal:** 使用者在上傳區跳 modal 多選「資料源」（connector group），per-session 圈定範圍——模型只在選定 group 內、依對話意圖選該打哪隻 API。三側：deepagent（config group 層＋registry 過濾＋只注入選定組）、Java（`GET /api/connectors`＋`selectedGroups` 傳遞）、前端（多選 modal）。

**Architecture:** config 引入 `connector_groups`（每 group 含 members＝現況 ConnectorDefinition＋group 歸屬），向後相容舊扁平 `connectors:`（當單一隱式 group）。`selectedGroups` 隨 `/chat` request 傳到 deepagent（與 files/userId 同載體），registry 按選定 group 過濾後才餵 tools/prompt——**只注入選定組是本擴充核心（比現況全域注入還省 prompt）**。deepagent 暴露 `GET /connectors` 回 group 清單，Java `GET /api/connectors` 代理（config 權威在 deepagent，不兩處解析）。

**Tech Stack:** deepagent（Python/FastAPI）＋Java Spring Boot＋前端 React。分支 `feat/connector-selection`（worktree `worktrees/connector-selection`，base＝feat/api-connector 含§12；**所有工作在此 worktree**）。

**設計權威**：`docs/superpowers/specs/2026-08-20-api-connector-design.md` §11。

## Global Constraints

- **v1 簡化（偏離 spec §11.3，明確標注）**：connector name **全域唯一**（config 載入時驗證跨組不重名，重名啟動即炸）；**不做自動前綴命名空間**（`mes.line_list`）——理由：自動前綴牽涉 DuckDB 表名合法性（點號非法識別字）、fetch alias 生成等中等複雜度，全域唯一用確定性載入驗證即保證不衝突。config 作者用 `mes_line_list` 手動區分。前綴自動化列 §11 follow-up
- **功能關閉不變式不得破壞**：`AGENT_CONNECTORS_FILE` 未設＝byte-identical（e2e 釘）；**selectedGroups 空＝全部 group 可用**（向後相容——舊前端不傳 selectedGroups 時行為同現況全域）
- deepagent `app/engine/` 禁 langchain 家族；wire/DTO additive
- Java：constructor injection／DTO record／@Tag/@Operation/@Valid／@WebMvcTest+@MockitoBean；`./mvnw test` 全綠（NEVER `| tail`）
- 前端：React.FC＋props interface、相對 `/api`、antd `App.useApp()` message、vitest 行為測試
- 命名 ≥3 字元；註解 1–2 行；測試 snake_case；formatter hook（用法先、import 後）；每 task 各側測試＋lint 全綠才 commit；trailer 照 session 慣例

## Task 1: deepagent config group 層＋registry 過濾＋全域唯一驗證

**Files:**
- Modify: `deepagent-service/app/engine/connectors.py`
- Test: `deepagent-service/tests/test_connectors.py`

**Interfaces:**
- `ConnectorGroup`（Pydantic：`name`, `display`, `description`, `members: list[ConnectorDefinition]`）；`ConnectorDefinition` 加 `group: str`（載入時由所屬 group 填）
- `load_connector_registry`：支援兩種 config——頂層 `connector_groups`（分組）或 `connectors`（舊扁平→單一隱式 group `default`/display「資料源」）；**跨組 connector name 重複→`ConnectorConfigError`**
- `ConnectorRegistry` 加：`groups() -> list[ConnectorGroup]`（name/display/description 供清單）；`filter_by_groups(selected: list[str]) -> ConnectorRegistry`（空 selected＝回自身全部；非空＝只留選定 group 的 connector；未知 group 名忽略＋不炸）

- [ ] Steps: failing tests（分組 config 解析；舊扁平向後相容；跨組重名炸；filter 空＝全部、非空＝子集、未知 group 忽略）→ 實作 → 全套＋ruff → commit `feat(deepagent): connector group 層＋registry 過濾——config 分組/向後相容/全域唯一驗證`

## Task 2: deepagent selectedGroups 傳遞＋只注入選定組＋`GET /connectors`

**Files:**
- Modify: `deepagent-service/app/api/schemas.py`（`ChatRequest` 加 `selectedGroups: list[str] = []`）
- Modify: `deepagent-service/app/agent/graph.py`（`build_agent` 收 `selected_groups`，registry 先 `filter_by_groups` 再餵 tools/prompt）
- Modify: `deepagent-service/app/agent/chat_turn.py`（傳 `request.selectedGroups` 進 build_agent）
- Modify: `deepagent-service/app/main.py`（加 `GET /connectors` 回 `[{name, display, description}]`）
- Test: `tests/test_graph.py`／`test_chat.py`／新 `test_connectors_endpoint`

**Interfaces:** Consumes Task 1。selectedGroups 空＝全部（不變式）；非空＝filter 後只注入子集。

- [ ] Steps: failing tests（selectedGroups=[mes]→prompt/tools 只含 mes 的 connector；空→全部；`GET /connectors` 回 group 清單；功能關閉時 `GET /connectors` 回空 list 不炸）→ 實作 → 全套＋ruff → commit `feat(deepagent): selectedGroups 過濾注入＋GET /connectors——只注入選定組`

## Task 3: Java selectedGroups 傳遞＋`GET /api/connectors` 代理

**Files:**
- Modify: `backend/.../web/dto/SendMessageRequest.java`（加 `List<String> selectedGroups`，@Schema，nullable→預設空 list）
- Modify: `backend/.../agent/model/AgentRequest.java`（加 selectedGroups）＋傳遞鏈（MessageController→orchestrator→LangGraphAnalysisProvider.buildRequestBody 塞進 `/chat` body，與 files 同路）
- Create: `backend/.../web/ConnectorController.java`（`GET /api/connectors` → WebClient 代理 deepagent `GET /connectors`，回 group DTO list；deepagent 不可達→502 或空 list，判定）
- Modify: `AnalysisAgentProperties`（已有 baseUrl）
- Test: `@WebMvcTest`（controller 代理）＋傳遞鏈單元測試（selectedGroups 進 /chat body）

**Interfaces:** wire additive——deepagent 未更新前 selectedGroups 欄位被忽略（跨版安全）；空 selectedGroups＝全部（不變式一致）。

- [ ] Steps: failing tests → 實作 → `./mvnw test` 全綠 → commit `feat(backend): selectedGroups 傳遞＋GET /api/connectors 代理——資料源選擇後端鏈路`

## Task 4: 前端多選 modal＋選定狀態＋送 selectedGroups

**Files:**
- Modify: `frontend/src/api/`（`fetchConnectors(): Promise<ConnectorGroup[]>`；send message 帶 selectedGroups）
- Modify: 上傳區元件（加「選擇資料源」按鈕＋多選 modal；選定狀態顯示；modal 多選但預設引導單選＋「跨系統分析可能需明確指定關聯欄位」提示——§11.5 UX 折衷）
- Modify: chat 送出鏈路（selectedGroups 進 SendMessageRequest）
- Test: vitest 行為測試（開 modal→拉清單→多選→送出帶 selectedGroups；空選＝不帶/空陣列）

**Interfaces:** selectedGroups 空＝後端當全部（向後相容，舊行為）。

- [ ] Steps: failing tests → 實作 → `npm test` 全綠 → commit `feat(frontend): 資料源多選 modal——選定 group 隨訊息送出`

## Task 5: e2e＋驗收

- [ ] deepagent e2e：selectedGroups=[mes] 全鏈→只有 mes 的 fetch 可用（打 erp connector 應「不存在」退貨）；功能關閉不變式（無 config→無 fetch 工具、`GET /connectors` 空）
- [ ] 三側全綠（各顯式 exit code，NEVER `| tail`）
- [ ] spec §11 標記從「未實作」改「as-built」；§11.3 前綴 defer 註記為 follow-up
- [ ] opus 全分支終審 → 修整波 → PR（base＝feat/api-connector；#62 好後此 PR 續）

## 收尾備忘

- **§11 follow-up**：自動前綴命名空間（跨組同名 connector）——v1 用全域唯一約束替代
- 跨組 join 護欄（§11.5）：prompt 規則「跨 group 關聯必須顯式 join key」在 Task 2 的 prompt 生成納入；量級護欄沿用既有
- 與 #63 replay 正交；#62 merge 後本分支 rebase master
