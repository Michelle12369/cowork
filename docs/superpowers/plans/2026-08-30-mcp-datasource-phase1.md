# MCP Datasource Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 對話驅動的 API datasource（Phase 1）：選 connector→鎖定互斥→agent 以 `land_as` 工具取數→寬鬆落表→分析，recipe 為 Phase 2 存料；in-code 模擬 connector 為主線、MCP adapter 並行。

**Architecture:** spec＝`docs/superpowers/specs/2026-08-30-mcp-datasource-design.md`（§4 契約、§5 機制、§5b 三側改動面、§9 Phase 切分）。Connector 供應層抽象在 `app/agent/connectors/`（LangChain 允許區）；落表/snapshot 在 engine（純度區）；DuckDB 鎖門改 `allowed_directories` 白名單模式（connector session 專用，檔案 session 行為 byte 不變）。

**Tech Stack:** Python 3.12/LangGraph/DuckDB≥1.2、Java 17/Spring Boot/Mongo、React 18。

## Global Constraints

- Branch：`feat/mcp-datasource`（自 master，spec 已在支上）
- deepagent：engine 純度 stdlib＋boto3＋openpyxl（connector 抽象/wrapper 放 `app/agent/connectors/`——TID251 豁免區）；`uv run pytest -q && uv run ruff check .` 必綠
- Java：constructor injection；測試命名 `methodName_condition_expectedBehavior`；`SPRING_DATA_MONGODB_URI=mongodb://localhost:27017/cowork-test ./mvnw test`
- 前端：React.FC/useSuspenseQuery/`import type`/無 any；`npx vitest run && npx tsc --noEmit -p tsconfig.app.json`
- **Token 紅線**：ssoToken NEVER 進 log/prompt/recipe/落盤——wire 與 contextvar 全程遮罩（比照 `CoworkContext.toString()`）
- **互斥不變式**：connector session 永無檔案源（雙向 409）——`allowed_directories` 鎖門模式依賴此
- 落表寬鬆（spec §4-2）：`read_json_auto` 直吃；底線＝`land_as` safe-identifier＋0 列不落
- Commit trailer 一律：
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
  `Claude-Session: https://claude.ai/code/session_01YANgdohkmgzDPhxeLJLgmK`

## 檔案地圖

- Create: `deepagent-service/app/agent/connectors/model.py`（抽象）、`registry.py`（in-code 版＋示範）、`catalog.py`（internal-owned seam）、`wrapper.py`（land_as 包裝）、`mcp_adapter.py`（T9）
- Create: `deepagent-service/app/engine/api_snapshot.py`（snapshot 落檔/remount）、`app/engine/recipe.py`（落表記錄）
- Modify: `deepagent-service/app/engine/duck.py`（allowed_directories）、`workspace.py`（api_snapshots/recipe dirs）、`request_context.py`（ssoToken）、`api/schemas.py`、`agent/chat_turn.py`、`agent/graph.py`、`main.py`（GET /connectors）
- Modify: `backend/.../domain/ChatSession.java`、`agent/AgentOrchestrator.java`、`service/FileService.java`、`provider/analysis/LangGraphAnalysisProvider.java`、`web/`（ConnectorController 新增）
- Modify: `frontend/src/`（connectorsApi、picker 元件、CoworkPage 接線）
- Modify: `scripts/internal-owned-paths.txt`＋`scripts/test-sync-upstream.sh`（catalog seam）

---

### Task 1: request_context 加 ssoToken＋wire schema 擴充

**Files:**
- Modify: `deepagent-service/app/engine/request_context.py`、`app/api/schemas.py`、`app/agent/chat_turn.py`、`app/agent/repair_flow.py`
- Test: `tests/test_request_context.py`（追加）

**Interfaces:**
- Produces: `current_sso_token: ContextVar[str|None]`、`require_sso_token() -> str`（未設或 None 拋 LookupError）、`set_request_identity(user_id, session_id, sso_token=None)`（tokens tuple 擴為 3）；`ChatRequest`/`RepairRequest` +`ssoToken: str | None = None`、`ChatRequest` +`selectedConnectors: list[str] = []`

