# Oracle → MongoDB（standalone 主線）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 erd-cowork backend 資料層從 Oracle（Spring Data JPA + Flyway + H2 測試）整組換成 MongoDB，四 collection、不依賴多文件交易，缺口以 reaper/補償處理。

**Architecture:** 大爆炸換掉持久層框架（JPA→Spring Data MongoDB）——entity `@Entity`→`@Document`、repository `JpaRepository`→`MongoRepository`、丟 Flyway 改啟動時建索引、交易管理換 no-op（standalone）。多文件原子性缺口用 reaper（孤兒 artifact）與 per-file 補償（upload 半批）補。測試改用 flapdoodle 嵌入式 mongod（standalone、無 Docker）。

**Tech Stack:** Spring Boot 3.4.1、Spring Data MongoDB、`de.flapdoodle.embed.mongo.spring3x`（測試）、MapStruct（不動）、Lombok。

**Spec:** `docs/superpowers/specs/2026-08-11-oracle-to-mongodb-migration-design.md`

## Global Constraints

- Java 17（NEVER 18+ API）；google-java-format 由 hook 自動跑，勿手動改格式
- 變數 NEVER 1–2 字元名稱；constructor injection（`@RequiredArgsConstructor`）；NEVER `@Autowired` field injection
- `*Service`/`*Repository`/`*Config` 等 bean 命名分類法（命名即契約）；例外類放 `..exception..`
- DTO 一律 record；MapStruct `unmappedTargetPolicy = ERROR`；**MapStruct mapper 不動**
- 測試命名 `methodName_condition_expectedBehavior`；controller slice 用 `@WebMvcTest`+`@MockitoBean`
- Secrets NEVER 進 properties；`.properties` 值一律 ASCII（中文只能放註解）
- 用語：codebase NEVER 寫「公司」「外部（環境義）」——一律 internal／upstream
- **本計畫＝standalone 主線**：NEVER 用 `MongoTransactionManager`（standalone 連線會拋錯）；`@Transactional`/`TransactionTemplate` 保留在 code、但掛 **no-op transaction manager**
- **`message.artifactId` 方向不翻**（不採 `artifact.messageId`）
- 每個 task 完成即 commit；分支 `feat/oracle-to-mongodb`（已存在，spec 已 commit）
- 歷史 plan/spec（`docs/superpowers/` 既有檔）不回改

## ⚠️ 阻擋級前提（開工前 MUST 確認）

**flapdoodle 首次會下載對應版本 mongod binary**（Task 1 起就需要）。air-gapped internal 環境連不出去時，MUST 先把 mongod 7.0.x binary 放進**內部 mirror**（設 flapdoodle 的 download/distribution 來源指向它），或預先放進 flapdoodle cache 目錄。**此前提未成立則 Task 1 的 smoke 測試與後續全套測試都起不來**——若本機開發能連外、CI air-gapped，至少要在 CI 端備妥。

## 檔案結構（此計畫會動到的）

| 檔案 | 責任 | 動作 |
|---|---|---|
| `backend/pom.xml` | 依賴 | 移 jpa/ojdbc/flyway/h2，加 mongodb + flapdoodle |
| `backend/src/main/resources/application.properties` | 組態 | 移 datasource/jpa/flyway，加 mongodb.uri |
| `backend/src/main/java/com/erd/cowork/config/PersistenceConfig.java` | 交易/稽核設定 | JPA→Mongo auditing、no-op tx manager |
| `backend/src/main/java/com/erd/cowork/config/MongoIndexInitializer.java` | 啟動時建索引 | **新建** |
| `.../domain/{ChatSession,ChatMessage,UploadedFile,Artifact}.java` | entity | `@Entity`→`@Document` |
| `.../repo/*.java`（4 個） | repository | `JpaRepository`→`MongoRepository`、`@Modifying @Query`→Mongo |
| `.../service/SessionGuard.java` | session upsert | 例外型別 `DataIntegrityViolationException`→`DuplicateKeyException` |
| `.../service/OrphanArtifactReaper.java` | 孤兒 artifact 清理 | **新建** |
| `.../service/FileService.java` | 上傳批次補償 | 半批 rollback 補償 |
| `backend/src/test/.../support/EmbeddedMongoConfig`（或 properties） | 測試 Mongo | **新建**（flapdoodle standalone） |
| `db/migration/V1__init.sql` | Flyway schema | **刪除** |
| compose / README / architecture.md / CLAUDE.md | infra/文件 | 更新 |

