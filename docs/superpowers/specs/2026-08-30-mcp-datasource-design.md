# MCP Datasource——API 作為 csv/xlsx 以外的資料源 — 設計

> 狀態：2026-08-30 討論定案。從零重設計（PR #62/#63/#65 僅作概念參考，不復活）；本 spec 涵蓋需求一（對話驅動取數）與需求二（publish/viewer 重放），實作分兩 Phase。

## 1. 需求

**需求一（對話驅動）**：使用者在對話開始前選擇 API connector（可多選）；每個 connector 對應一組 API——含「真正拉資料的 data API」與「提供 data API 參數選項的 lookup API」。首訊後 connector 鎖定不可改；**csv/xlsx 上傳與 connector 同 session 互斥**。對話中 agent 收集 data API 的 request payload：必要時打 lookup API 取選項→反問使用者（ask_user），或於 prompt 直接列選項——同時服務「熟」與「完全不會操作」兩類使用者。

**需求二（publish/分享/viewer 重放）**：dashboard 有 publish 按鈕可分享。其他人開啟時**拿自己的 SSO token 重打同樣的 data API**，看到自己有權看的資料。schema 漂移與 lookup 資料漂移 MUST 語意化失敗，不可白屏。

## 2. 決策紀錄（討論定案）

| 決策 | 結論 |
|---|---|
| 取數協定 | **MCP**：connector＝MCP server、data/lookup API＝tools、參數 schema＝inputSchema |
| MCP server 所有權 | **internal 自寫自養**（含攤平語意、憑證、tool 版本化）；repo 只做接入/落表/鎖定/replay 機制——同 upload_decrypt 整檔複寫哲學 |
| 分析引擎 | **維持 DuckDB**（1NF 為撰寫指引，Phase 1 寬鬆落表先實驗——見 §4-2）；「引擎換 Python（沙箱執行器）」獨立成案，不扣住本案（模型現況 deepseek-v4-flash 使其值得評估，但沙箱是獨立的平台級工作） |
| Auth | **user SSO token 上 wire**（Java→deepagent→MCP header），對話期用發話者的、重放期用 viewer 的——資料權限由下游 API 依 token 裁決 |
| HTML 直打 | 永遠不成立（MCP 是 agent 協定）；viewer 重抓一律 server 端零 LLM replay |

## 3. 架構總覽

```
對話期（Phase 1）
  選 connector（多選、與檔案互斥）→ 首訊鎖定
  → deepagent 只掛選定 server 的 MCP tools
  → agent: lookup tool → ask_user 反問 → data tool
  → 寬鬆落表（read_json_auto，底線見 §4-2）→ DuckDB 表（alias）→ snapshot 持久（跨 turn remount）
  → 分析 → dashboard；每次 data tool 呼叫記入 replay manifest

分享重放期（Phase 2）
  owner publish（凍結 replay manifest＋HTML）→ 分享
  → viewer 開啟 → Java 取 viewer SSO token → deepagent 零 LLM replay：
    分級驗證 ①②③④（見 §6）→ 凍結參數重打落表呼叫（viewer token）
  → 重落表 → 重跑 replay manifest 的 qN SQL → 注入 HTML → viewer 看到自己權限內的資料
```

## 4. Internal MCP server 契約規範（隨本 spec 交付給 internal 的文件）