- [ ] **Step 1: 失敗測試**（追加到 test_request_context.py，沿既有風格）：`require_sso_token` 設定後回值、未設拋 LookupError（訊息含 `sso_token`）、reset 後再取拋；`set_request_identity("u","s")` 不帶 token 時 `require_sso_token` 拋（None 視同未設——**fail loud，dev 無 SSO 時 connector 功能本來就不該通**）。
- [ ] **Step 2: 確認失敗** → `uv run pytest tests/test_request_context.py -q`
- [ ] **Step 3: 實作**：`current_sso_token` ContextVar；`set_request_identity` 第三參數（預設 None 也 set——token None 時 `require_sso_token` 拋 LookupError）；reset 對稱。schemas 兩欄位（additive、預設值）。`chat_turn.py`/`repair_flow.py` 的 `set_request_identity(request.userId, request.sessionId)` 改傳 `request.ssoToken`。**NEVER log token**。
- [ ] **Step 4: 全綠** `uv run pytest -q && uv run ruff check .`
- [ ] **Step 5: Commit** `feat(deepagent): request_context 加 ssoToken contextvar+wire 欄位`

---

### Task 2: Connector 抽象＋in-code registry＋示範 connector＋目錄 seam＋/connectors

**Files:**
- Create: `app/agent/connectors/__init__.py`、`model.py`、`registry.py`、`catalog.py`
- Modify: `app/main.py`（`GET /connectors`）、`scripts/internal-owned-paths.txt`＋`scripts/test-sync-upstream.sh`（+`deepagent-service/app/agent/connectors/catalog.py`，fixture 兩處補建檔——照 upload_decrypt 前例）
- Test: `tests/test_connectors_registry.py`、`tests/test_connectors_endpoint.py`

**Interfaces（Produces，後續 task 全部依賴）:**
```python
# model.py
@dataclass(frozen=True)
class ConnectorTool:
    name: str                      # 原名（未加前綴）
    description: str
    input_schema: dict             # JSON Schema；Phase 1 僅頂層 scalar properties
    call: Callable[[dict], object] # args -> 已解析 JSON；錯誤拋 ConnectorToolError(message)

class ConnectorToolError(Exception): ...  # message MUST 可行動（§4-5）

@dataclass(frozen=True)
class Connector:
    connector_id: str              # safe identifier（^\w+$）
    display_name: str
    tools: tuple[ConnectorTool, ...]
    skill_markdown: str            # 四段式劇本

# catalog.py（internal-owned 整檔複寫；repo 版回示範）
def load_connectors() -> tuple[Connector, ...]: ...
# registry.py
def demo_connector() -> Connector  # id="demo_quality"
def resolve_connectors(selected_ids: list[str]) -> tuple[Connector, ...]  # 未知 id 拋 ValueError 列可用清單
```

- [ ] **Step 1: 失敗測試**：demo connector 形狀（2 tools：`list_fabs` lookup 無參；`get_quality(fab, week)` 回**信封**`{"data":[...9 列...], "errorCode": ""}`——合成資料含一個淺巢狀欄 `device: {"id","name"}`，刻意演練寬鬆落表）；`resolve_connectors(["demo_quality"])` 回 1 個；未知 id 拋 ValueError 且訊息列出可用 id；`GET /connectors` 回 `[{"id":"demo_quality","name":...}]`（TestClient，沿 test_chat 的 app fixture 手法；bearer auth 同其他端點）。
- [ ] **Step 2: 確認失敗**
- [ ] **Step 3: 實作**：`catalog.py` docstring 註明 internal 整檔複寫（比照 `upload_decrypt.py` 前例）、repo 版 `return (demo_connector(),)`；demo 的 `get_quality.call` 用合成資料（固定 seed 生成，無網路）；`skill_markdown` 照四段式模板寫全（清單/順序/參數來源/範例——範例明示 `land_as` 用法）。main.py `GET /connectors`。sync harness fixture 補檔後 `bash scripts/test-sync-upstream.sh` 必綠。
- [ ] **Step 4: 全綠＋harness 綠**
- [ ] **Step 5: Commit** `feat(deepagent): connector 抽象+in-code registry+示範 connector+目錄 seam`

