# Oracle → MongoDB（Branch 1：純遷移基座）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 erd-cowork backend 資料層從 Oracle（Spring Data JPA + Flyway + H2 測試）整組換成 MongoDB，四 collection、**完全不含交易與補償**——純遷移基座。

**Architecture:** 三分支策略的第一支。Branch 1＝純框架換裝（JPA→Spring Data MongoDB），四 collection、`message.artifactId` 不翻、**移除所有交易基建（無 tx manager、無 `@Transactional`/`TransactionTemplate`）、不做任何補償**，多文件缺口暫不處理。原子性策略解耦到後續兩支：**Branch 2（補償：reaper + upload 補償）**、**Branch 3（交易：`MongoTransactionManager` + `@Transactional`）**，各基於 Branch 1、擇一 merge。測試改 flapdoodle 嵌入式 mongod（standalone、無 Docker）。

**Tech Stack:** Spring Boot 3.4.1、Spring Data MongoDB、`de.flapdoodle.embed.mongo.spring3x`（測試）、MapStruct（不動）、Lombok。

**Spec:** `docs/superpowers/specs/2026-08-11-oracle-to-mongodb-migration-design.md`

## Global Constraints

- Java 17（NEVER 18+ API）；google-java-format 由 hook 自動跑，勿手動改格式
- 變數 NEVER 1–2 字元名稱；constructor injection（`@RequiredArgsConstructor`）；NEVER `@Autowired` field injection
- bean 命名分類法（命名即契約）；例外類放 `..exception..`；DTO record；MapStruct `unmappedTargetPolicy = ERROR`，**mapper 不動**
- 測試命名 `methodName_condition_expectedBehavior`；controller slice 用 `@WebMvcTest`+`@MockitoBean`
- Secrets NEVER 進 properties；`.properties` 值一律 ASCII（中文只能放註解）
- 用語：codebase NEVER 寫「公司」「外部（環境義）」——一律 internal／upstream
- **Branch 1 範圍鐵律**：NEVER 加 `MongoTransactionManager`；**移除所有 `@Transactional`/`TransactionTemplate`/tx manager**（不是掛 no-op，是整組拔掉）；**不做 reaper、不做 upload DB 補償、不翻 `message.artifactId`**——這些屬 Branch 2/3
- **Branch 1 單獨不宜上 production**（多文件原子性缺口未處理）；須 merge Branch 2 或 3 其一才完整
- 每個 task 完成即 commit；分支 `feat/oracle-to-mongodb`（已存在，spec 已 commit）
- 歷史 plan/spec 不回改

## ⚠️ 阻擋級前提（開工前 MUST 確認）

**flapdoodle 首次會下載對應版本 mongod binary**（Task 1 起就需要）。air-gapped internal 環境連不出去時，MUST 先把 mongod 7.0.x binary 放進**內部 mirror**（設 flapdoodle 的 download/distribution 來源指向它）或預塞 flapdoodle cache。**此前提未成立則 Task 1 smoke 與後續全套測試都起不來**。

## 檔案結構（Branch 1 會動到的）

| 檔案 | 動作 |
|---|---|
| `backend/pom.xml` | 移 jpa/ojdbc/flyway/h2，加 mongodb + flapdoodle |
| `application.properties`、`application-local.properties` | 移 datasource/jpa/flyway/h2，加 mongodb.uri |
| `config/PersistenceConfig.java` | JPA→Mongo auditing、**移除 tx manager + TransactionTemplate bean**、加 `AfterConvertCallback` |
| `config/MongoIndexInitializer.java` | **新建**（啟動建索引） |
| `domain/{ChatSession,ChatMessage,UploadedFile,Artifact}.java` | `@Entity`→`@Document` |
| `repo/*.java`（4 個） | `JpaRepository`→`MongoRepository`、`@Modifying @Query`→Mongo `@Query`+`@Update` |
| `agent/AgentConversationWriter.java` | **移除 `TransactionTemplate` 注入與 execute 包裝** → 裸寫入 |
| `service/ArtifactRepairService.java`、`service/SessionService.java` | **移除 `@Transactional`** |
| `service/FileService.java` | **移除 upload 的 `transactionTemplate.execute` 包裝**（保留既有 storage cleanup catch），裸寫入 |
| `service/RetentionCleanupService.java` | stale artifact 取值改用 `Artifact` getter |
| `service/SessionGuard.java` | 例外 `DataIntegrityViolationException`→`DuplicateKeyException` |
| `db/migration/V1__init.sql` | **刪除** |
| 測試：`FileControllerTest`、`FileServiceDecryptionFailureTest` | **刪除斷言原子性 rollback 的測試方法** |
| compose / README / architecture.md / CLAUDE.md | 更新 |

