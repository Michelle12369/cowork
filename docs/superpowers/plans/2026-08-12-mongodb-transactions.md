# MongoDB 交易（Branch 3：疊在 Branch 1 上）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Branch 1（純遷移基座）之上，加回 MongoDB 多文件交易——`MongoTransactionManager` + 重新包 `@Transactional`/`TransactionTemplate`，讓三處多文件寫入真原子，並重加 Branch 1 刪掉的原子性測試。

**Architecture:** 三分支策略的交易支。internal Mongo 已確認為 replica set（多文件交易可用）。難點在**測試/本機也需單成員 replica set**（`@Transactional` 才成立，CI 又無 Docker）：flapdoodle 從 standalone 改成單成員 replica set。生產靠 `MongoTransactionManager`＋replica set 原生原子性，Branch 1 的裸寫入 + storage cleanup 改回交易包裝。

**Tech Stack:** Spring Boot 3.4.1、Spring Data MongoDB、`MongoTransactionManager`、`de.flapdoodle.embed.mongo`（測試，單成員 replica set）。

**Spec:** `docs/superpowers/specs/2026-08-11-oracle-to-mongodb-migration-design.md`（三分支差異總表、Branch 3 欄）

**Base:** `feat/oracle-to-mongodb-txn`（自 `feat/oracle-to-mongodb` = Branch 1 @ 342af5c 開）

## Global Constraints

- Java 17（NEVER 18+ API）；google-java-format 由 hook 自動跑，勿手動改格式
- constructor injection（`@RequiredArgsConstructor`）；NEVER `@Autowired` field injection
- bean 命名分類法；例外類放 `..exception..`；DTO record；MapStruct 不動
- 測試命名 `methodName_condition_expectedBehavior`；`@DataMongoTest` 不掃 `@Configuration`→需 `@Import(PersistenceConfig.class)`（Branch 1 既有模式）
- Secrets NEVER 進 properties；`.properties` 值一律 ASCII
- 用語：codebase NEVER 寫「公司」「外部（環境義）」——一律 internal／upstream
- **本分支核心**：加回交易；三處多文件寫入的**正常路徑行為不得改變**（只是失敗時多了原子回滾）；storage 副作用仍在交易外（Mongo 交易不涵蓋 MinIO/S3）——維持 Branch 1 既有的 storage cleanup 語意
- 每個 task 完成即 commit；歷史 plan/spec 不回改

## ⚠️ 阻擋級風險（Task 1 即驗證）

flapdoodle spring3x autoconfig **只起 standalone mongod**；交易需要**單成員 replica set**。Task 1 必須先證明「flapdoodle 單成員 replica set + 一筆多文件交易」在此 sandbox 起得來（含 mongod binary 可取得、`rs.initiate()` 能到 PRIMARY）。**若 Task 1 無法讓交易在嵌入 replica set 上成立 → 回報 BLOCKED**，因為 CI 無 Docker、沒有替代測試底層。

## 檔案結構

| 檔案 | 動作 |
|---|---|
| `backend/src/test/.../support/EmbeddedReplicaSetMongo*.java`（新建） | flapdoodle 單成員 replica set 測試 harness |
| `backend/src/test/resources/config/application.properties` | 移除/改 spring3x standalone autoconfig 設定（改由 harness 提供 uri） |
| `config/PersistenceConfig.java` | 加 `MongoTransactionManager` + `TransactionTemplate` bean |
| `agent/AgentConversationWriter.java` | persistHtmlResult/persistAiMessage/tryPersistAiMessage 重新包 `TransactionTemplate` |
| `service/ArtifactRepairService.java` | 重加 `@Transactional` |
| `service/FileService.java` | upload 批次重新包 `TransactionTemplate`（storage cleanup 維持交易外） |
| `service/SessionService.java` | 視需要重加 `@Transactional`（多步驟寫入才需要；純讀不加） |
| 測試：`FileControllerTest`、`FileServiceDecryptionFailureTest` | 重加 Branch 1 刪掉的原子性斷言測試（現由交易 rollback 達成） |
| `docker-compose.infra.yml`、`docker-compose.app.yml` | mongo 改單成員 replica set（`rs.initiate`）、uri 加 `?replicaSet=rs0` |
| `docs/architecture.md`、`CLAUDE.md` | 敘述改「原子性由 MongoDB 多文件交易（replica set）達成」 |