---

### Task 3: DuckDB allowed_directories 模式＋snapshot 落檔＋寬鬆落表

**Files:**
- Modify: `app/engine/duck.py`、`app/engine/workspace.py`（+`api_snapshots_dir` property＋ensure）
- Create: `app/engine/api_snapshot.py`
- Test: `tests/test_duck.py`（追加）、`tests/test_api_snapshot.py`

**Interfaces:**
- Produces: `open_locked_connection(sources, memory_limit="2GB", allowed_directories: list[str] | None = None)`——None＝現行行為 byte 不變（`enable_external_access=false`）；非 None＝`SET allowed_directories=[...]`（duckdb 語意：`enable_external_access=false` 下的例外白名單——實測留開時 no-op；效果＝僅名單內路徑可讀）＋`lock_configuration`。`land_snapshot(connection, connection_lock, workspace, alias, payload) -> LandingResult(columns: list[str], row_count: int)`——寫 `api_snapshots/{alias}.json`（temp+rename 原子）→鎖內 `CREATE OR REPLACE TABLE`→回欄名/列數；`payload` 頂層空陣列拋 `EmptyLandingError`（可行動訊息）；alias 過 `_validate_alias`。`remount_snapshots(connection, connection_lock, workspace) -> list[str]`——每個既存 snapshot 重建表。

- [ ] **Step 1: 失敗測試**：(a) `open_locked_connection([], allowed_directories=[dir])` 後鎖內 `read_json_auto(dir 內檔)` 可讀、dir 外檔拒；(b) None 模式行為回歸（既有測試不動＋新增斷言 external access 關）；(c) `land_snapshot` 信封 payload（demo 同形）落成表、STRUCT 欄存在（寬鬆實證）、0 列拋 EmptyLandingError、alias `"bad-name"` 拋、同 alias 重落 last-wins（列數變）；(d) `remount_snapshots` 兩檔重建兩表。
- [ ] **Step 2: 確認失敗**
- [ ] **Step 3: 實作**：duck.py 分支（`allowed_directories` 經 `Path.resolve()` 正規化）；api_snapshot.py 用 `json.dump` 落檔（engine 純度 OK）＋`CREATE OR REPLACE TABLE "{alias}" AS SELECT * FROM read_json_auto(?)` 於 `connection_lock` 臨界區；欄名以 `DESCRIBE` 取。
- [ ] **Step 4: 全綠**
- [ ] **Step 5: Commit** `feat(deepagent): duck allowed_directories 白名單+api snapshot 寬鬆落表`

---

### Task 4: recipe 記錄＋前置稽核

**Files:**
- Create: `app/engine/recipe.py`；Modify: `workspace.py`（+`recipe_dir`）
- Test: `tests/test_recipe.py`

**Interfaces:**
- Produces: `record_landing(workspace, *, connector_id, tool_name, args, land_as, observed_columns, input_schema_hash)`——append `recipe/landings.jsonl`；`record_tool_audit(workspace, *, connector_id, tool_name, args, landed: bool)`——append `recipe/audit.jsonl`；`load_landings(workspace) -> list[dict]`；`schema_hash(input_schema: dict) -> str`（`sha256(json.dumps(sort_keys=True))[:16]`）。**args 原樣記錄（無 token——token 不在 args）**；qN SQL 已由既有 `record_query` 持久化於 `queries/`，本 task 不重複。

