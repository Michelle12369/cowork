# MariaDB 切換 + UUIDv7 PK + deepagent-service log 強化 — Design

日期：2026-08-10
狀態：已與使用者討論定案

## 背景與目標

部署目標 DB 由 Oracle 改為 MariaDB。MariaDB/InnoDB 的 PK 是 clustered index（B+tree），現行隨機 UUID(v4) PK 會使插入點分散於整棵樹——page split、buffer pool 命中率差、索引碎片化。三個目標：

1. **DB 全面切換 MariaDB**：不保留 Oracle 相容、不做資料遷移（dev 資料可拋棄，internal 尚未上線）。
2. **PK 改 UUIDv7**：時間有序（前 48 bits 為毫秒 timestamp），插入落點集中於 B+tree 最右側熱 page，行為等同 AUTO_INCREMENT 追加寫入；schema 長度（36 字元字串）、FK、API、DTO 全部不動。
3. **deepagent-service log 強化**：集中式設定、每行自動帶 sessionId、`LOG_LEVEL` 可切 DEBUG，補齊 graph 節點／LLM 呼叫／SSE 事件三類關鍵路徑。

### 已否決的替代方案

- **BIGINT AUTO_INCREMENT 內部 PK＋UUID 對外唯一鍵**：B+tree 理論最優（8 bytes 鍵寬），但對外位址仍須 UUID（ChatSession 為 client 指定 id、API 不可暴露流水號），熱路徑每次都要先走 UUID 二級索引換內部 id 並未變快；且需重接全部 FK／entity／repository、重做 ChatSession upsert 設計。改動面與收益不成比例。
- **自製 prefix 字串**：效果同 v7 但非標準、長度改變、工具鏈不認得。
- **MariaDB 10.7+ 原生 `UUID` 型別**：其內部位元組重排是為 UUIDv1 的時間戳位置設計，套在 v7 上反而打亂時間排序。PK 維持字串。

## 範圍

- backend（pom、Flyway V1、entity id generator、H2 測試 mode、寫入護欄）
- frontend（session id 改產 v7）
- deepagent-service（logging）
- compose 兩個 stack、README、`docs/architecture.md`、CLAUDE.md

### 非範圍

- 資料遷移（明確不做）
- 歷史 plan／spec 文件不回改（史料）
- 實驗分支 `exp/custom-chart-only` 不動
- 前端匿名 user id（`apiClient.ts` 的 localStorage UUID）維持 v4——它是 `user_id` 欄位非 PK，無 clustered index 問題

## A. Oracle → MariaDB

### A1. 依賴（backend/pom.xml）

- 移除：`com.oracle.database.jdbc:ojdbc11`（或現行 artifact）、`org.flywaydb:flyway-database-oracle`
- 加入：`org.mariadb.jdbc:mariadb-java-client`（runtime）、`org.flywaydb:flyway-mysql`

### A2. Schema（V1__init.sql 直接重寫）

型別對映（internal DBA 規範：TEXT 家族僅可用 `TEXT`）：

| Oracle | MariaDB | 附註 |
|---|---|---|
| `VARCHAR2(n)` | `VARCHAR(n)` | utf8mb4 為字元語意；現行 UTF-8 byte 計長驗證保守安全，不動 |
| `CLOB` | `TEXT` | **上限 64KB bytes**，寫入路徑加護欄（見 A3） |
| `NUMBER(19)` | `BIGINT` | |
| `NUMBER(1)` | `TINYINT` | expired 布林 |
| `TIMESTAMP` | `DATETIME(6)` | 避開 TIMESTAMP 的 2038 與時區隱式轉換 |

- 表定義加 `ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`（若 H2 MariaDB mode 不吃 table options，退為僅真 MariaDB 需要時的 vendor 目錄方案，見「風險」）。
- 索引、FK、unique 約束維持原設計不變。
- artifact HTML 本即存 object storage（DB 只留 storage_key），不受 TEXT 上限影響。

### A3. TEXT 64KB 寫入護欄

utf8mb4 中文一字最多 4 bytes，64KB 實際約 1.6 萬中文字。受影響欄位與風險：`chat_message.text`（低）、`steps_json`／`questions_json`（極低）、`uploaded_file.metadata_json`（中——寬表摘要可達數十 KB）。