---

### Task 1: flapdoodle 單成員 replica set 測試 harness（去風險 gate）

> 先證明交易在嵌入 replica set 上成立，再投入其餘。**這是 BLOCKER 檢查點。**

**Files:**
- Create: `backend/src/test/java/com/erd/cowork/support/EmbeddedReplicaSetMongo.java`（啟動單成員 replica set 的 harness）
- Create: `backend/src/test/java/com/erd/cowork/support/TransactionSmokeTest.java`
- Modify: `backend/src/test/resources/config/application.properties`

**Interfaces:**
- Produces: 一個能讓 `@SpringBootTest`/`@DataMongoTest` 連上「單成員 replica set 嵌入 mongod」的機制，並把 `spring.data.mongodb.uri`（含 `?replicaSet=rs0`）注入 context。

- [ ] **Step 1: 建 replica-set harness**

用 flapdoodle transitions API 起帶 `--replSet` 的 mongod，連上後 `rs.initiate()` 等到 PRIMARY，透過 `@DynamicPropertySource` 注入 uri。放 `EmbeddedReplicaSetMongo.java`（單例、`@BeforeAll` 或 static 啟動一次）。核心（flapdoodle 4.x；實際 API 名以編譯為準，關鍵是 `MongodArguments` 帶 replication `Storage.of("rs0", ...)`、start `Version.Main.V7_0`、連上跑 `rs.initiate`）：

```java
package com.erd.cowork.support;

import com.mongodb.client.MongoClient;
import com.mongodb.client.MongoClients;
import de.flapdoodle.embed.mongo.commands.MongodArguments;
import de.flapdoodle.embed.mongo.commands.ServerAddress;
import de.flapdoodle.embed.mongo.distribution.Version;
import de.flapdoodle.embed.mongo.transitions.Mongod;
import de.flapdoodle.embed.mongo.transitions.RunningMongodProcess;
import de.flapdoodle.embed.mongo.types.DistributionBaseUrl;
import de.flapdoodle.reverse.TransitionWalker;
import de.flapdoodle.reverse.transitions.Start;
import org.bson.Document;
import org.springframework.test.context.DynamicPropertyRegistry;

/** 單成員 replica set 嵌入 mongod——交易在測試才成立（standalone 無交易）。啟動一次、JVM 存活期間共用。 */
public final class EmbeddedReplicaSetMongo {

  private static TransitionWalker.ReachedState<RunningMongodProcess> running;
  private static String connectionString;

  private EmbeddedReplicaSetMongo() {}

  public static synchronized void start() {
    if (running != null) {
      return;
    }
    Mongod mongod =
        Mongod.instance()
            .withMongodArguments(
                Start.to(MongodArguments.class)
                    .initializedWith(
                        MongodArguments.defaults()
                            .withReplication(de.flapdoodle.embed.mongo.types.Storage.of("rs0", 10))));
    running = mongod.start(Version.Main.V7_0);
    ServerAddress address = running.current().getServerAddress();
    String host = address.getHost() + ":" + address.getPort();
    try (MongoClient client = MongoClients.create("mongodb://" + host + "/?directConnection=true")) {
      Document config = new Document("_id", "rs0")
          .append("members", java.util.List.of(new Document("_id", 0).append("host", host)));
      client.getDatabase("admin").runCommand(new Document("replSetInitiate", config));
      // 等 PRIMARY
      for (int attempt = 0; attempt < 60; attempt++) {
        Document status = client.getDatabase("admin").runCommand(new Document("hello", 1));
        if (Boolean.TRUE.equals(status.getBoolean("isWritablePrimary"))) {
          break;
        }
        Thread.sleep(500);
      }
    } catch (InterruptedException interrupted) {
      Thread.currentThread().interrupt();
      throw new IllegalStateException("interrupted waiting for replica set primary", interrupted);
    }
    connectionString = "mongodb://" + host + "/cowork?replicaSet=rs0";
  }

  public static void registerUri(DynamicPropertyRegistry registry) {
    start();
    registry.add("spring.data.mongodb.uri", () -> connectionString);
  }
}
```