---

### Task 1: 換持久層框架 + 移除所有交易基建 → 全編譯、smoke 綠

> 框架大爆炸換裝＋去交易化是不可切分的原子單位——entity/repo/交易移除必須一起改才編得起來。完成時「app 起得來、基本 CRUD 對 flapdoodle Mongo 成功、無任何交易基建」。

**Files:** 見上表（除測試 rollback 刪除與 compose/docs 外全部）。

**Interfaces:**
- Produces: `@Document` entities（collection `chat_session`/`chat_message`/`uploaded_file`/`artifact`）；`MongoRepository` 介面（method 簽名同現有）；`MongoIndexInitializer`；`PersistenceConfig`（無 tx manager）。

- [ ] **Step 1: pom.xml 換依賴**

移除：`spring-boot-starter-data-jpa`、`com.oracle.database.jdbc:ojdbc11`、`org.flywaydb:flyway-core`、`org.flywaydb:flyway-database-oracle`、`com.h2database:h2`。加入：

```xml
    <dependency>
      <groupId>org.springframework.boot</groupId>
      <artifactId>spring-boot-starter-data-mongodb</artifactId>
    </dependency>
    <dependency>
      <groupId>de.flapdoodle.embed</groupId>
      <artifactId>de.flapdoodle.embed.mongo.spring3x</artifactId>
      <version>4.20.1</version>
      <scope>test</scope>
    </dependency>
```

- [ ] **Step 2: application.properties 換組態**

移除 `spring.datasource.*`、`spring.jpa.*`、`spring.flyway.*`。加入：

```properties
spring.data.mongodb.uri=${SPRING_DATA_MONGODB_URI:mongodb://localhost:27017/cowork}
```

`application-local.properties`（gitignored，存在才改）同樣，移除 `spring.h2.console.*`。

- [ ] **Step 3: PersistenceConfig 換 auditing、移除交易基建**

整檔改為（`@EnableMongoAuditing`；**不再有任何 transaction manager / TransactionTemplate bean**；加 `AfterConvertCallback` 讓載入的 `ChatSession` 標 not-new）：

```java
package com.erd.cowork.config;

import com.erd.cowork.domain.ChatSession;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.data.mongodb.config.EnableMongoAuditing;
import org.springframework.data.mongodb.core.mapping.event.AfterConvertCallback;

/**
 * Branch 1（純遷移基座）：MongoDB standalone、無多文件交易，且本分支刻意不引入任何交易語意——
 * 移除 JPA 的 TransactionTemplate/transaction manager，服務改裸寫入。多文件原子性策略解耦到
 * Branch 2（補償）/ Branch 3（交易），本分支不含。
 */
@Configuration
@EnableMongoAuditing
public class PersistenceConfig {

  /** 載入既有 session 後標 not-new，讓後續 save 走 replace 而非 insert（取代 JPA @PostLoad）。 */
  @Bean
  AfterConvertCallback<ChatSession> chatSessionAfterConvert() {
    return (session, document, collection) -> {
      session.markNotNew();
      return session;
    };
  }
}
```

- [ ] **Step 4: 四個 entity 換 @Document**

模式（`ChatMessage` 為例；`@Column`/`@Lob`/`@Enumerated` 全移除，import 換 `org.springframework.data.annotation.*` 與 `...mongodb.core.mapping.Document`）：

```java
package com.erd.cowork.domain;

import java.time.Instant;
import lombok.EqualsAndHashCode;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import org.springframework.data.annotation.CreatedDate;
import org.springframework.data.annotation.Id;
import org.springframework.data.mongodb.core.mapping.Document;

@Document(collection = "chat_message")
@Getter
@Setter
@EqualsAndHashCode(of = "id")
@NoArgsConstructor
public class ChatMessage {

  @Id private String id;
  private String sessionId;
  private Sender sender;
  private String text;
  private String stepsJson;
  private String artifactId;
  private String questionsJson;

  @CreatedDate private Instant createdAt;
}
```

