# Project Rules

全部程式碼 MUST 遵守以下規則。撰寫 Backend Java 程式前，先讀 `.claude/skills/java/SKILL.md`（收合後的 Java skill；依任務讀對應 `references/*.md`——spring-boot、spring-web、spring-data、jpa-patterns、spring-testing、code-quality、design-patterns）；
撰寫 Frontend 程式前，先讀 `.claude/skills/frontend-dev-guidelines/SKILL.md`；
撰寫 Python/FastAPI（`deepagent-service/`）程式前，先讀 `.claude/skills/fastapi/SKILL.md`（FastAPI 官方 skill；SSE 用 `fastapi.sse.EventSourceResponse`、參數/依賴一律 `Annotated`，其餘見該 skill 與其 `references/`）。

## 專案脈絡

- **產品**：Cowork · Data Studio——使用者上傳 CSV/Excel + prompt，agent 產出 self-contained HTML dashboard（Tailwind + ECharts CDN）。UI 完整還原 `docs/mockup/eRDWorkspaceonline.html` 的 Cowork tab（僅此畫面）。
- **Artifact 契約**：所有 provider 產出的 HTML 從 `window.__ERD_DATA__[alias]` 讀資料；後端 ArtifactAssembler 注入全量資料（抽樣機制已移除——目前資料量級不需要；LLM 只看 schema/統計摘要/樣本列不變）；統計計算在瀏覽器 JS。迭代修改時回餵前版 raw HTML + 最小變更指令；進度顯示由模型 `[[step:]]` 標記驅動。
- **LLM providers**（可插拔，`erd.agent.provider`＝`openai-compatible|langgraph-analysis`，統一回傳 AgentEvent 流：STEP/TOKEN/ANSWER/ARTIFACT/ERROR/QUESTION/THINKING）：OpenAICompatibleProvider（llm api 線，未設時預設；LLM 直寫 HTML，OpenAI-compatible `/v1/chat/completions` SSE——internal LLM 或 OpenRouter；internal 環境支援 auth-mode=token-exchange（j1→j2，TTL 快取，401 自動重試））、LangGraphAnalysisProvider（**analysis 主線**，本機 `.env` 預設切到這條：Java 橋接 `deepagent-service`（FastAPI + deepagents harness + `skills/dashboard/SKILL.md` + DuckDB in-process），模型用 get_schema/run_sql/preview_data 查資料、`write_file` 整份重寫 dashboard.html，服務端注入 `__ERD_RESULTS__`；瀏覽器修復走 `POST /repair`）。InternalCodegenProvider 已移除。
- **模型與 harness 前提（2026-09-11，repo 尚未反映）**：實際部署模型已換 **deepseek-v4-flash**（repo 內預設值仍是 `AGENT_MODEL=qwen3.6-35b`／`ERD_AGENT_OPENAI_COMPATIBLE_MODEL=gpt-oss-120b`，部署一律由 env 覆寫，程式碼與 prompt NEVER 寫死模型 id）。harness 設計前提：**模型只會更強**——NEVER 再為當前模型的弱點加補償性結構（gpt-oss 時期的 declarative spec＋確定性 renderer 已退場），護欄只留安全與契約（`__ERD_RESULTS__` 注入契約、SSO token 紅線、DuckDB 鎖門、engine 純度），能交給模型判斷的交給模型與 skills，換更強模型時應零改動受益。**Sandbox backend 即將到來**（agent 工具執行面改在隔離沙箱；細節未定，新功能設計時把工具執行面當可替換接縫，NEVER 把 in-process DuckDB 假設散進 prompt 契約以外的地方）。**VLM 可行但未評估**（例如模型看 dashboard 截圖自檢；尚無實驗數據，不得當作設計前提）。
- **Multi-user**：一律 `X-User-Id` header（v1 前端 localStorage 匿名 UUID 由 axios interceptor 附加；dev 線 `CurrentUserFilter` 吃 SSO 兩 header、缺席補 fallback 假值；internal 由接縫 C `InternalCurrentUserFilter` 注入）；所有 session 查詢按 userId 過濾，存取他人資源一律 404（artifact 讀取含 ownership 檢查）。
- **MCP datasource（connector 線，PR #78，在 `feat/9E`）**：使用者可改選一或多個 API 資料源取代上傳檔（互斥，雙向 409；第一句話後鎖定）。每個資料源是一台 internal 自寫的 MCP server（FastMCP 包 OpenAPI，作者手冊 `docs/superpowers/specs/2026-09-02-mcp-server-howto.md`）；Java `connector_catalog`（Mongo，mongosh 手動塞）→ `GET /api/connectors` → 每輪把 connectors 與 SSO 兩 header 帶給 deepagent；deepagent 以 fastmcp stateless client 取 tools／下載 skills（自動加 `{connectorId}` 前綴），每次 tool 呼叫成功即自動落 DuckDB 表（表名＝connector_tool_參數hash），表只活本輪、原始資料不進對話；`CONNECTOR_CALL_BUDGET`/`CONNECTOR_REQUEST_TIMEOUT_SECONDS`/`CONNECTOR_CALL_RETRIES`/`CONNECTOR_BEARER_TOKENS` 可設。**SSO token NEVER 進 log/prompt/落盤**。
- **檔案**：限 5 檔/session 共 5GB（CSV 單檔 2GB 串流解析、xlsx 上限 200MB）；xlsx 原樣直存（可為密文），解密與轉 CSV 在 deepagent-service（`upload_decrypt.py` 接縫由 internal 整檔複寫、`xlsx_to_csv.py`），llm api 線因此退化為 csv-only（accepted trade-off）；儲存雙路線（`erd.storage.type`／`STORAGE_BACKEND`＝`local|s3`；s3 走 write-once generation 快照，本機/compose 用 MinIO，internal 現行路線）；分級保留（artifact HTML 2 年、workspace 與上傳原始檔依 session 最後活動 180 天，皆可用 `ERD_STORAGE_RETENTION_*` 調整，並有 `ERD_STORAGE_CLEANUP_DRY_RUN`）。
- **關鍵文件**：架構 `docs/architecture.md`（權威現況說明）、internal 同步 `docs/internal-sync.md`、internal 接縫 `docs/internal-implementation-guide.md`、原始 spec `docs/superpowers/specs/2026-07-05-cowork-data-studio-design.md`、deepagent spec `2026-07-29-deepagent-dashboard-design.md`、MCP datasource spec `2026-08-30-mcp-datasource-design.md`、實作計畫 `docs/superpowers/plans/`、進度 ledger `.superpowers/sdd/progress.md`（gitignored，個人用）。
- **狀態（2026-09-11）**：master＝`fe3eb2c`（PR #80 sync-upstream 旗標化）。7/29 之後已進 master：Oracle→MongoDB（單成員 replica set＋交易，8/12）、deepagent-service 成為 analysis 主線（舊 LangGraph 手建 StateGraph／declarative renderer 路線與 `exp/custom-chart-only` 實驗已被取代，不再接續）、local/s3 雙路線儲存＋分級保留、workspace zip 快照、artifact CSP＋srcdoc 認證交付（8/25）、xlsx 密文直存＋deepagent 轉檔解密（8/26）、agent API bearer、internal 三接縫＋`scripts/sync-upstream.sh`（`--official <gl/ref> <GitHub sha>`／`--test gl/<ref>`，三道守門）。**整合分支 `feat/9E`**（HEAD `4e21991`＝master＋PR #78 MCP datasource）是 internal 同步錨點：NEVER rebase/force-push，進 master 用 merge commit 不 squash。開中 PR：#84 skills 每輪重掃（base `feat/9E`）、#83 `POST /tool-call`＋`mcp()` runtime prelude（base `feat/mcp-dashboard`；分享頁由 HTML 經 postMessage 打 MCP 的方向，spec `2026-09-02-replay-interactive-options.md` B/C 案尚未定案）。三側測試（PR #78 驗證）backend 658／前端 346／deepagent 426 綠，ruff 乾淨。**待辦積壓**：Java/前端 TABLE 事件型別清理（已無生產者）、connector 平行載入或載入前先發 STEP（避免 Java 180s 事件間逾時）、落表暫存檔 temp+rename、跨台 MCP skill 依賴宣告、三個 TOCTOU 以 `@Version` 收斂（Phase 2）、分享頁重放契約定案後重寫三份 MCP spec。前端 :3000、backend :${BACKEND_PORT:-8080}、deepagent :8000。
- **開發流程**：subagent-driven——implementer/task reviewer 用 sonnet、整支 branch 最終審查用 opus；主迴圈（任何模型）只負責規劃、架構與驗收，不寫 code（小型直接修正除外）。ledger `.superpowers/sdd/progress.md` 為跨 session 恢復地圖，任務完成即記帳。
- **多人協作**（多條 session/多人同時開發時）：每人一條 branch（建議各自 worktree）；同一條 branch NEVER 同時有兩個 session 或兩個 implementer。合併一律走 PR（`gh pr create`），gate＝後端 `./mvnw test`＋前端測試全綠＋opus 全分支終審 Ready to merge，終審結論寫進 PR 描述。跨人進度追蹤用 plan 檔的 `- [ ]` checkbox（隨 branch commit）與 PR，NEVER 依賴他人的 `.superpowers/sdd/progress.md`（gitignored 個人恢復地圖，不共享）。分工以 spec/plan 為單位認領，plan 之間檔案不重疊；無法避免時以 PR 順序序列化、後者 rebase。