1. **操作劇本 skill（取代靜態 tool 分類，一個 connector 可供多份）**：每個 connector MUST 附至少一份劇本文件——tools 清單與語意、**呼叫關係與順序**（含多步相依、lookup 餵 lookup、結果當他 tool 參數等非典型流程）、參數來源、範例。交付通道：MCP server 以 **`skill://` scheme 的 MCP resource** 自述，每個 resource 一份 skill（`skill://usage` 為主劇本慣例，但不特殊處理——與其他 `skill://*` 一視同仁）；in-code 模擬版隨 connector 物件附帶（`skills: dict[name, markdown]`）。tools 不做 data/lookup 靜態分類——同一 tool 可依流程扮演不同角色；「落不落表」由呼叫點決定（見 §5 `land_as`）。不落表的回應巢狀不限。每份劇本 MUST 依**四段式模板**撰寫（tools 清單與語意／呼叫順序與相依／參數來源／範例）——多 connector 多作者時的品質地板；每 connector tools 數**建議 ≤10**（tool 膨脹源頭治理）。
2. **落表形狀——Phase 1 採寬鬆模式，實驗後定案**。給 internal 的**撰寫指引**（軟性，管線不強制）：目標形＝1NF long format——每元素一列、每格純量（日期 ISO-8601）、一對多展開成多列、欄集一致、欄名 snake_case，「像一張乾淨 CSV，用 JSON 送、帶型別」。**Phase 1 落表管線寬鬆**：回應直接交 DuckDB `read_json_auto`（信封成怪表、淺巢狀成 STRUCT 欄照吞），僅保兩條底線——`land_as` safe-identifier 驗證（安全）與 0 列不落表。**候補機制**（實驗證據觸發才建）：record_path 信封拆封＋錯誤慣例宣告、淺巢狀攤平層、1NF 硬驗證。實驗裁決訊號：(i) agent 對 STRUCT/信封表的 SQL 成功率；(ii) 同 tool 兩次拉取的推斷 schema 穩定性；(iii)「圖有出來但內容錯」的垃圾落表頻率；(iv) 複選 connector 時的 tool 選擇準確率。replay manifest 觀測 schema 記 DuckDB 推斷後欄名——寬鬆模式下 §6 關卡 ③ 可能偏噪，屬實驗已知代價，Phase 2 開工前隨裁決一併定案。
3. **攤不平的判斷階梯**：① 拆多表（一 tool 一表＋join key；管線原生多 alias）→ ② server 端預切片/預聚合（樹狀給切面，切面旋鈕＝tool 參數）→ ③ 承認非 data（改列 lookup/context 或不納入）。「JSON 字串塞一欄」等半吊子逃生艙不開。
4. **Tool＝版本化契約**：breaking change（改名/刪欄/改參數）MUST 開新 tool 名；演進盡量 additive。
5. **錯誤訊息 MUST 可行動**：缺參指名、**值不合法/過期給候選**——這條同時是對話期確定性退貨與 **replay 漂移偵測的承重牆**（重放時過期參數靠它以語意化錯誤浮現，見 §6 ②）。
6. **量級 caps**：rows/bytes 上限由 server 端強制並於超限時明確報錯。
7. **Tool 回傳 MUST 為 structured output**（回 dict/list——FastMCP 自動生成 structuredContent；純文字回應會被 client 以可行動錯誤拒收）。
8. **傳輸模式 MUST 為 stateless streamable HTTP**（FastMCP `stateless_http=True` 級別的一個旗標）：每個 tool call 自包含、無跨請求 session 狀態——per-user auth 因此是「每請求各帶各的 Authorization」，且天然適配多 pod/load balancer。放棄的 server 推播功能（notifications/sampling）本案 tools 用不到。契約同時相容 2025-03-26 stateless 模式與 2026-07-28 原生 stateless spec（後者已把協定級 session 整個移除——本契約即協定演進方向）。

## 5. Repo 端機制

> **架構定案（純 MCP，取代原「雙實作」設計——PR 全數 review 過審後的最終形）**：目錄搬進 Java-owned Mongo `connector_catalog`（Phase 1 唯讀，種子資料 mongosh 手動 insert，無寫入 API）；wire 由 Java 解出**完整** MCP server 規格 `connectors: [{id, name, url}]` 送給 deepagent；deepagent **無目錄、無狀態**——不再查任何目錄，直接對 wire 給的每個 url 打 `load_mcp_connector`。in-code 模擬版（`registry.demo_connector()`）降級為**純測試 fixture**（供 pytest 免網路直組 `Connector` 物件），不再是 production 過渡路徑；dev/demo 要端對端玩 UI 時，**自行在本機另架 MCP server**（repo 不隨附 server），Mongo insert 一筆指向 `http://host.docker.internal:<port>/mcp` 的目錄項即可（compose 檔有現成 mongosh 指令註解）。