- 集中一個 truncation 工具（依 UTF-8 byte 長度截斷、不切斷 surrogate pair），在寫入 `text`／`metadata_json` 前套用，超限時截斷並 log warning（含 sessionId 與原始長度），**不讓 DB 層錯誤打斷 turn**。
- `metadata_json` 截斷會破壞 JSON——其生成端（metadata 摘要）本已有樣本列上限；護欄採「超限即降級重生成較小摘要或捨棄非必要區塊」優先、硬截斷為最後手段，實作計畫中細化。

### A4. 組態與 compose

- `application.properties`／`application-local.properties`：H2 URL `MODE=Oracle` → `MODE=MariaDB`；容器內預設 datasource URL 改 `jdbc:mariadb://mariadb:3306/cowork`（DB 名沿用現行 APP_USER 命名 `cowork`）。
- `docker-compose.infra.yml`：`oracle`（gvenzl/oracle-free）服務整組換 `mariadb:11.4`（LTS；internal 版本不同時僅換 image tag），env 用 `MARIADB_DATABASE`／`MARIADB_USER`／`MARIADB_PASSWORD`／`MARIADB_ROOT_PASSWORD`，healthcheck 用官方 `healthcheck.sh --connect --innodb_initialized`；volume 換名（`mariadb-data`）；cloudbeaver `depends_on` 同步改。
- `docker-compose.app.yml`：`SPRING_DATASOURCE_URL` 預設值換 MariaDB；「Oracle 首次啟動 2–4 分鐘靠 restart 重試」的註解改寫（MariaDB 秒級就緒，restart 機制保留作一般韌性）。
- `.env.docker`（gitignored）所需變數改名同步寫進 README。

### A5. 文件

- README：H2「Oracle 相容模式」→「MariaDB 相容模式」、infra 表格、完整環境敘述。
- `docs/architecture.md`：mermaid 圖的 Oracle 節點、ER 圖 `VARCHAR2_*`／`CLOB` 型別標註、「Oracle BYTE 語意說明」一節改寫為 MariaDB 字元語意＋byte 計長仍保守安全的說明、H2 mode 敘述。
- CLAUDE.md：專案脈絡與 Entity ID 規則（見 B4）。

## B. PK 改 UUIDv7

### B1. 後端 generator

Hibernate 6.6（Spring Boot 3.4.1）僅內建 `AUTO/RANDOM/TIME` 三種 style、無 v7，且 `TIME`（CustomVersionOneStrategy）的字串序非時間序——故自訂：

- 加依賴 `com.fasterxml.uuid:java-uuid-generator`（JUG 5.x）：v7 產生交給 `Generators.timeBasedEpochGenerator()`——48-bit 毫秒 timestamp、同毫秒單調遞增內建，演算法不自寫。
- `com.erd.cowork.domain.id.UuidV7`：meta-annotation（`@IdGeneratorType(UuidV7Generator.class)`）。
- `com.erd.cowork.domain.id.UuidV7Generator`：實作 `BeforeExecutionGenerator` 的薄 wrapper（十餘行），呼叫 JUG 後回傳 36 字元字串。
- 單元測試：wrapper 輸出格式（version=7、36 字元）、連續產生時間有序。

### B2. Entity 切換

`ChatMessage`／`UploadedFile`／`Artifact`：`@UuidGenerator` → `@UuidV7`。`AgentConversationWriter` 的「先 save 取得 id 再存 HTML」流程不變（generator 仍在 persist 時賦值），僅 Javadoc 提及處同步改字。

### B3. 前端（ChatSession id）

`ChatSession` 為 client 指定 id（upsert 設計，不動）。改變 id 的產生方式：

- 加依賴 `uuid`（v10+，原生支援 v7、內建單調計數器、含 TS 型別）。
- `CoworkPage.tsx` 的 `crypto.randomUUID()` → `import { v7 as uuidv7 } from 'uuid'`。
- 既有測試若 mock `crypto.randomUUID` 需同步調整；行為測試不變。