---

### Task 1: 換持久層框架（deps + 組態 + entities + repositories + 索引 + 測試 Mongo）→ 全編譯、smoke 綠

> 框架大爆炸換裝是不可切分的原子單位——entity 與 repository 必須一起換才編得起來。此 task 較大，但完成時「app 起得來、基本 CRUD 對 flapdoodle Mongo 成功」。

**Files:**
- Modify: `backend/pom.xml`
- Modify: `backend/src/main/resources/application.properties`、`application-local.properties`
- Modify: `backend/src/main/java/com/erd/cowork/config/PersistenceConfig.java`
- Create: `backend/src/main/java/com/erd/cowork/config/MongoIndexInitializer.java`
- Modify: 4 entities、4 repositories
- Delete: `backend/src/main/resources/db/migration/V1__init.sql`（整個 `db/migration` 目錄）
- Create: `backend/src/test/java/com/erd/cowork/support/EmbeddedMongoSmokeTest.java`

**Interfaces:**
- Produces: `@Document` entities（collection 名 `chat_session`/`chat_message`/`uploaded_file`/`artifact`）；`MongoRepository` 介面（method 簽名同現有）；`MongoIndexInitializer`（啟動建索引）；no-op `PlatformTransactionManager` bean。

- [ ] **Step 1: pom.xml 換依賴**

移除：`spring-boot-starter-data-jpa`、`com.oracle.database.jdbc:ojdbc11`、`org.flywaydb:flyway-core`、`org.flywaydb:flyway-database-oracle`、`com.h2database:h2`。
加入（`<dependencies>` 內）：

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

移除 `spring.datasource.*`、`spring.jpa.*`、`spring.flyway.*`（若有）。加入：

```properties
spring.data.mongodb.uri=${SPRING_DATA_MONGODB_URI:mongodb://localhost:27017/cowork}
```

`application-local.properties`（gitignored，存在才改）同樣移除 datasource/jpa、加 mongodb.uri，移除 `spring.h2.console.*`。

- [ ] **Step 3: PersistenceConfig 換 auditing + no-op tx manager**

整檔改為（`@EnableJpaAuditing`→`@EnableMongoAuditing`；transaction manager 換不做真交易的 no-op，讓 `@Transactional`/`TransactionTemplate` 不報錯也不包交易）：

```java
package com.erd.cowork.config;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.data.mongodb.config.EnableMongoAuditing;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.AbstractPlatformTransactionManager;
import org.springframework.transaction.support.DefaultTransactionStatus;
import org.springframework.transaction.support.TransactionTemplate;

/**
 * standalone 主線：MongoDB 無多文件交易可用，故提供不做真交易的 no-op transaction manager——
 * {@code @Transactional}/{@code TransactionTemplate} 照樣掛在 code（讓 transaction 疊加分支
 * 的 delta 最小），但每筆寫入各自 auto-commit、失敗不回滾。多文件原子性缺口由 reaper/補償處理。
 * NEVER 用 MongoTransactionManager（standalone 連線會拋錯）。
 */
@Configuration
@EnableMongoAuditing
public class PersistenceConfig {

  @Bean
  public PlatformTransactionManager transactionManager() {
    return new NoOpTransactionManager();
  }

  @Bean
  public TransactionTemplate transactionTemplate(PlatformTransactionManager transactionManager) {
    return new TransactionTemplate(transactionManager);
  }

  /** non-bean logic: 每個交易操作皆 no-op；純為讓交易語意的 code 在 standalone 下可執行。 */
  static final class NoOpTransactionManager extends AbstractPlatformTransactionManager {
    @Override
    protected Object doGetTransaction() {
      return new Object();
    }

    @Override
    protected void doBegin(Object transaction, org.springframework.transaction.TransactionDefinition definition) {}

    @Override
    protected void doCommit(DefaultTransactionStatus status) {}

    @Override
    protected void doRollback(DefaultTransactionStatus status) {}
  }
}
```

- [ ] **Step 4: 四個 entity 換 @Document**