- **Connector 目錄**：Java-owned，存於 Mongo `connector_catalog` collection（`ConnectorCatalogEntry{connectorId, displayName, mcpUrl}`）——Phase 1 唯讀，種子資料以 mongosh 手動 insert，無管理 API；空 collection graceful-empty（`GET /api/connectors` 回 `[]`）。deepagent 端沒有對應目錄，也不再暴露 `GET /connectors`。
- **Session 選擇與鎖定（Java）**：`ChatSession` 記 `selectedConnectors`；**首訊定案**後不可改（概念沿 #65）；**互斥**：session 已有 active 檔案→選 connector 拒（409），已鎖 connector→上傳拒（409）。換源＝開新對話。
- **Wire**：files/sources 之外新增 connector 資訊——Java 送的不是裸 id 清單，而是從 Mongo 目錄解出的**完整規格** `connectors: [{id, name, url}]`（見 §5c）；**SSO token/URL 走 HTTP header**（Java `CoworkContext.ssoToken`/`ssoUrl` → `X-SSO-Token`/`X-SSO-Url` request header → deepagent；NEVER 走 JSON body。log 全程遮罩，比照 `CoworkContext.toString()` 前例）。
- **deepagent 接入——純 MCP，無目錄無狀態**：repo 定義統一的 connector-tools 抽象（一個 connector 供應一組 tools：`name`／`inputSchema`／可呼叫體，**外加一組劇本 skills**）。repo 包裝每個 tool 加選用參數 **`land_as`（alias）**——帶了＝「寬鬆落表（§4-2 底線）→DuckDB→記 replay manifest」，沒帶＝回應進 agent context（lookup 式使用）；何時帶由劇本引導，落表決策在呼叫點而非 tool 靜態型別。劇本沿用 deepagent 既有 skills staging 機制、**只 stage 選定 connector 的劇本**（零注入原則延伸），且**漸進揭露**：context 僅含每個已選 connector 的一行索引，agent 需要時才讀劇本全文——目錄規模不影響單 session 成本。**複選情境**：跨 connector 關係不入劇本（配對知識 N² 不可維護）——沿 #65 概念以「跨 connector join 需使用者明確指定 key」護欄 prompt 承接；複選 tool 選擇準確率列實驗第四訊號。**唯一實作路徑**：`app.agent.connectors.mcp_adapter.load_mcp_connector(id, name, url)` 對 wire 上每個 `ConnectorSpec` 連上其 MCP server（**stateless streamable HTTP**，見 §4-7），把其 tools 映射進抽象；每次呼叫（`tools/list`／`tools/call`／`resources/read`）帶當下 contextvar 的 SSO token 進可配置 header——stateless 下無連線綁身分問題。`registry.demo_connector()` 為純測試 fixture（免網路直組 `Connector` 物件，供 pytest 用）；本機自架的 MCP server 則是 dev 端對端驗證的餵料方式——兩者都不是獨立的 production 分支。掛載範圍一律**只掛選定 connector 的 tools**（未選組零注入——概念沿 #65）。
- **落表管線**：`land_as` 回應→寬鬆落表（`read_json_auto` 直接吃；底線＝**0 列不落表**——空陣列推不出 schema，回可行動訊息由 agent 轉告）→DuckDB alias（沿 `open_locked_connection` 鎖門）→snapshot 原子落檔（**落檔即記 sha256**）＋跨 turn remount——remount/replay **按 replay manifest 清單＋hash 驗證掛載**、非目錄 glob（`allowed_directories` 實給讀寫權，模型 SQL 可覆寫/種植 snapshot 檔——hash 驗證使竄改 fail loud，守住跨 turn 與 Phase 2 溯源）。**`land_as` 為模型控制字串：MUST 過 safe-identifier 驗證；同 alias 重落表＝取代（last-wins）**。多 connector 掛載時 **tool 名以 connector id 前綴命名空間化**（防跨 server 撞名）。
- **退貨整形與上限**：MCP 錯誤包一層可行動整形；每 turn tool 呼叫上限。
- **Replay manifest 記錄**：① **落表呼叫**（server id＋tool name＋args＋inputSchema hash＋觀測 schema）；② **qN SQL**（agent 對落表資料計算 __ERD_RESULTS__ 的查詢——重放鏈的後半，沿 #63 概念；引用欄集自 SQL 解析）；③ 前置呼叫僅記錄供稽核、**不重放**。**重放＝凍結參數重打落表呼叫（viewer token）→ 重落表 → 重跑 qN SQL → 注入**——不依新 lookup 重推參數（否則 dashboard 靜默變成另一個切片）；過期參數由契約 §4-5 可行動錯誤浮現。

## 5b. 三側改動面