## General

- 變數/參數/lambda 參數 NEVER 用 1–2 字元名稱（`id` 等 domain 語彙除外）；一律描述性單詞（domain 語彙優先）；迴圈計數器用 `index`/`rowIndex`/`columnIndex` 等
- google-java-format（由 Claude hook 自動執行，勿手動改格式風格）
- Entity ID 用 Mongo `@Id`（String UUID）：null id 由 `PersistenceConfig` 的 `BeforeConvertCallback<T>` 在 save 前補 `UUID.randomUUID().toString()`（取代 JPA `@UuidGenerator`——Spring Data Mongo 對 null String `@Id` 預設賦 24 字元 ObjectId hex，不符 36 字元 UUID 契約，新 entity MUST 一併補這道掛鉤）；時間戳一律 Mongo Auditing（`@EnableMongoAuditing` + `@CreatedDate`/`@LastModifiedDate`，語意同 JPA Auditing）。例外：`ChatSession` 採 client 指定 id（session upsert 設計），無 generator、實作 `Persistable<String>`，建立時 MUST 先 `setId()`——理由見該 entity class Javadoc
- 多文件寫入的原子性**已採 Branch 3 交易方案（`feat/oracle-to-mongodb-txn`）**：`MongoTransactionManager` ＋ `@Transactional`/`TransactionTemplate`，全 backend 三處多文件寫入（`AgentConversationWriter.persistHtmlResult`、`ArtifactRepairService`、`FileService.upload` 批次）皆受交易保護。**standalone Mongo 不支援交易**——本機/測試/compose 皆 MUST 是單成員以上 replica set（`rs.initiate`），NEVER 對著 standalone Mongo 跑會觸發交易的路徑；曾評估的 standalone 補償方案（孤兒 reaper＋DB 補償）未採用，見 `feat/oracle-to-mongodb-compensation` 分支歷史
- Health 檢查用 Spring Boot Actuator，不自寫 health controller
- 前端 API 一律相對路徑 `/api`；api/hooks/utils 頂層維持；components 可依內聚分子資料夾（不做 features 分層）
- DTO 一律 Java record；例外統一走 `@RestControllerAdvice`
- Secrets NEVER 放入 `application.properties`；一律用 env vars
- 多行文字輸出（email、report）用 Velocity template（`.vm` 放 `src/main/resources/templates/`）；NEVER 用 String 拼接