對每個 entity 套用同一模式（以 `ChatMessage` 為例，其餘比照——collection 名對映表名，`@Column` 移除，`@Lob` 移除，`@Enumerated` 移除）：

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
  private Sender sender; // enum 直接存字串,Mongo 原生支援
  private String text;
  private String stepsJson;
  private String artifactId;
  private String questionsJson;

  @CreatedDate private Instant createdAt;
}
```

`ChatSession`（保留 `Persistable<String>`——Spring Data Mongo 同以 `isNew()` 決定 insert vs replace；`@PostLoad`/`@PostPersist` 換成 Mongo 的生命週期或改用建立時 setId+isNew，見下）：

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

  // isNew 預設 true;載入後由 AfterConvertCallback 設 false(見 Step 4b),讓 update 走 replace。
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

（注意：`@Transient`/`@Id`/`@CreatedDate` 全改用 `org.springframework.data.annotation.*` 版本，不是 jakarta.persistence。`UploadedFile`、`Artifact` 比照 `ChatMessage` 模式，欄位名與現況相同、移除所有 JPA annotation。`Artifact` 的 Javadoc 保留。）

- [ ] **Step 4b: ChatSession isNew 的載入重置（新增 callback）**

JPA 的 `@PostLoad` 沒了，改用 Spring Data Mongo 的 `AfterConvertCallback` 把載入的 session 設為 not-new（否則 update 會被當 insert）。放進 `PersistenceConfig`：

```java
  @Bean
  org.springframework.data.mongodb.core.mapping.event.AfterConvertCallback<com.erd.cowork.domain.ChatSession>
      chatSessionAfterConvert() {
    return (session, document, collection) -> {
      session.markNotNew();
      return session;
    };
  }
```

- [ ] **Step 5: 四個 repository 換 MongoRepository**

`extends JpaRepository<X, String>` → `extends MongoRepository<X, String>`（import 換 `org.springframework.data.mongodb.repository.MongoRepository`）。derived query method 全部原樣保留（`findByUserIdOrderByUpdatedAtDesc`、`findBySessionIdOrderByCreatedAtAsc`、`findBySessionId`、`findBySessionIdAndExpiredFalse`、`findFirstBySessionIdOrderByCreatedAtDesc`、`countBySessionId`、`findByUpdatedAtBefore`）。

`ArtifactRepository` 的兩個 `@Modifying @Query`（`clearHtmlStorageKey`/`clearRawHtmlStorageKey`）與 `findStaleArtifactStorageKeys` 改寫：

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

  // retention：createdAt 早於 cutoff 且仍有 html/raw storage key 的 artifact
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

> 注意：原 `findStaleArtifactStorageKeys` 回傳的是 projection view（`ArtifactStorageKeyView`）。改回傳完整 `Artifact`（欄位少、成本可忽略）；`RetentionCleanupService` 呼叫端改用 `artifact.getId()`/`getHtmlStorageKey()`/`getRawHtmlStorageKey()`——見 Step 5b。

- [ ] **Step 5b: 調整 RetentionCleanupService 對 stale artifact 的取值**

`RetentionCleanupService`（約 117 行起）原用 `ArtifactStorageKeyView` 的 getter；改為對 `Artifact` 取 `getId()`/`getHtmlStorageKey()`/`getRawHtmlStorageKey()`。移除 `ArtifactRepository.ArtifactStorageKeyView` interface。邏輯（clear 哪個 key、刪哪個 storage）不變。

- [ ] **Step 6: MongoIndexInitializer 啟動建索引**

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

/** 啟動時建立索引（取代 Flyway；Spring auto-index-creation 預設關且生產不建議）。 */
@Component
@RequiredArgsConstructor
public class MongoIndexInitializer {

  private final MongoTemplate mongoTemplate;

  @EventListener(ApplicationReadyEvent.class)
  public void createIndexes() {
    mongoTemplate
        .indexOps(ChatSession.class)
        .ensureIndex(new Index().on("userId", Sort.Direction.ASC).on("updatedAt", Sort.Direction.DESC));
    mongoTemplate.indexOps(ChatSession.class).ensureIndex(new Index().on("updatedAt", Sort.Direction.ASC));
    mongoTemplate
        .indexOps(ChatMessage.class)
        .ensureIndex(new Index().on("sessionId", Sort.Direction.ASC).on("createdAt", Sort.Direction.ASC));
    mongoTemplate
        .indexOps(UploadedFile.class)
        .ensureIndex(new Index().on("sessionId", Sort.Direction.ASC).on("expired", Sort.Direction.ASC));
    mongoTemplate
        .indexOps(UploadedFile.class)
        .ensureIndex(new Index().on("sessionId", Sort.Direction.ASC).on("alias", Sort.Direction.ASC).unique());
    mongoTemplate
        .indexOps(Artifact.class)
        .ensureIndex(new Index().on("sessionId", Sort.Direction.ASC).on("createdAt", Sort.Direction.DESC));
    mongoTemplate.indexOps(Artifact.class).ensureIndex(new Index().on("createdAt", Sort.Direction.ASC));
  }
}
```