### 前端（React）
**Phase 1**：
- Connector 選擇器：對話開始前的多選 UI（來源 `GET /api/connectors`；空目錄→整個功能隱藏）；已選 chips 顯示
- 首訊後鎖定態：選擇器唯讀＋「資料源已鎖定——換資料源請開新對話」提示（#65 概念）
- 互斥 UX：已選 connector → 上傳入口禁用（含說明）；已有 active 檔案 → connector 選擇禁用
- Wire：首訊 payload 帶 selectedConnectors（定案由後端執行）
**Phase 2**：publish 按鈕、分享 link 產生/複製 UI、viewer 開啟頁（replay 載入態＋漂移語意錯誤卡的呈現）

### 後端（Java / Spring Boot）
**Phase 1**：
- `GET /api/connectors`：目錄端點（讀 Mongo-backed `connector_catalog` collection——`ConnectorCatalogService.listCatalog()`；Phase 1 唯讀，種子資料 mongosh 手動 insert，無寫入 API；空 collection graceful-empty）
- `ConnectorCatalogEntry`（`@Document(collection = "connector_catalog")`）：`connectorId`／`displayName`／`mcpUrl`；`ConnectorCatalogService.resolveSpecs`（首訊定案的 id 清單→wire 完整規格 `ConnectorSpec{id,name,url}`；缺項 404「資料源 X 已下架」，`mcpUrl`/`displayName` 空白 404「資料源 X 設定不完整」）與 `.validateKnownIds`（首訊鎖定前擋未知 id，409，帶排序後可用 id 清單）
- `ChatSession` +`selectedConnectors`；**首訊定案**（null=未定案；定案後請求值一律忽略、存儲值權威——#65 session-lock 語意）
- 互斥驗證雙向 409：`FileService.upload` 拒已鎖 connector 的 session；connector 定案拒已有 active 檔案的 session
- Wire 擴充（`LangGraphAnalysisProvider`）：body +`connectors: [{id, name, url}]`（`AgentOrchestrator` 在 prepare 階段以 `ConnectorCatalogService.resolveSpecs` 把 session 存儲的 `selectedConnectors` id 清單解成完整規格才組進 body——deepagent 收到的一律是可直接連線的完整規格，不是裸 id）；+`X-SSO-Token`/`X-SSO-Url` request header（自 `CoworkContext.ssoToken`/`ssoUrl`；NEVER 走 body；log 全程遮罩）
- DTO/`@Schema`/`@Valid`/`@Operation` 照規範
**Phase 2**：publish 端點（凍結 replay manifest＋HTML 為分享版本）、capability link、viewer 開啟端點（取 viewer token→觸發 deepagent replay→沿 #66 認證交付管線呈現）

### deepagent（Python / LangGraph）
**Phase 1**：
- `request_context` +`current_sso_token`/`current_sso_url`＋`require_sso_token()`/`require_sso_url()`（fail-loud）；`ChatTurn`/repair 設定與 reset（沿既有 token 生命週期紀律）；值來自 `/chat`、`/repair` handler 讀 `X-SSO-Token`/`X-SSO-Url` header（`Annotated[str | None, Header(...)]`），NEVER 是 request body 欄位
- `ChatRequest`/`RepairRequest` schema +`connectors: list[ConnectorSpec]`（`{id, name, url}`；body 不含 SSO 欄位）
- **Connector 供應層——純 MCP，無目錄無狀態**：抽象（id／tools／劇本）＋單一 production 實作 `mcp_adapter.load_mcp_connector(id, name, url)`（stateless client、每請求 token header）；`ChatTurn` 對 wire 上每個 `ConnectorSpec` 逐一呼叫，deepagent 端**沒有目錄、沒有靜態註冊**（catalog seam 已移除，目錄權威在 Java Mongo）。`registry.demo_connector()` 降級為**純測試 fixture**（pytest 免網路直組 `Connector` 物件，`app/agent/connectors/registry.py` 模組 docstring 明載「production wire 路徑一律走 mcp_adapter.load_mcp_connector」）；dev 端對端驗證＝本機自架 MCP server（repo 不隨附），在 Mongo `connector_catalog` insert 一筆指向 `host.docker.internal` 的目錄項即可（見 compose 檔註解），不需要改任何程式碼分支。
- **Tool 包裝**：`land_as` 選參注入、connector id 前綴命名空間、轉發前剝除、safe-identifier 驗證
- **寬鬆落表管線**：`read_json_auto`→DuckDB alias（0 列不落）→snapshot 原子落檔＋跨 turn remount（沿 #62 概念）
- **劇本 staging**：只 stage 選定 connector 的每一份 skill＋每 connector 一行索引（漸進揭露）；MCP `resources/list` 篩 `skill://` scheme、逐一 `resources/read` 抓取
- Prompt 段：connector 模式通用說明（land_as 引導、跨 connector join 護欄、lookup→ask_user 劇本銜接）
- 退貨整形＋每 turn 呼叫上限；**replay manifest 記錄**（落表呼叫＋qN SQL＋前置稽核，存 workspace）
- **實驗觀測埋點**：四訊號可量測（SQL 成功率、schema 穩定性、垃圾落表、複選 tool 準確率）
**Phase 2**：replay 端點（凍結參數重打→重落表→重跑 qN SQL→注入）＋分級驗證 ①②③④

