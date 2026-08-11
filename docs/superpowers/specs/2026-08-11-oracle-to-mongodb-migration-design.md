# Oracle → MongoDB 遷移 — Design

日期：2026-08-11（更新 2026-08-12：改採 standalone 主線 + transaction 疊加分支）
狀態：已與使用者討論定案（待 spec review）
分支：`feat/oracle-to-mongodb`（自 master／Oracle 基線開；**此為 standalone 主線**）

## 背景與動機

**internal 基盤強制**：部署環境只提供 MongoDB（K8s StatefulSet），不再提供關聯式 DB。因此 backend 資料層由 Oracle（Spring Data JPA + Flyway）整組換成 MongoDB。

**交易能力不確定 → 分兩條分支**：internal 的 Mongo 雖以 replica set 部署（`db.hello().setName` 有值、理論上多文件交易可用），但**是否穩定可用尚未在 internal 實環境驗證**。為避免把整個遷移賭在交易上，採**兩分支策略**：

1. **`feat/oracle-to-mongodb`（standalone 主線）**：四 collection、**不依賴多文件交易**，多文件原子性缺口以應用層手段處理（見〈無交易造成的問題與解法〉）。可先在 standalone Mongo 上跑起來。
2. **`feat/oracle-to-mongodb-txn`（transaction 疊加分支，基於主線）**：在主線之上加 `MongoTransactionManager` + `@Transactional` 包裹、切換測試為單成員 replica set。**確認 internal replica set 交易穩定後再合併**。

**建模在兩分支間完全相同**（四 collection、`message.artifactId` 方向不變），故 transaction 分支相對主線是**最小 delta**（加交易管理、去掉主線的補償/reaper），合併乾淨。

這推翻了 `docs/architecture.md`「為什麼選 relational DB」——前提已變成「只有 Mongo」。該節改寫為「被 internal 基盤強制換 Mongo，以及如何在 document store（先無交易、後可加交易）上保住原有不變量」。

### 與其他分支的關係

- 從 **master（Oracle 基線）** 開。
- **取代** MariaDB 遷移（`feat/mariadb-uuidv7-logging` / PR #41，不 merge、廢棄）。UUIDv7 那組與本案無關，不納入。

## 範圍

backend 資料層：pom 依賴、entity、repository、多文件寫入的原子性處理、索引/schema 管理、組態、compose infra、測試底層、文件。

### 非範圍

- **資料遷移**：不做（dev 資料可拋、internal 未上線）。
- 前端、deepagent-service、DTO 契約：不動。
- 儲存層（FileStorage／MinIO/S3）：不動——大 payload 本就存物件儲存、DB 只留 storage key。

## 資料建模

### 四個 collection 對映現有四張表，NOT 嵌入

`chat_session`、`chat_message`、`uploaded_file`、`artifact` 各自一個 collection。**不嵌入**：嵌入會讓單一文件隨版本鏈無上限成長、撞 16MB／單文件上限（`architecture.md` 既有論證）。分開 collection 時，新增一版 = insert 新文件，成長的是 collection 筆數（本該無上限、靠索引查），永遠碰不到單文件上限。

**保留 `message.artifactId` 方向不翻**（不採 `artifact.messageId`）：翻方向雖能讓 standalone 的失敗態變合法，但那是為暫時階段做永久建模改動，且需補無聲失敗提示；本案 standalone 缺口改用 reaper/補償處理（見下），建模與現況一致、也讓 transaction 分支的 delta 最小。

### `_id` 與參照

- `_id` 沿用 **String UUID**（36 字元），保留現有 id 形狀與 `ChatSession` 的 client 指定 id。id 由應用層產生（Spring Data Mongo 對 null String `_id` 會塞 ObjectId hex，故 MUST 建立時明確賦 UUID）。
- 跨實體用**純 String 參照欄位**（`sessionId`、`artifactId`），無 DB 強制外鍵（與現況一致）。