- [ ] **Step 7: 刪 Flyway migration，加 flapdoodle 測試版本 property**

刪 `backend/src/main/resources/db/migration/`（整個目錄）。
在 `backend/src/test/resources/` 建 `application.properties`（測試專用，讓 flapdoodle 用固定版本、避免抓最新；air-gapped 需搭配內部 mirror，見風險）：

```properties
de.flapdoodle.mongodb.embedded.version=7.0.14
```

- [ ] **Step 8: smoke 測試（確認 flapdoodle standalone + 一個 entity 往返）**

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

- [ ] **Step 9: 建置 + 跑 smoke**

Run: `cd backend && ./mvnw -Dtest=EmbeddedMongoSmokeTest test`
Expected: 編譯通過、flapdoodle 起 standalone mongod、測試 PASS。（首次會下載 mongod binary；air-gapped 需先備妥 mirror。）

- [ ] **Step 10: Commit**

```bash
git add backend/pom.xml backend/src/main backend/src/test/java/com/erd/cowork/support backend/src/test/resources
git rm -r backend/src/main/resources/db/migration
git commit -m "feat(backend): 持久層換 Spring Data MongoDB——entity/repo/索引/no-op tx manager/flapdoodle 測試"
```

---

### Task 2: 全測試套件在 flapdoodle 上綠

**Files:**
- Modify: 既有 15 個 `@SpringBootTest` 與 2 個 `@DataJpaTest` 測試 class（依實跑結果）
- Modify: `backend/src/test/resources/application.properties`（若 `@SpringBootTest` 需 mongodb.uri 覆寫）

**Interfaces:**
- Consumes: Task 1 的 Mongo 持久層與 flapdoodle 測試設定。

- [ ] **Step 1: 跑全套，盤點壞掉的測試**

Run: `cd backend && ./mvnw test 2>&1 | tee /tmp/mongo-test-run.txt; grep -E "Tests run|ERROR|FAIL" /tmp/mongo-test-run.txt | tail -40`
Expected: 編譯過；`@WebMvcTest`（4）全綠不受影響；`@DataJpaTest`（2）編譯錯（annotation 不存在）；部分 `@SpringBootTest` 可能因 H2 殘留設定或 JPA 斷言失敗。逐一記錄。

- [ ] **Step 2: `@DataJpaTest` → `@DataMongoTest`**

那 2 個 slice 測試：`@DataJpaTest` → `@org.springframework.boot.test.autoconfigure.data.mongo.DataMongoTest`；若用 `TestEntityManager` 改用注入的 `MongoRepository`/`MongoTemplate` 直接存取；SQL/JPA 特定斷言改成對 Mongo 的等價斷言。

- [ ] **Step 3: `@SpringBootTest` 逐一修綠**

`@SpringBootTest` 會啟完整 context——flapdoodle spring3x autoconfig 會自動提供嵌入 mongo。逐一處理失敗：
- 若因舊 H2/datasource property 殘留而炸 → 清掉測試裡的 datasource 覆寫。
- 若斷言依賴 JPA 行為（flush、cascade、SQL 計數）→ 改成 Mongo 等價（Mongo 無 flush/cascade 概念，通常是移除或改直接查文件）。
- 種資料的測試若原用 SQL/`@Sql` → 改用 repository `save` 種。

逐個測試檔修完即 `./mvnw -Dtest=<ClassName> test` 驗證該檔綠。

- [ ] **Step 4: 全套綠**

Run: `cd backend && ./mvnw test`
Expected: 全部通過（`@WebMvcTest` 不受影響、slice 與 full-context 皆對 flapdoodle Mongo 綠）。

- [ ] **Step 5: Commit**

```bash
git add backend/src/test
git commit -m "test(backend): 測試套件遷移到 flapdoodle 嵌入式 Mongo（H2 退役）"
```

---

### Task 3: SessionGuard upsert 例外型別（E11000）

