# Oracle → MongoDB 遷移 — Design

日期：2026-08-11
狀態：已與使用者討論定案（待 spec review）
分支：`feat/oracle-to-mongodb`（自 master／Oracle 基線開）

## 背景與動機

**internal 基盤強制**：部署環境只提供 MongoDB（K8s StatefulSet，已確認為 Mongo **replica set**——`db.hello().setName` 有值、多文件交易可用），不再提供關聯式 DB。因此 backend 資料層由 Oracle（Spring Data JPA + Flyway）整組換成 MongoDB。

這推翻了 `docs/architecture.md`「為什麼選 relational DB」的既有決策——但那份決策的前提是「兩者皆可用、選資料不變量最貼合的」；現在前提變成「只有 Mongo」，決策隨之改變。該節將改寫為「被 internal 基盤強制換 Mongo，以及如何在 document store 上保住原有不變量」。

### 與其他分支的關係

- 從 **master（Oracle 基線）** 開分支。
- **取代** MariaDB 遷移（`feat/mariadb-uuidv7-logging` / PR #41）——該分支不 merge、廢棄。故 UUIDv7 那組（B+tree clustered index 友善）與本案無關，不納入。

## 範圍

backend 資料層：pom 依賴、entity、repository、交易管理、索引/schema 管理、組態、compose infra、測試底層、文件。

### 非範圍

- **資料遷移**：不做（dev 資料可拋、internal 未上線，schema 翻新建即可）。
- 前端、deepagent-service：不動（DB 只在 backend）。
- 儲存層（FileStorage／MinIO/S3）：不動——artifact HTML、上傳原始檔本就存物件儲存、DB 只留 storage key。

## 資料建模

### 四個 collection 對映現有四張表，NOT 嵌入

`chat_session`、`chat_message`、`uploaded_file`、`artifact` 各自一個 collection，形狀對映現有表。**不採嵌入**（把訊息/版本鏈塞進 session 文件）：嵌入會讓單一文件隨版本鏈無上限成長、撞 Mongo 16MB／單文件上限；且大 payload 一拆又回到跨 collection 查詢，locality 好處消失（`architecture.md` 既有論證）。

「無上限成長」是**單文件**問題（嵌入才有）；分開 collection 時，新增一版 = insert 一個新文件，成長的是 **collection 筆數**（本該無上限、靠索引查，等同關聯式 table 列數增加），永遠碰不到 16MB per-document 上限。

### `_id` 與參照

- `_id` 沿用現有 **String UUID**（36 字元），保留現有 id 形狀與 `ChatSession` 的 **client 指定 id**（upsert 設計）。id 由應用層產生（維持現行 `@UuidGenerator` 等價的 UUID 產生；Spring Data Mongo 對 null String `_id` 會塞 ObjectId hex，故 MUST 明確在建立時賦 UUID，不依賴預設）。
- 跨實體關聯用**純 String 參照欄位**（`sessionId`、`artifactId`），**無 DB 強制外鍵**——與現況一致（`artifact_id` 本就是軟關聯、無 FK；訊息↔artifact 對應在應用層/前端由 `artifactId` 推導）。

### 存取模式：熱路徑無 join

核心存取全是「某 `sessionId`/`userId` 底下的全部 X」——單 collection 依索引查，Mongo 原生且與關聯式同成本、**非 join**：

| 存取 | 查法 |
|---|---|
| 按 user 撈 session 列表 | `chat_session.find({userId}).sort(updatedAt)` |
| 撈 session 訊息 | `chat_message.find({sessionId}).sort(createdAt)` |
| 撈 session artifact 版本鏈 | `artifact.find({sessionId}).sort(createdAt)` |
| 撈 session 上傳檔 | `uploaded_file.find({sessionId})` |
| ownership | `chat_session.find({_id, userId})` |

唯一「像 join」的是組出「一個 session 的完整畫面」——在應用層跑約 4 個 `findBySessionId`（固定少數、走索引、結果集小），**非 N+1**，且現況本就分開查。付出的代價：失去 FK 強制完整性（改應用層維護；`artifactId` 本無 FK 影響有限）、跨 collection 一致性變顯式（但有交易保住，見下）。`$lookup` 不走熱路徑。

## 持久層改寫

### 框架與 entity

- **Spring Data JPA → Spring Data MongoDB**（`spring-boot-starter-data-mongodb`，移除 `spring-boot-starter-data-jpa`、`ojdbc11`、`flyway-*`、`h2`）。
- Entity：`@Entity`/`@Table` → `@Document(collection=...)`；`@Id` 保留（String）；欄位 `@Field`；`@Column` 相關移除。JPA Auditing（`@CreatedDate`/`@LastModifiedDate`）→ Mongo Auditing（`@EnableMongoAuditing` + 同名 annotation，Spring Data Mongo 支援）。
- `ChatSession` **維持實作 `Persistable<String>`**（Spring Data Mongo 同樣以 `Persistable.isNew()` 決定 insert vs replace，不需改判定機制）——client 指定 id 的 upsert 語意與「建立時先 `setId()`」原封不動保留，這正是不改用 ObjectId `_id` 的關鍵理由。
- `@Version` optimistic locking 保留（Spring Data Mongo 原生支援）。
- Lombok 用法（`@Getter`/`@Setter`/`@EqualsAndHashCode(of="id")` 等）不變。

### Repository 與 Mapper

- `JpaRepository` → `MongoRepository`；derived query method（`findByUserIdOrderByUpdatedAtDesc`、`findBySessionId...`）多數原樣可用。無法 derive 的用 `@Query`（Mongo JSON query）。分頁 `Pageable`/`Page<T>` 保留。
- **MapStruct mapper 不動**（entity↔DTO 轉換與持久層無關）。