## Backend (Java / Spring Boot)

- Java 17（internal 環境；NEVER 用 18+ API）

### 注入與結構

- 一律 constructor injection；NEVER 使用 `@Autowired` field injection
- 例外類與 `GlobalExceptionHandler` 一律放 `com.erd.cowork.exception` package
- 使用者身分用 `@RequestScope` 的 `CurrentUser` context bean（`com.erd.cowork.context` 獨立包；interceptor 自 `X-User-Id` 填入）注入 service；method 簽名 NEVER 傳 userId。**async/SSE 邊界前 MUST 先把 CurrentUser 值物件化**（request scope 不跨執行緒）。sessionId 屬資源位址，維持顯式參數
- 使用 `@RequiredArgsConstructor` 產生 constructor；不手寫 constructor boilerplate
- 分層順序：Controller → Service → Repository；不得跨層直接呼叫
- Config binding 用 `@ConfigurationProperties`；NEVER hardcode URL、credentials、環境值
- **類別命名分類法**（命名即契約，code review 強制）：

  | 後綴／位置 | 類別 | 結構要求 |
  |---|---|---|
  | `*Service` / `*Controller` / `*Provider` / `*Assembler` / `*Validator` / `*Rewriter` / `*Repairer` / `*Guard` / `*Repository` / `*Mapper` / `*Config` / `*Properties` / `*Handler` / `*Interceptor` / `*Writer` / `*Normalizer` | Spring bean | 有 Spring stereotype（`@Component`/`@Service`/`@RestController`/`@Repository`/`@Configuration`/`@ConfigurationProperties`/`@RestControllerAdvice`）或為 MapStruct `@Mapper` interface；絕不用 `new` 建立 |
  | `*Utils` | static utility | `final` class、僅 `private` 建構子（拋 `UnsupportedOperationException`）、全 `static` 方法、無實例欄位、無 Spring 註解 |
  | `*Helper` | per-use 有狀態 helper | 無 Spring 註解；有實例狀態；class Javadoc MUST 標記 `non-bean: instantiate per <context>.`；MUST 用 `new` 建立 |
  | `*Dto` | API record | `record`；位於 `..web.dto..` package |
  | `..parsing.model..` 內 | domain record | 全 `record`；無 Spring 註解 |
  | `*Exception` | 例外 | 位於 `..exception..` package |