### 存取模式：熱路徑無 join

| 存取 | 查法 |
|---|---|
| 按 user 撈 session 列表 | `chat_session.find({userId}).sort(updatedAt)` |
| 撈 session 訊息 | `chat_message.find({sessionId}).sort(createdAt)` |
| 撈 session artifact 版本鏈 | `artifact.find({sessionId}).sort(createdAt)` |
| 撈 session 上傳檔 | `uploaded_file.find({sessionId})` |
| ownership | `chat_session.find({_id, userId})` |
| serve artifact HTML | `artifact.findById` 拿 storageKey+assetProfile（metadata），再由 **FileStorage** 讀位元組、CDN 改寫、串流 |

唯一「像 join」的是組 session 完整畫面（約 4 個 `findBySessionId`，固定少數、走索引、非 N+1，現況本就分開查）。**serve HTML 的內容是 storage 的事，DB 只解 metadata**。

## 無交易造成的問題與解法（standalone 主線的核心）

多文件寫入沒有交易時**只有跨多文件的原子性會喪失**；**單文件寫入永遠原子**（Mongo 鐵律，standalone 亦然）。全 backend 只有三處是多文件寫入，逐一列問題與解法。共通前提：**storage 副作用（MinIO/S3）永遠不在 Mongo 交易內**（連 transaction 分支也是），storage 孤兒兩分支都靠 `RetentionCleanupService` 清，非交易專屬問題。

### 問題 1：`AgentConversationWriter.persistHtmlResult`（artifact + AI 訊息 + storage）

- **多文件**：insert artifact → store HTML → insert AI 訊息（`message.artifactId` 指向 artifact）。
- **無交易的問題**：若 AI 訊息在 artifact 已寫入後失敗 → **孤兒 artifact**（帶 sessionId），會**混進版本下拉選單**（`artifact.find({sessionId})` 查得到）顯示壞版本。
- **standalone 解法（採用）**：寫入順序維持「artifact 先、訊息後」（同現況）；加一個**輕量 reaper**（排程掃「id 未被任何 `message.artifactId` 引用」的 artifact 並刪除，順帶刪其 storage 物件）。reaper 是**加法、可移除**——transaction 分支合併後直接刪掉。早期若可容忍，也能先只靠 reaper 定期清、不即時處理。
- **transaction 分支解法**：`@Transactional` 包住 → artifact+訊息原子，孤兒不可能發生，**reaper 移除**。
- **已評估但不採的替代**（記錄備查）：
  - *embed artifact 進訊息*（塌成單文件、standalone 也原子）：否決——artifact by-id 查詢全變巢狀（見〈已否決〉），retention 掃更大的 message collection，清 key 還整則訊息重寫。
  - *翻方向 `artifact.messageId` + 訊息先寫*（partial 態變合法）：否決——為暫時階段做永久建模改動、只救此對、且失敗變無聲需另補提示。

### 問題 2：`ArtifactRepairService`（artifact 原地更新 + repair 訊息 + storage）

- **多文件**：`findById` 載入既有 artifact → 更新 storage key 後 `save`（**原地更新、非新 artifact**）；另 insert 一則 repair 記錄訊息（`artifactId = null`，不與 artifact 關聯）。
- **無交易的問題**：兩者皆可能半成。但 artifact 更新是**單文件（原子）**；repair 訊息是**單文件 insert（原子）**。真正的跨文件缺口是「artifact 更新成功、repair 訊息失敗」→ **修好的 artifact 存在、只是少一則 log 訊息**。**危害低**（artifact 有效可用，缺 log 純屬紀錄）。
- **standalone 解法（採用）**：**容忍**——修好的 artifact 是合法狀態，缺一則 repair log 不影響功能或版本鏈。
- **transaction 分支解法**：`@Transactional` 包住，一併原子。

### 問題 3：`FileService.upload`（N 個 uploaded_file 批次 + storage）