**Files:**
- Modify: `backend/src/main/java/com/erd/cowork/service/SessionGuard.java`（約 114 行的 catch）
- Modify/Create: `backend/src/test/.../SessionGuardTest.java`（若有；否則新增行為測試）

**Interfaces:**
- Consumes: Task 1 的 `ChatSessionRepository`（Mongo）。

- [ ] **Step 1: 寫失敗測試（並發 upsert：第二次撞 unique _id 應 fallback load，不冒泡）**

在 `SessionGuardTest`（`@DataMongoTest` 或 `@SpringBootTest`）加：先存一個 session，再以同 id、同 userId 呼叫 `loadOrCreateOwnedAs`，斷言回傳既有 session 而非拋例外（模擬 race 的 fallback 路徑；直接以「id 已存在」觸發 `DuplicateKeyException`）。

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

- [ ] **Step 2: 跑測試確認 FAIL（現 catch `DataIntegrityViolationException` 在 Mongo 不會被丟）**

Run: `cd backend && ./mvnw -Dtest=SessionGuardTest test`
Expected: 若走到「先 findById 命中既有」分支則直接 return（可能已綠）；關鍵是把 catch 型別對齊 Mongo，避免真 race 時 `DuplicateKeyException` 冒泡。以「插入既有 id」直接觸發 save 的 duplicate key 做覆蓋。

- [ ] **Step 3: 換 catch 型別**

`SessionGuard.java` 的 `catch (DataIntegrityViolationException exception)` → `catch (org.springframework.dao.DuplicateKeyException exception)`（Spring Data Mongo 的 duplicate key 對應例外；import 調整）。訊息與 fallback `loadOwnedAs` 不變。

- [ ] **Step 4: 跑測試確認 PASS**

Run: `cd backend && ./mvnw -Dtest=SessionGuardTest test`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/main/java/com/erd/cowork/service/SessionGuard.java backend/src/test
git commit -m "fix(backend): SessionGuard upsert 例外改 DuplicateKeyException（Mongo E11000）"
```

---

### Task 4: 孤兒 artifact reaper（無交易缺口——問題 1）

**Files:**
- Create: `backend/src/main/java/com/erd/cowork/service/OrphanArtifactReaper.java`
- Create: `backend/src/test/.../OrphanArtifactReaperTest.java`
- Modify: `backend/src/main/java/com/erd/cowork/repo/ChatMessageRepository.java`（加 `existsByArtifactId` 或批次查詢輔助）

**Interfaces:**
- Consumes: `ArtifactRepository`、`ChatMessageRepository`、`FileStorage`。
- Produces: `OrphanArtifactReaper.reap()`——刪除「無任何 chat_message 以 artifactId 引用」的 artifact 及其 storage 物件。

- [ ] **Step 1: repository 加輔助 query**

`ChatMessageRepository` 加：`boolean existsByArtifactId(String artifactId);`

- [ ] **Step 2: 寫失敗測試**

`OrphanArtifactReaperTest`（`@SpringBootTest`，用真 FileStorage local 或 mock）：

```java
@Test
void reap_artifactNotReferencedByAnyMessage_isDeleted() {
  Artifact orphan = new Artifact();
  orphan.setSessionId("s1");
  orphan.setTitle("v1");
  orphan = artifacts.save(orphan); // 無任何 message.artifactId 指向它

  Artifact referenced = new Artifact();
  referenced.setSessionId("s1");
  referenced.setTitle("v2");
  referenced = artifacts.save(referenced);
  ChatMessage msg = new ChatMessage();
  msg.setSessionId("s1");
  msg.setSender(Sender.AI);
  msg.setArtifactId(referenced.getId());
  messages.save(msg);

  reaper.reap();

  assertThat(artifacts.findById(orphan.getId())).isEmpty();
  assertThat(artifacts.findById(referenced.getId())).isPresent();
}
```

- [ ] **Step 3: 跑測試確認 FAIL**

Run: `cd backend && ./mvnw -Dtest=OrphanArtifactReaperTest test`
Expected: COMPILE ERROR（OrphanArtifactReaper 不存在）

- [ ] **Step 4: 實作 reaper**

```java
package com.erd.cowork.service;

import com.erd.cowork.domain.Artifact;
import com.erd.cowork.repo.ArtifactRepository;
import com.erd.cowork.repo.ChatMessageRepository;
import com.erd.cowork.storage.FileStorage;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.data.domain.PageRequest;
import org.springframework.stereotype.Service;