### Lombok

- 使用 `@Slf4j`；NEVER 手寫 `private static final Logger log = LoggerFactory.getLogger(...)`
- Entity NEVER 用 `@Data`；改用 `@Getter` + `@Setter` + `@EqualsAndHashCode(of = "id")` + `@ToString(exclude = {lazy collections})`
- Immutable DTO/response 用 Lombok `@Value` 或 Java record；request 用 Java record + Bean Validation
- Entity 使用 `@Builder` 時 MUST 一併加 `@NoArgsConstructor` + `@AllArgsConstructor`

### MapStruct

- Entity↔DTO 轉換一律用 MapStruct `@Mapper(componentModel = "spring")`；NEVER 手寫 mapping 或用 ModelMapper
- Mapper MUST 加 `unmappedTargetPolicy = ReportingPolicy.ERROR`，防止欄位靜默遺失
- `toEntity()` 中 DB-managed 欄位（`id`、`createdAt` 等）MUST 加 `@Mapping(target = "...", ignore = true)`

### API 設計

- 每個 Controller class MUST 加 `@Tag`；每個 endpoint MUST 加 `@Operation` + `@ApiResponse`
- 每個 DTO 欄位 MUST 加 `@Schema(description, example)`
- 所有 `@RequestBody` MUST 加 `@Valid`；Controller class MUST 加 `@Validated`
- GET NEVER 改變狀態；POST 建立資源回傳 201；DELETE 回傳 204；資源不存在回傳 404；衝突回傳 409
- NEVER 在 API response 直接暴露 `@Document` entity；一律用 DTO
- 若引入 Spring Security，config MUST whitelist `/v3/api-docs/**`、`/swagger-ui/**`、`/actuator/health`（v1 無認證，不引入 Security）
- 每個專案 MUST 加入 `springdoc-openapi-starter-webmvc-ui`；NEVER 用已棄用的 Springfox