### 交易（保留 @Transactional）

- replica set 可用 → 配一顆 **`MongoTransactionManager`** bean，讓 `@Transactional` 與 `TransactionTemplate` 對 Mongo 生效。
- `AgentConversationWriter` 的 `TransactionTemplate`（artifact + AI 訊息同交易、storage 寫檔 `IOException` 回滾整筆）**語意維持**，只是底層換 Mongo 交易。
- ⚠️ Mongo 交易限制（無 savepoint、部分操作語意不同、交易內建 collection 隱含限制）——`AgentConversationWriter`、`FileService` 的 `TransactionTemplate` 路徑 MUST 實測驗過（見測試）。

### 索引/schema 管理

- **丟掉 Flyway**（Mongo schemaless）。
- 索引改在**啟動時建立**（一個 `ApplicationRunner`/`@EventListener` 用 `MongoTemplate.indexOps()`，或 `@Indexed`/`@CompoundIndex` + `spring.data.mongodb.auto-index-creation=true`——採**顯式 runner**，因 auto-index-creation 預設關且生產不建議）：
  - `chat_session`：`(userId, updatedAt)`
  - `chat_message`：`(sessionId, createdAt)`
  - `uploaded_file`：`sessionId`；`(sessionId, alias)` **unique**（對映現行 `uq_uploaded_file_alias`）
  - `artifact`：`sessionId`

### 組態

- 移除 `spring.datasource.*`、`spring.jpa.*`、`spring.flyway.*`、ddl-auto、H2 console。
- 新增 `spring.data.mongodb.uri`（env `SPRING_DATA_MONGODB_URI`，含 `?replicaSet=...`）。
- Actuator health 換 Mongo（`spring-boot-starter-actuator` 的 `MongoHealthIndicator` 自動生效）。

## 測試策略

**flapdoodle 嵌入式 mongod**（`de.flapdoodle.embed.mongo`）——CI 無 Docker，故不能用 Testcontainers；flapdoodle 不需要 Docker、最貼近現行 H2 的「CI 零 Docker」迴圈。

- **MUST 設成單成員 replica set**：否則 standalone 無交易，`@Transactional` 測試會失敗。flapdoodle 啟動 mongod 時帶 `--replSet` 並 `rs.initiate()`。
- Spring Boot 3.4 已移除內建 embedded-mongo auto-config → **手動接線**（起 `MongodExecutable`／或 flapdoodle-spring 社群整合，注入 `spring.data.mongodb.uri` 指向嵌入實例），封裝成一個測試基底（如 `@EmbeddedMongoReplicaSet` 自訂或 base test class）。
- **⚠️ 硬前提**：flapdoodle 首次會下載對應版本 mongod binary；air-gapped internal CI 連不出去，MUST 先把該 binary 放進**內部 mirror**（或預塞進 CI image）。此前提未成立則測試起不來——實作前 MUST 確認可取得。
- 既有 ~600 測試：repository/service 層（原 `@DataJpaTest`/啟 context 者）改對嵌入 Mongo 跑；`@WebMvcTest` + `@MockitoBean` 的 controller slice **不受影響**（不碰 DB）。斷言 SQL/JPA 特定行為者需改寫。**這是本案最大工時**。

## compose / infra / 文件

- `docker-compose.infra.yml`：`oracle` 服務 → `mongo`（**本機也 MUST 初始化成單成員 replica set**，否則本機跑不了交易——init 容器 `rs.initiate()`）；volume 換名；cloudbeaver `depends_on` 改。
- `docker-compose.app.yml`：`SPRING_DATASOURCE_URL` → `SPRING_DATA_MONGODB_URI`（`mongodb://mongo:27017/cowork?replicaSet=rs0`）。
- README、`docs/architecture.md`（「為什麼選 relational」→「被 internal 基盤強制換 Mongo＋如何保住不變量」、ER 圖標註、H2 敘述、清理由 DB 驅動段落沿用）、CLAUDE.md（DB 段落、Entity ID/JPA 規則改 Mongo 對應）。歷史 plan/spec 不回改。

## 風險與前提

| 項目 | 說明 / 對策 |
|---|---|
| **~600 測試 JPA→Mongo 改寫** | 本案主要工時；分批進行，controller slice 不受影響先確認範圍 |
| **flapdoodle mongod binary（air-gapped）** | 硬前提，實作前 MUST 確認內部 mirror 可取得；拿不到則測試策略需重議 |
| **Mongo `@Transactional` 限制** | 無 savepoint、操作語意差異；`AgentConversationWriter`/`FileService` 交易路徑 MUST 實測 e2e 驗過 |
| **本機/測試 Mongo 需 replica set** | compose init 容器與 flapdoodle 皆 MUST `rs.initiate()`，否則交易不可用 |
| **FK 完整性喪失** | 改應用層維護；`artifactId` 本無 FK，影響有限 |

## 已否決的替代方案

- **嵌入建模**（訊息/版本鏈 embed 進 session 文件）：撞 16MB 單文件成長、大 payload 拆開又回 join 形，無淨收益。
- **Testcontainers 測試**：目前主流且最貼近生產，但 CI **無 Docker** → 出局。
- **standalone Mongo**：無多文件交易 → 一致性不變量得靠應用層補償，複雜且易錯；已確認 internal 是 replica set，無此問題。
- **`bwaldvogel/mongo-java-server`**（純 Java in-memory）：不需 binary/Docker，但功能覆蓋不完整、**交易支援弱**，與本案重度依賴交易衝突 → 不採。