/**
 * standalone 缺口補償（無交易）：persistHtmlResult 若在 artifact 已寫入後、AI 訊息寫入前失敗，
 * 會留下無訊息引用的孤兒 artifact，混進版本下拉。此 reaper 定期刪除「無任何 chat_message 以
 * artifactId 引用」的 artifact 及其 storage 物件。transaction 疊加分支合併後可移除本類。
 */
@Service
@RequiredArgsConstructor
@Slf4j
public class OrphanArtifactReaper {

  private static final int BATCH_SIZE = 200;

  private final ArtifactRepository artifacts;
  private final ChatMessageRepository messages;
  private final FileStorage fileStorage;

  public void reap() {
    int reaped = 0;
    for (Artifact artifact : artifacts.findAll(PageRequest.of(0, BATCH_SIZE))) {
      if (!messages.existsByArtifactId(artifact.getId())) {
        deleteStorageQuietly(artifact.getHtmlStorageKey());
        deleteStorageQuietly(artifact.getRawHtmlStorageKey());
        artifacts.deleteById(artifact.getId());
        reaped++;
      }
    }
    if (reaped > 0) {
      log.info("orphan artifact reaper removed {} orphan artifacts", reaped);
    }
  }

  private void deleteStorageQuietly(String storageKey) {
    if (storageKey == null) {
      return;
    }
    try {
      fileStorage.delete(storageKey);
    } catch (Exception exception) {
      log.warn("failed to delete orphan artifact storage key {}", storageKey, exception);
    }
  }
}
```

> 觸發方式：接上現有清理排程（`RetentionCleanupService` 的 cron 同一節奏呼叫 `reap()`），或加獨立 `@Scheduled`。實作時沿用專案既有排程風格；排程掛載寫進同一 commit。

- [ ] **Step 5: 跑測試確認 PASS**

Run: `cd backend && ./mvnw -Dtest=OrphanArtifactReaperTest test`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/src/main/java/com/erd/cowork/service/OrphanArtifactReaper.java backend/src/main/java/com/erd/cowork/repo/ChatMessageRepository.java backend/src/test
git commit -m "feat(backend): 孤兒 artifact reaper——standalone 無交易缺口(問題1)補償"
```

---

### Task 5: FileService.upload 半批補償（無交易缺口——問題 3）

**Files:**
- Modify: `backend/src/main/java/com/erd/cowork/service/FileService.java`（`upload` 的批次 `save` 段，約 186–193 行）
- Modify: `backend/src/test/.../FileServiceUploadTest.java`

**Interfaces:**
- Consumes: `UploadedFileRepository`、`FileStorage`。

- [ ] **Step 1: 寫失敗測試（批次中一筆 save 失敗 → 已寫入的檔案文件與 storage 都被清掉）**

在 `FileServiceUploadTest` 加：mock/安排讓第二個檔案的 `files.save` 拋例外，斷言第一個檔案的 DB 文件不留（已補償刪除）、且已存的 storage key 都被 `storage.delete`。（沿用該檔既有的 mock 風格。）

- [ ] **Step 2: 跑測試確認 FAIL**

Run: `cd backend && ./mvnw -Dtest=FileServiceUploadTest test`
Expected: FAIL（目前 no-op 交易不回滾，半批殘留）

- [ ] **Step 3: 實作補償**

`FileService.upload` 的批次寫入段：逐檔 `save`，記下已成功寫入的 id；任何一筆失敗時，於 catch 內刪除本批已寫入的 file 文件（`files.deleteById`）——與既有的 storage `storedKeys` cleanup 併在同一個失敗處置。示意：

```java
      List<String> savedFileIds = new ArrayList<>();
      try {
        List<FileDto> result = new ArrayList<>();
        for (UploadedFile entity : entities) {
          UploadedFile saved = files.save(entity);
          savedFileIds.add(saved.getId());
          result.add(mapper.toFileDto(saved));
        }
        return result;
      } catch (RuntimeException persistError) {
        for (String savedId : savedFileIds) {
          try {
            files.deleteById(savedId);
          } catch (Exception cleanupError) {
            log.warn("failed to roll back partial upload file doc {}", savedId, cleanupError);
          }
        }
        throw persistError; // 由外層 catch 一併清 storage（storedKeys）
      }
```

（移除原 `transactionTemplate.execute(...)` 包裝——no-op 交易不提供原子性，改為顯式逐檔補償；storage cleanup 的外層 catch 維持。）