`ChatSession`（保留 `Persistable<String>`，`isNew` 由 `markNotNew()` 在 `AfterConvertCallback` 翻）：

```java
package com.erd.cowork.domain;

import java.time.Instant;
import lombok.EqualsAndHashCode;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import org.springframework.data.annotation.CreatedDate;
import org.springframework.data.annotation.Id;
import org.springframework.data.annotation.LastModifiedDate;
import org.springframework.data.annotation.Transient;
import org.springframework.data.domain.Persistable;
import org.springframework.data.mongodb.core.mapping.Document;

@Document(collection = "chat_session")
@Getter
@Setter
@EqualsAndHashCode(of = "id")
@NoArgsConstructor
public class ChatSession implements Persistable<String> {

  @Id private String id;
  private String title;
  private String userId;

  @CreatedDate private Instant createdAt;
  @LastModifiedDate private Instant updatedAt;

  @Transient private boolean isNew = true;

  @Override
  public boolean isNew() {
    return isNew;
  }

  public void markNotNew() {
    this.isNew = false;
  }
}
```

`UploadedFile`、`Artifact` 比照 `ChatMessage`（欄位名不變、移除所有 JPA annotation；`Artifact` 的 Javadoc 保留）。

- [ ] **Step 5: 四個 repository 換 MongoRepository**

`extends JpaRepository<X, String>` → `extends MongoRepository<X, String>`；derived query 全部原樣保留。`ArtifactRepository` 改寫（`@Modifying @Query`→Mongo `@Query`+`@Update`；`findStaleArtifactStorageKeys` 回傳 `List<Artifact>`、移除 `ArtifactStorageKeyView`）：

```java
package com.erd.cowork.repo;

import com.erd.cowork.domain.Artifact;
import java.time.Instant;
import java.util.List;
import java.util.Optional;
import org.springframework.data.mongodb.repository.MongoRepository;
import org.springframework.data.mongodb.repository.Query;
import org.springframework.data.mongodb.repository.Update;
import org.springframework.data.repository.query.Param;

public interface ArtifactRepository extends MongoRepository<Artifact, String> {

  Optional<Artifact> findFirstBySessionIdOrderByCreatedAtDesc(String sessionId);

  long countBySessionId(String sessionId);

  @Query("{ 'createdAt': { $lt: ?0 }, $or: [ {'htmlStorageKey': {$ne: null}}, {'rawHtmlStorageKey': {$ne: null}} ] }")
  List<Artifact> findStaleArtifactStorageKeys(@Param("cutoff") Instant cutoff);

  @Query("{ '_id': ?0 }")
  @Update("{ $set: { 'htmlStorageKey': null } }")
  void clearHtmlStorageKey(@Param("id") String id);

  @Query("{ '_id': ?0 }")
  @Update("{ $set: { 'rawHtmlStorageKey': null } }")
  void clearRawHtmlStorageKey(@Param("id") String id);
}
```

`RetentionCleanupService`（約 117 行起）對 stale artifact 改用 `Artifact` 的 `getId()`/`getHtmlStorageKey()`/`getRawHtmlStorageKey()`（原用 `ArtifactStorageKeyView` getter）；邏輯不變。

- [ ] **Step 6: 去交易化服務層**

- `AgentConversationWriter`：移除 `private final TransactionTemplate transactionTemplate;` 注入；`persistHtmlResult`/`persistAiMessage`/`tryPersistAiMessage` 三處的 `transactionTemplate.execute(status -> {...})` 拆掉、body 直接執行（裸寫入，回傳值照舊）。
- `ArtifactRepairService`：移除 method 上的 `@Transactional`。
- `SessionService`：移除 class 與 method 上的 `@Transactional`/`@Transactional(readOnly = true)`。
- `FileService.upload`：把 `transactionTemplate.execute(status -> {...})` 拆成直接執行 body（裸逐檔 `files.save`）；**保留**外層既有的 storage `storedKeys` cleanup catch（那是既有的顯式 storage 補償、非交易依賴）；移除 `TransactionTemplate` 注入。
- `ArtifactRepository` 的 `clearHtmlStorageKey`/`clearRawHtmlStorageKey`：Mongo `@Update` 不需要 `@Transactional`（Step 5 已無）。