## 5c. Wire API 契約全覽（Java↔deepagent 實際改動清單）

### Java → deepagent `/chat`（POST，SSE）
| 項目 | 內容 | 來源 |
|---|---|---|
| Header `X-SSO-Token`* | 使用者 SSO token；**值非空才帶** | `CoworkContext.ssoToken`（internal filter 填；external 線 null 不帶） |
| Header `X-SSO-Url`* | SSO URL；值非空才帶 | `CoworkContext.ssoUrl` |
| Body `connectors: [{id, name, url}]` | 該 session 已鎖定的 connector，**解成完整 MCP server 規格**（純 MCP 定案後改此形，取代原本送裸 id 清單的 `selectedConnectors: string[]`）；空陣列＝檔案模式 | `ChatSession.selectedConnectors`（存儲的 id 清單）經 `ConnectorCatalogService.resolveSpecs` 解析 Mongo `connector_catalog` 而得（**以 session 存儲值為準**，非請求值） |
| Body 其餘欄位 | 不變（sessionId/userId/message/history/sources/previousDashboardHtml） | — |

\* Header 名稱**兩側皆可配置**：Java 出站＝`AnalysisAgentProperties.ssoTokenHeader/ssoUrlHeader`；deepagent 入站＝env `SSO_TOKEN_HEADER`/`SSO_URL_HEADER`（預設同名，internal 名稱不同時兩側各自覆寫對齊）。token/url **不在 JSON body**。deepagent 側收到 `connectors` 後直接逐一 `load_mcp_connector(id, name, url)`，不做任何目錄查詢。

### Java → deepagent `/repair`（POST）
同樣帶兩個 SSO header（值非空才帶）；body 無 SSO 欄位。repair 不使用 connector（無 connectors）。

### 前端 → Java 新增
| 項目 | 內容 |
|---|---|
| `GET /api/connectors` | 新端點：讀 Java-owned Mongo `connector_catalog` collection（`ConnectorCatalogService.listCatalog()`）——**不再代理 deepagent**（deepagent 已無目錄/`GET /connectors`）；空 collection → `[]`（graceful-empty）。回 `ConnectorInfoDto{id, name}` |
| `SendMessageRequest.selectedConnectors: List<String>` | 首訊帶使用者選擇（前端仍只送裸 id 清單；解成完整 wire 規格是 Java 內部 `resolveSpecs` 的事，前端無感）；session 已定案後此值被忽略 |
| `SessionDetailDto.selectedConnectors: List<String>` | 前端渲染鎖定態用（非空＝已鎖定） |
| `FileService.upload` 409 | session 已鎖 connector → 409「本對話已鎖定 API 資料源」 |
| 首訊定案 409 | session 有 active 檔案時帶 selectedConnectors → 409 |
| 首訊定案未知 id 409 | `ConnectorCatalogService.validateKnownIds` 擋下——訊息列出未知 id 與目前可用 id（排序後） |

### Java 資料模型新增
`ChatSession.selectedConnectors: List<String>`（null＝未定案；首訊寫入後不可改）。`ConnectorCatalogEntry`（`@Document(collection = "connector_catalog")`）：`connectorId`／`displayName`／`mcpUrl`，唯一索引在 `connectorId`（`MongoIndexInitializer`）。`AgentRequest` 內部擴充 `connectorSpecs: List<ConnectorSpec>`／ssoToken/ssoUrl（toString 全遮罩）。