- [ ] **Step 4: 跑測試確認 PASS + 全測**

Run: `cd backend && ./mvnw -Dtest=FileServiceUploadTest test && ./mvnw test`
Expected: PASS、全套綠

- [ ] **Step 5: Commit**

```bash
git add backend/src/main/java/com/erd/cowork/service/FileService.java backend/src/test
git commit -m "feat(backend): upload 半批補償——standalone 無交易缺口(問題3)"
```

---

### Task 6: compose / infra / 文件

**Files:**
- Modify: `docker-compose.infra.yml`（`oracle`→`mongo`，standalone）
- Modify: `docker-compose.app.yml`（datasource env → mongodb uri）
- Modify: `README.md`、`docs/architecture.md`、`CLAUDE.md`

**Interfaces:**
- Consumes: Task 1 的 `SPRING_DATA_MONGODB_URI` 組態。

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

header 註解「oracle」字樣改「mongo」；`volumes:` 的 `oracle-data:` 改 `mongo-data:`；cloudbeaver `depends_on` 的 `- oracle` 改 `- mongo`。

- [ ] **Step 2: docker-compose.app.yml**

`SPRING_DATASOURCE_URL`（及 username/password 三行）→ 單一：

```yaml
      SPRING_DATA_MONGODB_URI: ${SPRING_DATA_MONGODB_URI:-mongodb://mongo:27017/cowork}
```

header 與 backend 服務註解的「oracle」改「mongo」；「Oracle 首次啟動 2–4 分鐘 restart」註解改寫（Mongo 秒級就緒）。

- [ ] **Step 3: README / architecture.md / CLAUDE.md sweep**

Run: `grep -niE "oracle|VARCHAR2|CLOB|H2|Flyway|@UuidGenerator|JPA" README.md docs/architecture.md CLAUDE.md` 逐一處理：
- README：「H2（Oracle 相容模式）」→「flapdoodle 嵌入式 Mongo（測試）」、infra 表格 `oracle`→`mongo`、完整環境敘述。
- `docs/architecture.md`：mermaid 的 Oracle 節點→MongoDB；ER 圖改為 collection 圖（型別標註移除或改 Mongo 語意）；**「### 為什麼選 relational DB」整節改寫**為「### 為什麼被迫換 MongoDB（internal 基盤強制）＋如何保住不變量」（保住：分開 collection＋熱路徑無 join＋standalone 缺口 reaper/補償＋日後可加交易）；H2 mode 敘述改 flapdoodle。
- `CLAUDE.md`：專案脈絡的 DB 敘述、Entity ID 規則（`@UuidGenerator`→Mongo `@Id` String UUID、`Persistable`）、JPA/Flyway 段落→Mongo 對應、測試段落（H2→flapdoodle）。

改完 `grep` 確認殘留為 0（歷史敘事除外，需列理由）。

- [ ] **Step 4: 端到端驗證（真 Mongo standalone）**

```bash
docker network create erd-cowork-net 2>/dev/null || true
docker compose --env-file .env.docker -f docker-compose.infra.yml up -d mongo
docker compose -f docker-compose.infra.yml ps   # 等 healthy
cd backend && SPRING_DATA_MONGODB_URI="mongodb://localhost:27017/cowork" ./mvnw spring-boot:run
```

Expected：啟動 log 出現索引建立、無 Flyway；另開 shell `curl -s localhost:8080/actuator/health` 回 `{"status":"UP"}`（Mongo health 綠）。驗證後停 backend、`docker compose -f docker-compose.infra.yml stop mongo`（不 down -v）。

- [ ] **Step 5: Commit**

```bash
git add docker-compose.infra.yml docker-compose.app.yml README.md docs/architecture.md CLAUDE.md
git commit -m "feat(infra): compose oracle 換 mongo:7 standalone + README/architecture/CLAUDE 同步"
```

---

## 完成後

依 `superpowers:finishing-a-development-branch`：backend `./mvnw test` 全綠 → opus 全分支終審 → `gh pr create`（終審結論寫進 PR 描述）→ 使用者觸發 merge。**transaction 疊加分支（`feat/oracle-to-mongodb-txn`）為後續獨立計畫**，基於此主線、加 `MongoTransactionManager`＋`@Transactional` 生效＋刪 reaper/補償＋測試切單成員 replica set。