- [ ] **Step 1: 失敗測試**：兩次 record_landing → load 回 2 筆序正確；schema_hash 對 key 順序不敏感；audit 檔獨立；損毀行跳過不炸（沿 load_all_results 的容錯手法）。
- [ ] **Step 2–4: TDD 循環＋全綠**
- [ ] **Step 5: Commit** `feat(deepagent): recipe 落表記錄+工具稽核——Phase 2 重放材料`

---

### Task 5: land_as 工具包裝＋每 turn 上限＋退貨整形

**Files:**
- Create: `app/agent/connectors/wrapper.py`
- Test: `tests/test_connector_wrapper.py`

**Interfaces:**
- Consumes: T2 抽象、T3 `land_snapshot`、T4 `record_landing/record_tool_audit`
- Produces: `build_connector_tools(connectors, connection, connection_lock, workspace, *, call_budget: int = 12) -> list[BaseTool]`——每個 ConnectorTool 產一個 LangChain tool：名`{connector_id}_{tool.name}`；args_schema＝pydantic `create_model` 自 input_schema 頂層 properties（type 映射 string→str|None…，全 Optional）＋`land_as: str | None = None`；行為＝剝 land_as→`tool.call(args)`→帶 land_as 則 `land_snapshot`＋`record_landing` 回「已落表 {alias}：N 列，欄位 …」摘要、不帶則回應 JSON 截斷至 LLM_VIEW 大小直接回；`ConnectorToolError`/`EmptyLandingError` 訊息原样回 agent（可行動）；其他例外包「connector 呼叫失敗：{type}」；**budget 歸零後一律回「本輪 connector 呼叫已達上限」**；每呼叫 `record_tool_audit`。實驗埋點：logger.info `connector_metric event=<landing_ok|landing_empty|tool_error|budget_exhausted> connector=… tool=…`（**無 args 值、無 token**）。

- [ ] **Step 1: 失敗測試**（用 demo connector＋in-memory duck 連線）：帶 land_as 落表→表可查、摘要含列數欄名、recipe 有記錄；不帶→回 JSON 文本、audit landed=false；壞 alias→可行動錯誤且無落表；budget=1 時第二呼叫被拒；ConnectorToolError 訊息透傳。
- [ ] **Step 2–4: TDD＋全綠**
- [ ] **Step 5: Commit** `feat(deepagent): land_as 工具包裝——呼叫點落表+上限+退貨整形+實驗埋點`

---

### Task 6: ChatTurn/graph 整合＋劇本 staging＋prompt 段＋remount

**Files:**
- Modify: `app/agent/chat_turn.py`、`app/agent/graph.py`（`build_agent` +`extra_tools: list[BaseTool] = []`）、prompt 檔（先讀 `app/agent/prompts.py` 找 dashboard 模式段落錨點）
- Test: `tests/test_chat.py`（追加情境）、`tests/test_chat_turn_connectors.py`

**Interfaces:**
- Consumes: T1–T5 全部
- 行為：`ChatTurn.__aenter__`——`request.selectedConnectors` 非空時：`resolve_connectors`→連線改 `open_locked_connection([], allowed_directories=[str(workspace.api_snapshots_dir)])`（**selectedConnectors 與 sources 同時非空→直接 raise，互斥後端已擋、此為防禦**）→`remount_snapshots`→`build_connector_tools`→傳入 `build_agent(extra_tools=…)`；劇本 staging：每個選定 connector 的 `skill_markdown` 寫入 `workspace.skills_dir/connectors/{id}/SKILL.md`（漸進揭露交給既有 deepagents skills 機制）；prompt 追加 connector 段（有選定時才注入）：一行索引 per connector＋land_as 何時用＋「>1 connector 時 join 需使用者明確指定 key」護欄＋lookup→ask_user 銜接指引（措辭沿既有 prompt 風格，先讀再寫）。空 selectedConnectors＝現行為 byte 不變（e2e 釘）。