### deepagent → connector API（MCP adapter 出站）
轉送兩個 header 給 connector API：名稱由 env `CONNECTOR_SSO_TOKEN_HEADER`/`CONNECTOR_SSO_URL_HEADER` 決定（預設 `X-SSO-Token`/`X-SSO-Url`）；token header 必帶（無身分即 fail-loud）、url header 值存在才帶。呼叫皆為 stateless JSON-RPC POST（`tools/list`／`tools/call`／`resources/read`）。注意：若 internal server 期望 `Authorization`，可設 `CONNECTOR_SSO_TOKEN_HEADER=Authorization`，但值為裸 token（無 `Bearer ` 前綴）——server 端需接受此形式。

## 5d. Agent 端執行期產物與資料流

### Workspace 檔案佈局（connector session 新增部分）
```
{workspace root}/
├─ api_snapshots/{alias}.json     # land_as 落表的原始回應（原子寫入；LandingResult 附 sha256）
├─ replay/
│  ├─ landings.jsonl              # 落表呼叫記錄（replay manifest 本體，Phase 2 重放材料）
│  └─ audit.jsonl                 # 全部 connector 工具呼叫稽核（含未落表/失敗）
├─ queries/{qN}.sql               # agent 對落表資料跑的 SQL（既有機制）
├─ results/{qN}.json              # __ERD_RESULTS__ 材料（既有機制）
├─ dashboard.html                 # 產出（既有）
└─ .skills/connectors/{id}/{skill}/SKILL.md  # 選定 connector 的每份劇本（每 turn 重 stage，不入快照）
```
以上（除 .skills）隨 workspace 快照 zip（`gen-*.zip`）持久化、跨 turn/跨 pod remount。

### Replay manifest（原名 replay manifest，模組 `app/engine/replay_manifest.py`）記錄內容
**landings.jsonl 每筆**：`connector_id`、`tool_name`、`args`（原樣；token 不在 args）、`land_as`（表 alias）、`observed_columns`（DuckDB 推斷後欄名）、`input_schema_hash`（tool inputSchema 的 sha256 前 16 碼）、`snapshot_sha256`（落檔 bytes 完整 hash）。
**audit.jsonl 每筆**：`connector_id`、`tool_name`、`args`、`landed: bool`。
**用途**：`landing_hashes()` 取每 alias 最後一筆 sha256 → 下一 turn remount 按清單驗 hash 掛載（竄改 fail-loud）；Phase 2 重放＝凍結 args 重打落表呼叫＋重跑 `queries/` 的 qN SQL。

### Connector tool call 完整流程（每一次呼叫）
```
LLM 發 tool call「{connector_id}_{tool 原名}」(args ± land_as)
→ wrapper：每 turn 呼叫上限檢查（超限→回「已達上限」）
→ 剝除 land_as → connector 實作呼叫（in-code 直打 API／MCP adapter 轉送含 SSO headers）
→ 無 land_as（lookup 式）：回應 JSON 序列化、截 8000 字元回給 LLM；audit 記 landed=false
→ 有 land_as：safe-identifier 驗證 → 回應原子落檔 api_snapshots/{alias}.json（算 sha256）
   → DuckDB CREATE OR REPLACE TABLE "{alias}"（read_json_auto 寬鬆吃）
   → landings.jsonl＋audit.jsonl 記錄 → 回給 LLM 一行摘要
→ 任何錯誤：可行動訊息字串回 LLM（不炸 graph）；記 audit landed=false
```

### LLM 可見資訊（connector 模式）
1. **System prompt 附註**（有選定 connector 才注入）：每 connector 一行索引（id＋名稱＋可用劇本名稱清單）、命名橋接規則（劇本原名＋前綴＝實際工具名）、land_as 使用時機、lookup→反問銜接、>1 connector 的 join 護欄
2. **劇本 skill**（漸進揭露——LLM 要用時才讀 `.skills/connectors/{id}/{skill}/SKILL.md` 全文；一個 connector 可能有多份）：四段式（tools 清單與語意／呼叫順序／參數來源／範例）
3. **Tool 定義**：前綴後名稱＋描述（connector 顯示名＋tool 描述）＋inputSchema 欄位＋`land_as` 選參
4. **呼叫回饋**：lookup＝截斷後 JSON；落表＝「已落表 {alias}：{N} 列，欄位 {...}」一行摘要（**原始資料不進 context**，分析走 run_sql）；錯誤＝可行動訊息
5. **不可見**：SSO token/url、snapshot 檔案內容、replay manifest、其他未選 connector 的一切