- **多文件**：一批 ≤5 個檔案，各自 store 物件 + `save` 文件。現況用 `TransactionTemplate` 包住批次 `save`，且 catch 區已對 storage 做 cleanup。
- **無交易的問題**：批次中途失敗 → **半批**（部分檔案文件已寫）。
- **standalone 解法（採用）**：**逐檔 insert（各自原子）+ 補償**——沿用現有 catch 區的 storage cleanup，並補上「刪掉本批已寫入的 file 文件」。檔案彼此獨立、無「訊息一定配 artifact」那種強不變量，半批容忍度高（使用者可重傳）。
- **transaction 分支解法**：`@Transactional` 包住批次 `save` → 全有全無，補償移除。

### 兩分支的差異總表

| | standalone 主線 | transaction 疊加分支 |
|---|---|---|
| `@Transactional`/`TransactionTemplate` in code | **保留**（程式碼寫成交易形狀，讓分支 delta 最小） | 保留 |
| transaction manager bean | **no-op / resourceless**（`@Transactional` 不報錯、但也不真的包交易；各寫入 auto-commit）——NEVER 用 `MongoTransactionManager`（standalone 連線會拋錯） | 換 `MongoTransactionManager`（多文件真原子）|
| 問題 1 | reaper 清孤兒 artifact | 原生原子，**刪 reaper** |
| 問題 2 | 容忍（危害低） | 原生原子 |
| 問題 3 | 逐檔 + 補償 | 原生原子，**刪補償** |
| 測試 Mongo | standalone flapdoodle | 單成員 replica set flapdoodle |
| 合併時機 | — | 確認 internal replica set 交易穩定後 |

## 持久層改寫

### 框架與 entity

- **Spring Data JPA → Spring Data MongoDB**（`spring-boot-starter-data-mongodb`；移除 `spring-boot-starter-data-jpa`、`ojdbc11`、`flyway-*`、`h2`）。
- Entity：`@Entity`/`@Table` → `@Document(collection=...)`；`@Id` 保留（String）；`@Column` 移除。JPA Auditing → Mongo Auditing（`@EnableMongoAuditing` + 同名 annotation）。
- `ChatSession` **維持 `Persistable<String>`**（Spring Data Mongo 同以 `isNew()` 決定 insert vs replace）——client 指定 id upsert 與「建立時先 `setId()`」不變。
- `@Version` optimistic locking 保留。Lombok 用法不變。

### Repository 與 Mapper

- `JpaRepository` → `MongoRepository`；derived query（`findByUserIdOrderByUpdatedAtDesc`、`findBySessionId...`）多數原樣可用；無法 derive 用 `@Query`（Mongo JSON）。`Pageable`/`Page<T>` 保留。
- **MapStruct mapper 不動**。
- `ArtifactRepository` 的 `@Modifying @Query`（`clearHtmlStorageKey`/`clearRawHtmlStorageKey`）→ Mongo 用 `@Query` + `MongoTemplate.updateFirst($set)`。

### 索引/schema 管理

- **丟掉 Flyway**。索引改**啟動時建立**（顯式 `ApplicationRunner` 用 `MongoTemplate.indexOps()`；不用 auto-index-creation，預設關且生產不建議）：
  - `chat_session`：`(userId, updatedAt)`；`updatedAt`（retention 掃描 `findByUpdatedAtBefore`）
  - `chat_message`：`(sessionId, createdAt)`
  - `uploaded_file`：`(sessionId, expired)`；`(sessionId, alias)` **unique**（對映 `uq_uploaded_file_alias`）
  - `artifact`：`(sessionId, createdAt)`（`findFirstBySessionIdOrderByCreatedAtDesc`/count/版本鏈）；`createdAt`（retention `findStaleArtifactStorageKeys`）
- **unique index 的 race**：`SessionGuard` 現以 `DataIntegrityViolationException` 接 session upsert 競態 → Mongo 對應 **duplicate key `E11000`**（`DuplicateKeyException`），catch 型別需調整。