- [ ] **Step 1: 失敗測試**：帶 selectedConnectors 的 ChatRequest → agent tools 含 `demo_quality_get_quality`（檢 build_agent 收到的 tools 名單，mock 模型）；skills_dir 有 connectors/demo_quality/SKILL.md；sources 同帶→raise；空 selected→tools 不含 connector 項、連線走舊模式（斷言 external access 關）。
- [ ] **Step 2–4: TDD＋全綠**
- [ ] **Step 5: Commit** `feat(deepagent): connector 模式接進 ChatTurn——掛載/劇本/prompt/remount/互斥防禦`

---

### Task 7: Backend session 鎖定＋互斥＋wire＋connectors 代理

**Files:**
- Modify: `backend/.../domain/ChatSession.java`（+`selectedConnectors: List<String>`）、`agent/AgentOrchestrator.java`（定案邏輯——先讀 prepare 現況）、`service/FileService.java`（互斥）、`provider/analysis/LangGraphAnalysisProvider.java`（wire +selectedConnectors +ssoToken）、`web/dto`、新 `web/ConnectorController.java`＋`service/ConnectorCatalogService.java`（代理 deepagent `GET /connectors`，graceful-empty：deepagent 不可達/空→`[]`）
- Test: 對應 service/controller 測試

**Interfaces:**
- 定案語意（spec §5）：session.selectedConnectors null＝未定案；**首訊**（AgentOrchestrator.prepare 內、既有 session touch 處）request 帶非空 selectedConnectors 且 session 未定案→驗證「無 active 檔案」（有→409 Conflict）→寫入定案；已定案→忽略 request 值、以存儲為準。`FileService.upload`：session.selectedConnectors 非空→409（訊息「本對話已鎖定 API 資料源，上傳請開新對話」）。wire：`LangGraphAnalysisProvider` body +`selectedConnectors`（自 session 存儲值）＋`ssoToken`（自 CoworkContext 值物件——**async 邊界前已物件化的既有 pattern，先讀該檔確認**；log 遮罩）。`SessionDetailDto` +selectedConnectors（前端鎖定態用）。409 用既有 conflict 例外（grep `409|Conflict` 找 GlobalExceptionHandler 現況，無則建 `ConflictException` 於 exception 包）。
- [ ] **Step 1: 失敗測試**：`prepare_firstMessageWithConnectors_locksSelection`、`prepare_lockedSession_ignoresRequestConnectors`、`prepare_sessionWithActiveFiles_connectorsRejected409`、`upload_lockedConnectorSession_returns409`、wire 測試斷言 body 含 selectedConnectors/ssoToken 且 log 不含 token 值、connectors 代理 graceful-empty。
- [ ] **Step 2–4: TDD＋全套 `./mvnw test`（cowork-test URI）**
- [ ] **Step 5: Commit** `feat(backend): connector session 鎖定+檔案互斥+wire 擴充+目錄代理`

---

### Task 8: 前端 connector picker＋鎖定態＋互斥 UX

**Files:**
- Create: `frontend/src/api/connectorsApi.ts`、`frontend/src/components/connectors/ConnectorPicker.tsx`＋測試
- Modify: `CoworkPage.tsx`（狀態與首訊 payload）、上傳入口元件（先讀 UploadModal/AttachmentsPopover 確認掛點）、`types.ts`
- Test: Vitest 行為測試

**Interfaces:**
- `GET /api/connectors` → `ConnectorInfo{id,name}[]`；空陣列→picker 整個不渲染。Picker：多選（antd Select/Checkbox 沿現有元件習慣）、置於上傳區旁；選了 connector→上傳入口 disabled＋提示；session 已定案（SessionDetailDto.selectedConnectors 非空）→picker 唯讀＋「資料源已鎖定——換資料源請開新對話」；有 active 檔案→picker disabled。首訊 send payload +selectedConnectors。
- [ ] **Step 1: 失敗測試**：空目錄不渲染；選定後上傳 disabled；鎖定態唯讀文案；首訊 payload 帶 id 陣列（mock sendMessage 斷言）。
- [ ] **Step 2–4: TDD＋`npx vitest run && npx tsc --noEmit -p tsconfig.app.json`**
- [ ] **Step 5: Commit** `feat(frontend): connector 選擇器+鎖定態+檔案互斥 UX`