> 若上述 flapdoodle 型別/方法名與實際 jar 不符（`Storage`/`withReplication`/`MongodArguments.defaults` 等在 4.x 可能位於不同套件），以編譯通過為準調整——**目標不變：起單成員 replica set、`rs.initiate`、到 PRIMARY、給出含 `?replicaSet=rs0` 的 uri**。spring3x 的 `de.flapdoodle.embed.mongo` 傳遞依賴已在 classpath；若缺 transitions API，於 pom test scope 顯式加 `de.flapdoodle.embed:de.flapdoodle.embed.mongo:4.x`。

- [ ] **Step 2: 停用 spring3x standalone autoconfig，改用 harness**

`src/test/resources/config/application.properties`：移除 `de.flapdoodle.mongodb.embedded.version=7.0.14`（那會觸發 spring3x standalone autoconfig，與我們的 replica-set harness 衝突）。若 spring3x autoconfig 仍會自動啟動，於測試基底以 `@ImportAutoConfiguration(exclude = ...)` 或屬性關閉它——確保只有 harness 提供 mongo。

- [ ] **Step 3: 交易 smoke 測試**

`TransactionSmokeTest.java`：用 `@DataMongoTest` + `@Import(PersistenceConfig.class)` + `@DynamicPropertySource` 掛 harness，注入 `MongoTemplate` 與（暫時的）`MongoTransactionManager`，在一個 `TransactionTemplate` 內寫兩個文件、故意在第二個寫入後拋例外，斷言**第一個文件也被回滾**（證明多文件交易生效）：

```java
package com.erd.cowork.support;

import static org.assertj.core.api.Assertions.assertThat;

import com.erd.cowork.config.PersistenceConfig;
import com.erd.cowork.domain.ChatMessage;
import com.erd.cowork.domain.Sender;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.data.mongo.DataMongoTest;
import org.springframework.context.annotation.Import;
import org.springframework.data.mongodb.MongoTransactionManager;
import org.springframework.data.mongodb.core.MongoTemplate;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.transaction.support.TransactionTemplate;

@DataMongoTest
@Import(PersistenceConfig.class)
class TransactionSmokeTest {

  @DynamicPropertySource
  static void mongoUri(DynamicPropertyRegistry registry) {
    EmbeddedReplicaSetMongo.registerUri(registry);
  }

  @Autowired MongoTemplate mongoTemplate;

  @Test
  void multiDocumentTransaction_secondWriteThrows_firstWriteRolledBack() {
    mongoTemplate.getCollection("chat_message").drop();
    TransactionTemplate tx =
        new TransactionTemplate(new MongoTransactionManager(mongoTemplate.getMongoDatabaseFactory()));
    try {
      tx.executeWithoutResult(status -> {
        ChatMessage first = new ChatMessage();
        first.setSessionId("s1");
        first.setSender(Sender.AI);
        mongoTemplate.save(first);
        throw new RuntimeException("boom after first write");
      });
    } catch (RuntimeException expected) {
      // rolled back
    }
    assertThat(mongoTemplate.getCollection("chat_message").countDocuments()).isZero();
  }
}
```

- [ ] **Step 4: 跑 smoke（BLOCKER gate）**

Run: `cd backend && ./mvnw -Dtest=TransactionSmokeTest test`
Expected: PASS（第一個文件被回滾、count=0）。**若無法讓 replica set 起來或交易不生效 → 回報 BLOCKED，附具體錯誤（binary 下載、rs.initiate、PRIMARY 逾時等）。**

- [ ] **Step 5: Commit**

```bash
git add backend/src/test/java/com/erd/cowork/support backend/src/test/resources
git commit -m "test(backend): flapdoodle 單成員 replica set harness + 多文件交易 smoke（Branch 3 去風險）"
```

---

### Task 2: MongoTransactionManager + 重新包三處多文件寫入