### 組態

- 移除 `spring.datasource.*`、`spring.jpa.*`、`spring.flyway.*`、H2 console。
- 新增 `spring.data.mongodb.uri`（env `SPRING_DATA_MONGODB_URI`）。standalone 主線：`mongodb://host:27017/cowork`；transaction 分支：`?replicaSet=rs0`。
- Actuator `MongoHealthIndicator` 自動生效。

## 測試策略

**flapdoodle 嵌入式 mongod**（`de.flapdoodle.embed.mongo`）——CI 無 Docker，不能用 Testcontainers；flapdoodle 不需 Docker、最貼近現行 H2 的零 Docker 迴圈。

- **standalone 主線**：flapdoodle 跑**純 standalone**（**不需 `rs.initiate()`**，設定較簡單）。
- **transaction 分支**：flapdoodle 切**單成員 replica set**（`@Transactional` 測試才過）。
- Spring Boot 3.4 已移除內建 embedded-mongo auto-config → **手動接線**（起 `MongodExecutable`，注入 uri），封成測試基底。
- **⚠️ 硬前提**：flapdoodle 首次下載對應版本 mongod binary；air-gapped internal CI 連不出去，MUST 先放進**內部 mirror**（或預塞 CI image）。實作前 MUST 確認可取得。
- 既有 ~600 測試：repository/service 層改對嵌入 Mongo 跑；`@WebMvcTest` + `@MockitoBean` controller slice **不受影響**。斷言 SQL/JPA 特定行為者需改寫。**主要工時。**
- standalone 主線需**額外測試**：reaper（孤兒 artifact 清理）、`FileService.upload` 半批補償、問題 2 的容忍行為。

## compose / infra / 文件

- `docker-compose.infra.yml`：`oracle` → `mongo`。**standalone 主線**：單一 mongo 容器即可（無 `rs.initiate()`）；**transaction 分支**：init 容器 `rs.initiate()` 成單成員 replica set。volume 換名、cloudbeaver `depends_on` 改。
- `docker-compose.app.yml`：`SPRING_DATASOURCE_URL` → `SPRING_DATA_MONGODB_URI`（主線無 `replicaSet`，txn 分支加）。
- README、`docs/architecture.md`（「為什麼選 relational」改寫、ER 圖標註、H2 敘述）、CLAUDE.md（DB 段落、Entity ID/JPA 規則）。歷史 plan/spec 不回改。

## 風險與前提

| 項目 | 說明 / 對策 |
|---|---|
| **~600 測試 JPA→Mongo 改寫** | 本案主要工時 |
| **flapdoodle mongod binary（air-gapped）** | 硬前提；實作前 MUST 確認內部 mirror；拿不到則測試策略重議 |
| **standalone 期的多文件缺口** | 依〈無交易造成的問題與解法〉處理：問題1 reaper、問題2 容忍、問題3 補償 |
| **internal replica set 交易穩定性未驗** | 正是分兩支的原因；txn 分支合併前 MUST 在 internal e2e 驗過交易可用且穩定 |
| **`SessionGuard` upsert 例外型別** | `DataIntegrityViolationException` → Mongo `DuplicateKeyException`（E11000） |
| **FK 完整性喪失** | 改應用層維護；`artifactId` 本無 FK，影響有限 |

## 已否決的替代方案

- **嵌入建模**（訊息/版本鏈 embed 進 session）：撞 16MB 單文件成長。
- **翻方向 `artifact.messageId`**：為暫時階段做永久建模改動、只救 artifact 對、失敗變無聲需另補提示——standalone 缺口改用 reaper/補償，建模保持與現況一致。
- **Testcontainers 測試**：最貼近生產，但 CI **無 Docker** → 出局。
- **`bwaldvogel/mongo-java-server`**（純 Java in-memory）：功能覆蓋不完整、交易支援弱 → 不採。