- [ ] **Step 7: MongoIndexInitializer 啟動建索引**

`config/MongoIndexInitializer.java`：

```java
package com.erd.cowork.config;

import com.erd.cowork.domain.Artifact;
import com.erd.cowork.domain.ChatMessage;
import com.erd.cowork.domain.ChatSession;
import com.erd.cowork.domain.UploadedFile;
import lombok.RequiredArgsConstructor;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.data.domain.Sort;
import org.springframework.data.mongodb.core.MongoTemplate;
import org.springframework.data.mongodb.core.index.Index;
import org.springframework.stereotype.Component;

/** 啟動時建立索引（取代 Flyway）。 */
@Component
@RequiredArgsConstructor
public class MongoIndexInitializer {

  private final MongoTemplate mongoTemplate;

  @EventListener(ApplicationReadyEvent.class)
  public void createIndexes() {
    mongoTemplate.indexOps(ChatSession.class)
        .ensureIndex(new Index().on("userId", Sort.Direction.ASC).on("updatedAt", Sort.Direction.DESC));
    mongoTemplate.indexOps(ChatSession.class).ensureIndex(new Index().on("updatedAt", Sort.Direction.ASC));
    mongoTemplate.indexOps(ChatMessage.class)
        .ensureIndex(new Index().on("sessionId", Sort.Direction.ASC).on("createdAt", Sort.Direction.ASC));
    mongoTemplate.indexOps(UploadedFile.class)
        .ensureIndex(new Index().on("sessionId", Sort.Direction.ASC).on("expired", Sort.Direction.ASC));
    mongoTemplate.indexOps(UploadedFile.class)
        .ensureIndex(new Index().on("sessionId", Sort.Direction.ASC).on("alias", Sort.Direction.ASC).unique());
    mongoTemplate.indexOps(Artifact.class)
        .ensureIndex(new Index().on("sessionId", Sort.Direction.ASC).on("createdAt", Sort.Direction.DESC));
    mongoTemplate.indexOps(Artifact.class).ensureIndex(new Index().on("createdAt", Sort.Direction.ASC));
  }
}
```

- [ ] **Step 8: 刪 Flyway，加 flapdoodle 測試版本 property**

刪 `backend/src/main/resources/db/migration/`（整目錄）。`backend/src/test/resources/application.properties` 加：

```properties
de.flapdoodle.mongodb.embedded.version=7.0.14
```

- [ ] **Step 9: smoke 測試**

`backend/src/test/java/com/erd/cowork/support/EmbeddedMongoSmokeTest.java`：

```java
package com.erd.cowork.support;

import static org.assertj.core.api.Assertions.assertThat;

import com.erd.cowork.domain.ChatSession;
import com.erd.cowork.repo.ChatSessionRepository;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.data.mongo.DataMongoTest;

@DataMongoTest
class EmbeddedMongoSmokeTest {

  @Autowired ChatSessionRepository sessions;

  @Test
  void save_newSessionWithClientId_roundTripsById() {
    ChatSession session = new ChatSession();
    session.setId("11111111-1111-1111-1111-111111111111");
    session.setUserId("user-a");
    session.setTitle("t");
    sessions.save(session);

    assertThat(sessions.findById("11111111-1111-1111-1111-111111111111"))
        .get()
        .extracting(ChatSession::getUserId)
        .isEqualTo("user-a");
  }
}
```

- [ ] **Step 10: 建置 + 跑 smoke**

Run: `cd backend && ./mvnw -Dtest=EmbeddedMongoSmokeTest test`
Expected: 編譯過、flapdoodle 起 standalone mongod、PASS。（首次下載 mongod binary；air-gapped 需先備 mirror。）

- [ ] **Step 11: Commit**

```bash
git add backend/pom.xml backend/src/main backend/src/test/java/com/erd/cowork/support backend/src/test/resources
git rm -r backend/src/main/resources/db/migration
git commit -m "feat(backend): 持久層換 MongoDB（純遷移，無交易/補償）——entity/repo/索引/去交易化/flapdoodle"
```

---

### Task 2: 全測試套件在 flapdoodle 上綠 + 移除原子性斷言測試