**Files:**
- Modify: `config/PersistenceConfig.java`、`agent/AgentConversationWriter.java`、`service/ArtifactRepairService.java`、`service/FileService.java`、`service/SessionService.java`

**Interfaces:**
- Consumes: Task 1 的 replica-set 測試底層。
- Produces: `MongoTransactionManager` + `TransactionTemplate` bean；三處多文件寫入原子。

- [ ] **Step 1: PersistenceConfig 加交易 bean**

class Javadoc 改為「Branch 3：replica set，多文件交易由 MongoTransactionManager 提供」。加：

```java
  @Bean
  MongoTransactionManager transactionManager(
      org.springframework.data.mongodb.MongoDatabaseFactory databaseFactory) {
    return new MongoTransactionManager(databaseFactory);
  }

  @Bean
  TransactionTemplate transactionTemplate(
      org.springframework.transaction.PlatformTransactionManager transactionManager) {
    return new TransactionTemplate(transactionManager);
  }
```

- [ ] **Step 2: AgentConversationWriter 重新包交易**

注入 `TransactionTemplate transactionTemplate`（`@RequiredArgsConstructor` 加 final 欄位）。`persistHtmlResult`/`persistAiMessage`/`tryPersistAiMessage` 的 body 用 `transactionTemplate.execute(status -> { ... })` 包起來（回傳值透傳）——**body 內容不變**，只是恢復交易邊界。`persistHtmlResult` 的 storage `fileStorage.store` 仍在交易內（IOException 拋出→回滾整筆，同遷移前語意）。Javadoc 的「Branch 1 writes are not atomic」段改回「artifact + AI 訊息同交易；storage IOException 回滾整筆」。

- [ ] **Step 3: ArtifactRepairService 重加 @Transactional**

`repairFromBrowserErrors` method 上加 `@Transactional`（artifact 更新 + repair 訊息同交易）。

- [ ] **Step 4: FileService.upload 重新包交易**

注入 `TransactionTemplate`。批次 `save` 段用 `transactionTemplate.execute(status -> {...})` 包起來（回傳 `List<FileDto>`）——**外層的 storage `storedKeys` cleanup catch 維持在交易外**（storage 非交易資源；交易只保 DB 批次原子）。移除 Branch 1 的「裸逐檔」註解。

- [ ] **Step 5: SessionService @Transactional**

只有多步驟**寫入** method 需要（若某 method 只單筆寫或純讀則不加）。逐一檢視，對真正多步驟寫入的加 `@Transactional`；純讀 method 不加（Mongo 無 readOnly 交易需求）。

- [ ] **Step 6: 全測（現有 610+ 應仍綠）+ Commit**

Run: `cd backend && ./mvnw test`
Expected: 全綠（現有測試在 replica-set 底層 + 交易下仍通過）。

```bash
git add backend/src/main/java
git commit -m "feat(backend): 加回 MongoTransactionManager + 三處多文件寫入重新包交易"
```

---

### Task 3: 重加原子性斷言測試

**Files:**
- Modify: `backend/src/test/.../FileControllerTest.java`、`FileServiceDecryptionFailureTest.java`

**Interfaces:**
- Consumes: Task 2 的交易；Task 1 的 replica-set 底層。

- [ ] **Step 1: 從 git 還原 Branch 1 刪掉的兩個測試並適配**

Branch 1 的 commit `6c52c22` 刪了兩個原子性測試；其父 `7d13b03` 仍有。取回內容：
`git show 7d13b03:backend/src/test/java/com/erd/cowork/web/FileControllerTest.java` 找 `uploadBatchWithOneBadFile_rollsBackWholeBatch_noOrphans`、`git show 7d13b03:backend/src/test/java/com/erd/cowork/service/FileServiceDecryptionFailureTest.java` 找 `upload_secondFileDecryptionFails_deletesFirstFilesStoredObject`。把這兩個測試方法（及必要 helper）加回對應檔案，斷言：一個壞檔 → **整批 rollback、DB 無半批殘留、storage 無孤兒**。現在交易生效，這些應通過。

- [ ] **Step 2: 跑這兩檔確認綠**