---

### Task 9: MCP adapter（並行泳道）

**Files:**
- Create: `app/agent/connectors/mcp_adapter.py`；Modify: `catalog.py` docstring（internal 版可回 MCP-backed connectors 的說明）
- Test: `tests/test_mcp_adapter.py`（fixture：本地起 FastMCP stateless server——dev 依賴加 `mcp` SDK 至 dev-dependencies，非 runtime）

**Interfaces:**
- Produces: `load_mcp_connector(connector_id, display_name, base_url) -> Connector`——httpx 直發 JSON-RPC（stateless：每請求 POST `tools/list`／`tools/call`，`Authorization: Bearer {require_sso_token()}` **呼叫當下取**）；劇本自 resource `skill://usage` 讀（無則空劇本＋warning）；tool 回應映射為已解析 JSON；MCP error → `ConnectorToolError`。**spike 結論寫進模組 docstring**（對 2025-03-26 stateless 模式 vs 2026-07-28 原生 stateless 的相容做法）。
- [ ] **Step 1: 失敗測試**：fixture server（FastMCP `stateless_http=True`，一個 echo data tool＋usage resource）→ load 出 Connector、tools/call 帶到 header（server 端斷言收到 Bearer）、error 轉 ConnectorToolError。
- [ ] **Step 2–4: TDD＋全綠**
- [ ] **Step 5: Commit** `feat(deepagent): MCP stateless adapter——每請求 token header,spike 結論入檔`

---

### Task 10: e2e 煙測＋三側驗證＋PR

- [ ] **Step 1**：三側全套（deepagent/backend/frontend＋sync harness）全綠。
- [ ] **Step 2: 實機煙測**（verification-before-completion）：起雙服務（backend 用 cowork-test URI＋測試埠），curl 建 session→首訊帶 `selectedConnectors:["demo_quality"]`（觀察 SSE：agent 呼 lookup→落表→dashboard 產出）→驗 workspace 有 `api_snapshots/*.json`、`recipe/landings.jsonl`、`queries/*.sql`→第二訊驗 remount→上傳檔案得 409→無 connector 的 csv session 走舊路 byte 不變。dev 無 SSO token：wire ssoToken null→`require_sso_token` 在 demo connector `call` 不需 token（合成資料不打真 API）——**煙測即驗證「demo 不碰 token、MCP/真 API 才要」的分層**。
- [ ] **Step 3: PR**：`gh pr create` base master；描述含 spec 連結、機制摘要、實驗四訊號怎麼觀測、互斥/鎖定語意、token 紅線、Phase 2 預告；**同 PR 註記關閉 #62/#63/#65**（spec §10 決議）。opus 全分支終審後更新描述。

---

## Self-Review 紀錄

- Spec §5b Phase 1 逐項對照：目錄 seam→T2、UI→T8、token wire→T1/T7、供應層雙實作→T2/T9、寬鬆落表＋snapshot→T3、劇本 staging＋prompt→T6、退貨整形＋上限→T5、recipe→T4（qN SQL 由既有 record_query 覆蓋）、實驗埋點→T5/T10、互斥鎖定→T7/T6（防禦）。無缺口
- 型別一致：`Connector/ConnectorTool/ConnectorToolError`、`land_snapshot/LandingResult/EmptyLandingError`、`build_connector_tools(call_budget)`、`open_locked_connection(allowed_directories)` 跨 task 簽名已對齊
- 已知風險記載：pydantic create_model 對 JSON schema 的映射限 Phase 1 頂層 scalar（spec §4 契約參數本就如此）；`allowed_directories` 需 duckdb 執行期支援（T3 測試即驗證，#62 曾以 1.5.5 spike 過）