### Exception Handling

- NEVER 空的 catch block；NEVER 吞掉例外不處理
- 拋出新例外 MUST 包裝原始 cause：`throw new XxxException("msg", e)`
- 所有 IO 資源 MUST 用 try-with-resources；NEVER 手動 `.close()` 放在 finally

### 日誌規範

- 關鍵路徑 MUST 在 controller 進入點記 request 參數摘要（sessionId、長度、計數等）；NEVER log API key、完整 prompt/HTML、使用者資料內容

### Null Safety

- NEVER 做 chained call 而不做 null check；NEVER `Optional.get()` 不先確認 `isPresent()`
- Public API NEVER 回傳 `null`；改用 `Optional<T>` 或空 collection
- null/empty 檢查優先用 Spring `StringUtils` / `ObjectUtils` / `CollectionUtils`；NEVER 手寫 `x == null || x.isEmpty()` 鏈

### Transaction

- 多步驟寫入 MUST 加 `@Transactional`；讀取 service method 加 `@Transactional(readOnly = true)`——**Mongo 現況例外**：Mongo 純讀不需要交易保護（無跨文件一致性問題），`readOnly` 交易對 Mongo 是 no-op，`SessionService`/`ArtifactService` 等讀取 method 已全數移除 `@Transactional(readOnly = true)`；交易只用在下方「多文件寫入的原子性」列出的三處多文件寫入，NEVER 因為這條規則對純讀 method 誤加
- MongoDB 交易 MUST 搭配單成員以上 replica set（standalone 不支援交易，`MongoTransactionManager` 會直接失敗）；本機/測試/compose 皆已切 replica set，見下方「MongoDB / Database」
- 交易範圍內 NEVER 包慢 IO／遠端呼叫（Mongo 交易 server-side 存活上限約 60s，超時會被中止）：遠端 LLM 呼叫、全量資料組裝等 MUST 在進交易前先做完或留在 `transactionTemplate.execute` 之外，交易內只留需要原子性保護的快寫入——範例見 `ArtifactRepairService.repairFromBrowserErrors`（LLM 呼叫留在交易外）、`AgentConversationWriter.persistHtmlResult`（資料組裝在交易前完成）

### Design Patterns

- Observer pattern 用 Spring Events（`ApplicationEventPublisher` + `@EventListener`）；NEVER 用 raw Singleton 做全域狀態
- Runtime 多實作選擇用 Spring Map-based Factory（inject `List<BeanType>`，build `Map<String, BeanType>`）

## MongoDB / Database

- Entity↔collection 用 `@Document(collection = "...")`；跨 entity 參照一律純 String 欄位（`sessionId`、`artifactId` 等），無 DB 強制外鍵——ownership／關聯查詢靠索引直查，NEVER 假設有 join
- 需要關聯資料時分開查（`findBySessionId` 等 derived query），NEVER 在 loop 中對每筆結果再各自查一次關聯（N+1 同樣適用於 document store）
- 有並發修改需求的 Entity MUST 加 `@Version`（optimistic locking，Spring Data Mongo 原生支援）
- 大量結果查詢 MUST 用 `Pageable`/`Page<T>`；NEVER 無限制 `findAll()` 無分頁
- Schema 無 migration 工具（Mongo schema-less）：collection shape 由 entity class 本身權威定義；索引改由 `MongoIndexInitializer`（`@Component` + `@EventListener(ApplicationReadyEvent.class)`）以 `MongoTemplate.indexOps()` 顯式建立（不用 auto-index-creation），新增查詢模式前先確認對應索引已建
- 測試直連真實 Mongo（flapdoodle 嵌入式 mongod 四件套已於 2026-08-25 移除，internal 化）：`SPRING_DATA_MONGODB_URI=mongodb://localhost:27017/cowork-test ./mvnw test`（未設時吃 `application.properties` 預設 `mongodb://localhost:27017/cowork`）；該 Mongo MUST 是單成員 replica set（compose `mongo`＋`mongo-init`，或 `mongod --replSet rs0` 後 `rs.initiate()`），standalone 會讓交易路徑直接失敗；本機直跑（`./mvnw spring-boot:run`）同樣需另起真實 Mongo（見 README）