## 6. 漂移防護——replay 分級驗證（需求二核心）

| 關卡 | 偵測 | viewer 所見 |
|---|---|---|
| ① tool 存在＋inputSchema hash | server 改版、tool 改名/改參數 | 「資料源接口已變更，請聯絡擁有者」 |
| ② 參數過期 | 重放序列時，過期/不合法參數由 server 依契約 §4-5 回可行動錯誤（含候選） | 「選項 X 已不存在」＋現行候選（改選互動＝Phase 2+ 接縫） |
| ③ 欄位子集檢查 | 引用欄集（自 replay manifest qN SQL 解析）⊆ 實際欄集（新增欄無害） | 「資料欄位結構已變更」 |
| ④ 渲染韌性 | 漏網之魚 | 單卡 try/catch＋resolver「—」fallback（沿 T26/data-bind 機制），壞卡不壞頁 |

**Viewer 權限≠漂移**：token 不同導致的空/少資料是 feature，正常渲染空狀態；③ 只驗結構不驗列數。

## 7. Publish/分享模型（Phase 2）

- publish＝凍結 replay manifest＋HTML 為可分享版本；分享預設形＝**capability link＋SSO 登入必須**（知道連結且登入者可開；資料層權限交給下游 API 依 viewer token 裁決）——如 internal 要更細的分享對象控制，於 Phase 2 細化。
- viewer 呈現走既有 artifact 認證交付管線（axios→srcdoc，#66 機制）；replay 在 server 端完成後注入，不動 CSP `connect-src 'none'`。

## 8. 安全

- **Token 邊界**：SSO token 只活在 Java context、wire header（`X-SSO-Token`，遮罩）、deepagent contextvar、MCP request header；NEVER 進 log/prompt/replay manifest/落盤，NEVER 走 JSON body。
- **Prompt injection 面**：tools 唯讀、資料權限在下游 API、每 turn 呼叫上限；MCP server 為 internal 自有（無第三方工具描述注入面）。
- **鎖門不變**：DuckDB `enable_external_access=false`＋`lock_configuration` 照舊；MCP 呼叫只發生在 engine 掛表之前。

## 9. Phase 切分與風險

**Phase 1（對話驅動）**：connector 目錄 seam、UI 選擇器＋鎖定＋互斥、token wire＋contextvar、**connector 供應層抽象＋in-code 模擬版（先行，整條管線靠它開發與 CI）**、寬鬆落表＋snapshot＋實驗觀測點（§4-2 三訊號可量測化）、退貨整形＋上限、replay manifest 記錄（為 Phase 2 存料）、prompt 段＋connector 劇本 staging（載入與引導 land_as 的通用說明；per-connector 劇本由 internal 供）、**MCP 版 adapter（含 per-user auth spike，與主線並行、不阻塞）**。
**Phase 2（publish/重放）**：publish 凍結、分享 link、viewer 開啟流程、零 LLM replay＋分級驗證 ①②③④（② 由 server 可行動錯誤承重）、viewer 改選互動與更細分享控制＝Phase 2+。

**風險與前置 spike**：
1. **P1｜MCP client 的 per-request header 注入**（已由 stateless 契約大幅降級）：server 端 stateless 化讓 per-user auth 成為每請求自帶 header；殘餘 spike＝驗證 client SDK（官方 python SDK／langchain-mcp-adapters）對 stateless server 的逐請求 header 注入 ergonomics，與模擬版並行、不阻塞。
2. internal 寫 MCP server 的意願/能量與網段可達性——契約規範（§4）隨 spec 先交付對齊。
3. internal 端模型版本未確認（dev＝deepseek-v4-flash）；本設計不倚賴模型升級（確定性結構照舊），故不阻塞。

## 10. 與舊資產的關係

概念挖礦對照：#62→驗證階梯理念（退貨可行動）、snapshot/remount、caps；#63→recipe/零 LLM replay/語意化錯誤分級；#65→只注入選定組、session-lock 首訊定案。**三支舊 PR 於本案 Phase 1 開工時關閉**（內容不再 rebase）。csv/connector 互斥為新增規則（舊設計允許混用）。