既存 v4 session id 與新 v7 id 並存無礙（同為 36 字元字串，僅排序特性不同）。

### B4. 規則同步

CLAUDE.md General 規則「Entity ID 用 Hibernate `@UuidGenerator`（String）」改為「Entity ID 用專案自訂 `@UuidV7`（String，時間有序 UUIDv7；MariaDB clustered index 友善）」，ChatSession 例外敘述不變。

## C. deepagent-service log 強化

### C1. 集中式設定（stdlib best practice）

- 新增 `app/logging_config.py`：`configure_logging(settings)`，於 `main.py` module 載入時（uvicorn worker 啟動即生效）呼叫一次——**只在應用進入點設定**，其餘模組一律只 `logging.getLogger(__name__)`，不做任何 handler/level 設定。
- 設定方式用 **`logging.config.dictConfig`**（宣告式，Python 官方建議），非散落的 `basicConfig`/手動 addHandler：formatter、filter、handler、logger 階層一次定義。
- 格式：`%(asctime)s %(levelname)s [%(name)s] [session=%(session_id)s] %(message)s`，`asctime` 用 ISO-8601（`datefmt`）。
- sessionId 注入：`contextvars.ContextVar`＋`logging.Filter`（無值時顯示 `-`），`/chat`、`/repair` 進入點 set；async 流程中 contextvar 自動隨 task 傳播，所有既有與新增 log 行自動帶上，不逐處手寫。
- **uvicorn logger 一併納入 dictConfig**（`uvicorn`、`uvicorn.error`、`uvicorn.access`）：統一格式、`propagate` 明確設定，杜絕雙重輸出。
- `LOG_LEVEL` env var（進 `Settings`，預設 `INFO`）。
- 慣例（實作與 review 依此檢查）：`%` lazy formatting（不用 f-string 進 log 呼叫）；例外一律 `logger.exception(...)`／`exc_info` 保留 stack trace，NEVER 吞掉；NEVER log 完整 prompt／HTML／使用者資料內容／secrets（與後端日誌規範一致），DEBUG 級僅記長度與摘要。

### C2. 涵蓋範圍（三類）

1. **graph 節點**：gather／synthesize 等節點進出（INFO：節點名、耗時）、重試與補救迴路觸發（WARNING：原因摘要）、ask_user 反問觸發。
2. **LLM 呼叫**：每次 model call 的耗時、重試次數、token 概況（有資料時）；失敗含錯誤類別。
3. **SSE 事件摘要**：turn 結束（finalize）時各事件型別計數；ERROR 事件記完整內容。

### C3. 既有行為

既有 log 行不刪改語意（僅自動多帶 session 欄位）；dozzle 純文字直接可讀。

## 測試策略

- 後端：generator 單元測試（B1）＋truncation 工具單元測試＋既有 `./mvnw test` 全綠（H2 MariaDB mode 下全套跑過即為 schema 相容性驗證）。
- 前端：uuidv7 util 使用點的既有行為測試調整後全綠。
- Python：logging filter／contextvar 單元測試（session id 注入、無 context 時 fallback）、關鍵 log 點以 `caplog` 斷言；既有 pytest 全綠。
- Compose 驗證：infra＋app 起 MariaDB 實跑 Flyway V1 成功、健康檢查通過（實作計畫納入人工驗證步驟）。

## 風險與退路

| 風險 | 對策 |
|---|---|
| H2 MariaDB mode 不吃部分 MariaDB DDL（table options、`DATETIME(6)` 等） | 首選：調整為兩者共通子集。退路：Flyway vendor 目錄（`db/migration/{vendor}`）分 H2／MariaDB 兩份 V1 |
| `TEXT` 64KB 截斷破壞 JSON 欄位 | A3 護欄：生成端降級優先、硬截斷＋warning 為最後手段 |
| internal MariaDB 版本與 `mariadb:11.4` 不一致 | 僅 image tag／JDBC URL 差異，一行可調；DDL 不用 10.11 之後才有的語法 |
| v7 同毫秒排序／溢位處理 | 交給 JUG `timeBasedEpochGenerator()`（內建單調遞增），不自寫演算法 |