**Files:**
- Modify: 既有 17 個碰 DB 的測試 class（15 `@SpringBootTest` + 2 `@DataJpaTest`）
- Delete（測試方法）：`FileControllerTest.uploadBatchWithOneBadFile_rollsBackWholeBatch_noOrphans`、`FileServiceDecryptionFailureTest` 斷言 upload 部分失敗 rollback 的測試

**Interfaces:**
- Consumes: Task 1 的 Mongo 持久層與 flapdoodle 設定。

- [ ] **Step 1: 移除斷言原子性的測試**

Branch 1 無交易無補償，這些斷言「整批 rollback / 部分失敗無殘留」的測試無法成立——**刪除它們**（非 `@Disabled`）。Branch 2/3 會依各自機制重加：
- `FileControllerTest`：刪 `uploadBatchWithOneBadFile_rollsBackWholeBatch_noOrphans`（及其專用 helper 若無他用）。
- `FileServiceDecryptionFailureTest`：刪斷言 upload 部分失敗後「無半批殘留」的測試方法（保留純解密失敗、與原子性無關的斷言）。
- 在 commit message 註明「原子性測試移至 Branch 2/3」。

- [ ] **Step 2: 跑全套，盤點壞掉的測試**

Run: `cd backend && ./mvnw test 2>&1 | tee /tmp/mongo-test-run.txt; grep -E "Tests run|ERROR|FAIL" /tmp/mongo-test-run.txt | tail -40`
Expected: `@WebMvcTest`（4）不受影響；`@DataJpaTest`（2）編譯錯；部分 `@SpringBootTest` 因 H2 殘留或 JPA 斷言失敗。逐一記錄。

- [ ] **Step 3: `@DataJpaTest` → `@DataMongoTest`**

2 個 slice 測試：`@DataJpaTest`→`@DataMongoTest`；`TestEntityManager` 改注入 `MongoRepository`/`MongoTemplate`；SQL/JPA 斷言改 Mongo 等價。

- [ ] **Step 4: `@SpringBootTest` 逐一修綠**

flapdoodle spring3x autoconfig 自動提供嵌入 mongo。逐一處理：清掉測試裡的 datasource 覆寫；JPA 行為斷言（flush/cascade/SQL 計數）改 Mongo 等價；`@Sql` 種資料改用 repository `save`。逐檔 `./mvnw -Dtest=<Class> test` 驗綠。

- [ ] **Step 5: 全套綠**

Run: `cd backend && ./mvnw test`
Expected: 全部通過。

- [ ] **Step 6: Commit**

```bash
git add backend/src/test
git commit -m "test(backend): 測試遷移 flapdoodle Mongo；移除原子性斷言測試（移至 Branch 2/3）"
```

---

### Task 3: SessionGuard upsert 例外型別（E11000）

**Files:**
- Modify: `backend/src/main/java/com/erd/cowork/service/SessionGuard.java`（約 114 行 catch）
- Modify/Create: `SessionGuardTest`

- [ ] **Step 1: 寫測試（id 已存在同 user → 回既有、不拋）**

```java
@Test
void loadOrCreateOwnedAs_idAlreadyExistsSameUser_returnsExistingNotThrows() {
  String sessionId = "22222222-2222-2222-2222-222222222222";
  ChatSession existing = new ChatSession();
  existing.setId(sessionId);
  existing.setUserId("user-a");
  existing.setTitle("t");
  sessions.save(existing);

  ChatSession result = sessionGuard.loadOrCreateOwnedAs("user-a", sessionId);
  assertThat(result.getId()).isEqualTo(sessionId);
}
```

- [ ] **Step 2: 跑確認行為**

Run: `cd backend && ./mvnw -Dtest=SessionGuardTest test`

- [ ] **Step 3: 換 catch 型別**

`catch (DataIntegrityViolationException exception)` → `catch (org.springframework.dao.DuplicateKeyException exception)`（import 調整）；訊息與 fallback 不變。

- [ ] **Step 4: 跑確認 PASS**