## Frontend (React / TypeScript)

### 版本

- React 鎖 **^18.x**（react/react-dom/@types 一律 18，不升 React 19）；antd **6.x**（原生支援 React 18，不需相容補丁）

### 元件規範

- 所有元件使用 `React.FC<Props>` + TypeScript props interface
- 元件結構順序：Props interface → Hooks → Handlers（useCallback） → Render → default export
- 獨立路由或重型第三方元件才用 `React.lazy(() => import('...'))`，並以 `<SuspenseLoader>` 包覆；單頁現況不強制
- NEVER 用 loading spinner early return（`if (isLoading) return <Spin />`）；一律用 `<SuspenseLoader>` 包圍內容

### 資料抓取

- 主要資料抓取 MUST 用 `useSuspenseQuery`；NEVER 用 `useQuery` + `isLoading` 模式做 loading 判斷
- API 呼叫集中在 `src/api/`，使用共用 `apiClient`（axios instance）；route 一律相對路徑 `/api/...`
- Mutation 失敗用 `onError` callback 處理；`useSuspenseQuery` 錯誤用 `<ErrorBoundary>` 接住

### 樣式

- 所有樣式用 Tailwind CSS utility classes；條件 class 組合建議 `cn()`（clsx/tailwind-merge）；引入前用模板字串亦可
- Class string 超過 100 行時抽成獨立 `.classes.ts`
- 使用者通知一律用 antd `message` 或 `App.useApp()`；NEVER 自訂 toast
- **字型 stack 常數**：`src/theme/fonts.ts` export `FONT_FAMILY`（`'Inter Variable', 'Noto Sans TC', -apple-system, 'PingFang TC', 'Microsoft JhengHei', sans-serif`）；三處落地：`index.css` body/root、Tailwind `@theme --font-sans`、antd ConfigProvider `theme.token.fontFamily`。NEVER hardcode font stack 字串，一律引用常數

### TypeScript

- 嚴格模式；NEVER 使用 `any` 型別
- Function MUST 有明確 return type
- Type-only import MUST 用 `import type { ... }`

### 效能

- 傳給子元件的 event handler MUST 用 `useCallback` 包裝
- 昂貴計算用 `useMemo`；昂貴元件用 `React.memo`
- 搜尋輸入 debounce 300–500ms
- `useEffect` MUST 回傳 cleanup function 避免 memory leak

### Import Aliases

- 使用 `@/` alias（指向 `src/`，定義於 vite.config + tsconfig）；不使用其他自訂 alias

## Testing

- Controller 測試用 `@WebMvcTest` + `@MockitoBean`（Spring Boot 3.4+）；NEVER 在 controller slice test 啟動完整 Spring context
- 測試方法命名格式：`methodName_condition_expectedBehavior`（例：`createUser_duplicateEmail_returns409`）
- 每個新功能 MUST 有對應單元測試；多步驟流程 MUST 有 integration test
- PR 合併前 MUST 確認 `./mvnw test` 全部通過
- 前端測試用 Vitest + React Testing Library；斷言元素級行為（NEVER snapshot-only）；fetch mock 用 `vi.stubGlobal`；每個互動元件 MUST 有行為測試
- **Backend Mongo 測試共享單一外部 DB**：所有 `@SpringBootTest`/`@DataMongoTest` context 都直連 `SPRING_DATA_MONGODB_URI` 指到的同一個 database，**無 per-test 清理**、可重跑不重建。斷言 MUST 按唯一 id/session scope 或 before/after delta 計數；NEVER 用全域絕對計數（如 `collection.count() == 1`）——會被其他測試留下的資料污染，跑到不確定的順序依賴失敗