Run: `cd backend && ./mvnw -Dtest=FileControllerTest,FileServiceDecryptionFailureTest test`
Expected: PASS（交易 rollback 讓「無半批」成立）。

- [ ] **Step 3: 全測 + Commit**

Run: `cd backend && ./mvnw test`
Expected: 全綠。

```bash
git add backend/src/test
git commit -m "test(backend): 重加原子性斷言測試——交易 rollback 達成無半批/無孤兒"
```

---

### Task 4: compose 單成員 replica set + 文件

**Files:**
- Modify: `docker-compose.infra.yml`、`docker-compose.app.yml`、`docs/architecture.md`、`CLAUDE.md`

- [ ] **Step 1: docker-compose.infra.yml — mongo 改單成員 replica set**

`mongo` 服務 command 加 `--replSet rs0`；加一個 init（`mongo-init` 容器或 healthcheck-gated 一次性 `mongosh --eval 'rs.initiate(...)'`）確保啟動後 `rs.initiate()` 成單成員 replica set。範例 init 容器：

```yaml
  mongo:
    image: mongo:7.0
    restart: unless-stopped
    command: ["--replSet", "rs0", "--bind_ip_all"]
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

  mongo-init:
    image: mongo:7.0
    depends_on:
      - mongo
    restart: "no"
    entrypoint:
      - bash
      - -c
      - 'until mongosh --host mongo:27017 --quiet --eval "rs.status().ok" 2>/dev/null | grep -q 1; do mongosh --host mongo:27017 --quiet --eval "rs.initiate({_id:\"rs0\",members:[{_id:0,host:\"mongo:27017\"}]})" || true; sleep 2; done'
    networks:
      - erd-cowork-net
```

- [ ] **Step 2: docker-compose.app.yml — uri 加 replicaSet**

`SPRING_DATA_MONGODB_URI` 預設改 `mongodb://mongo:27017/cowork?replicaSet=rs0&directConnection=false`（或等義）。backend `depends_on` 視需要等 `mongo-init`。

- [ ] **Step 3: 文件**

- `docs/architecture.md`：把 Branch 1 的「原子性缺口解耦到 Branch 2/3」敘述更新為「**原子性由 MongoDB 多文件交易（replica set + `MongoTransactionManager`）達成**」；三處多文件寫入標為交易保護；本機/測試需單成員 replica set。
- `CLAUDE.md`：DB 段落補「已採 Branch 3 交易方案（replica set）」；Entity/交易規則相應更新。
- Branch 2（補償）改述為「未採的 standalone 替代方案」。

- [ ] **Step 4: e2e 驗證（真 replica set mongo）**

```bash
docker network create erd-cowork-net 2>/dev/null || true
docker compose --env-file .env.docker -f docker-compose.infra.yml up -d mongo mongo-init
docker compose -f docker-compose.infra.yml ps   # 等 mongo healthy、mongo-init 完成
cd backend && SPRING_DATA_MONGODB_URI="mongodb://localhost:27017/cowork?replicaSet=rs0" ./mvnw spring-boot:run
```

Expected：backend 連上 replica set（log 顯示 REPLICA_SET/PRIMARY）、索引建立、`curl localhost:8080/actuator/health` UP。實跑一輪「送訊息→產 artifact」或以測試證交易路徑無誤。驗完停 backend、`docker compose -f docker-compose.infra.yml stop mongo mongo-init`（不 down -v）。若環境無法起，DONE_WITH_CONCERNS（compose/文件改動仍完成）。

- [ ] **Step 5: Commit**

```bash
git add docker-compose.infra.yml docker-compose.app.yml docs/architecture.md CLAUDE.md
git commit -m "feat(infra): mongo 改單成員 replica set + uri replicaSet + 文件（交易方案）"
```

---

## 完成後

依 `superpowers:finishing-a-development-branch`：`./mvnw test` 全綠 → opus 全分支終審 → PR。**PR/merge 策略**：Branch 3 含 Branch 1 全部 commit（基於它開），終審後與使用者確認——Branch 3 PR 進 master（取代 Branch 1 的 PR #43），或先 merge Branch 1 再 merge Branch 3 的交易 delta。此決策留給 finishing 階段與使用者。