Run: `cd backend && ./mvnw -Dtest=SessionGuardTest test`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/main/java/com/erd/cowork/service/SessionGuard.java backend/src/test
git commit -m "fix(backend): SessionGuard upsert 例外改 DuplicateKeyException（Mongo E11000）"
```

---

### Task 4: compose / infra / 文件

**Files:** `docker-compose.infra.yml`、`docker-compose.app.yml`、`README.md`、`docs/architecture.md`、`CLAUDE.md`

- [ ] **Step 1: docker-compose.infra.yml**

`oracle` 服務整組換 `mongo`（**standalone，不 `rs.initiate()`**）：

```yaml
  mongo:
    image: mongo:7.0
    restart: unless-stopped
    environment:
      MONGO_INITDB_DATABASE: ${MONGO_DATABASE:-cowork}
    ports:
      - "27017:27017"
    volumes:
      - mongo-data:/data/db
    networks:
      - erd-cowork-net
    healthcheck:
      test: ["CMD", "mongosh", "--quiet", "--eval", "db.adminCommand('ping').ok"]
      interval: 10s
      timeout: 5s
      retries: 10
      start_period: 20s
```

header 註解「oracle」改「mongo」；`volumes:` 的 `oracle-data:` 改 `mongo-data:`；cloudbeaver `depends_on` 的 `- oracle` 改 `- mongo`。

- [ ] **Step 2: docker-compose.app.yml**

`SPRING_DATASOURCE_URL`（含 username/password 三行）→ 單一 `SPRING_DATA_MONGODB_URI: ${SPRING_DATA_MONGODB_URI:-mongodb://mongo:27017/cowork}`；註解「oracle」改「mongo」、restart 註解改寫（Mongo 秒級就緒）。

- [ ] **Step 3: README / architecture.md / CLAUDE.md sweep**

Run: `grep -niE "oracle|VARCHAR2|CLOB|H2|Flyway|@UuidGenerator|JPA|relational" README.md docs/architecture.md CLAUDE.md` 逐一處理：
- README：H2 敘述→flapdoodle 嵌入 Mongo、infra 表格、完整環境。
- `docs/architecture.md`：mermaid Oracle 節點→MongoDB；ER 圖→collection（型別標註改 Mongo 語意/移除）；**「### 為什麼選 relational DB」整節改寫**為「### 為什麼被迫換 MongoDB（internal 基盤強制）＋如何保住不變量」（分開 collection＋熱路徑無 join；**原子性策略解耦到 Branch 2 補償／Branch 3 交易**）；H2 mode→flapdoodle。
- `CLAUDE.md`：DB 敘述、Entity ID 規則（`@UuidGenerator`→Mongo `@Id` String UUID + `Persistable`）、JPA/Flyway→Mongo、測試（H2→flapdoodle）；補一句「原子性走三分支：base/補償/交易」。

改完 `grep` 確認殘留 0（歷史敘事除外，列理由）。

- [ ] **Step 4: 端到端驗證（真 Mongo standalone）**

```bash
docker network create erd-cowork-net 2>/dev/null || true
docker compose --env-file .env.docker -f docker-compose.infra.yml up -d mongo
docker compose -f docker-compose.infra.yml ps   # 等 healthy
cd backend && SPRING_DATA_MONGODB_URI="mongodb://localhost:27017/cowork" ./mvnw spring-boot:run
```

Expected：啟動 log 有索引建立、無 Flyway；`curl -s localhost:8080/actuator/health` 回 `{"status":"UP"}`（Mongo health 綠）。驗完停 backend、`docker compose -f docker-compose.infra.yml stop mongo`（不 down -v）。

- [ ] **Step 5: Commit**

```bash
git add docker-compose.infra.yml docker-compose.app.yml README.md docs/architecture.md CLAUDE.md
git commit -m "feat(infra): compose oracle 換 mongo:7 standalone + README/architecture/CLAUDE 同步"
```

---

## 完成後

Branch 1（純遷移）：backend `./mvnw test` 全綠 → opus 全分支終審 → PR。**接續兩支獨立計畫（各基於 Branch 1，擇一 merge）**：
- **Branch 2（`feat/oracle-to-mongodb-compensation`）**：孤兒 artifact reaper + upload DB 補償（或翻 `artifact.messageId`）+ 重加原子性測試（斷言補償達成「無孤兒/無半批」）。
- **Branch 3（`feat/oracle-to-mongodb-txn`）**：`MongoTransactionManager` + 重新包 `@Transactional`/`TransactionTemplate` + 測試切單成員 replica set + 重加原子性測試（斷言交易 rollback）。
