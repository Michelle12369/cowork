# PVC RWX 儲存改造與分級保留 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 移除 S3/MinIO 儲存路線改為 PVC RWX 單一路線，並補上 artifact／workspace 的清理與可用環境變數調整的分級保留。

**Architecture:** `FileStorage` 與 `WorkspaceStore` 兩個介面保留，各自只剩單一本地實作。清理集中在 backend 的 `RetentionCleanupService`（DB 驅動：`uploaded_file`／`artifact` 兩張表各自查詢，workspace 走檔案系統但由 session row 決定），backend 新增掛載 `/data/workspace` 以便單一服務涵蓋兩顆 PVC。

**Tech Stack:** Java 17 / Spring Boot 3 / Lombok / JPA(Hibernate) / Oracle（測試用 H2）；Python 3.12 / FastAPI / pytest。

**Spec:** `docs/superpowers/specs/2026-08-01-pvc-storage-and-retention-design.md`

## Global Constraints

- Java 17，NEVER 使用 18+ API
- 一律 constructor injection，用 `@RequiredArgsConstructor`；NEVER `@Autowired` field injection
- `@Slf4j`；NEVER 手寫 `LoggerFactory.getLogger(...)`
- Config binding 一律 `@ConfigurationProperties` record；`application.yml` 用顯式 `${ENV_VAR:default}`，不依賴 relaxed binding
- 變數／參數 NEVER 用 1–2 字元名稱；迴圈計數器用 `index`／`rowIndex` 等
- 測試方法命名 `methodName_condition_expectedBehavior`
- 例外一律包裝原始 cause：`throw new XxxException("msg", cause)`；NEVER 空 catch
- IO 資源一律 try-with-resources
- google-java-format 由 hook 自動執行，NEVER 手動調整格式
- Python 端 `app/engine/` 純度規則：僅 stdlib（本次改造後 boto3 亦移除），LLM 框架禁止（ruff TID251 會擋）
- 每個 task 結束時 `./mvnw test` 必須全綠（Python 改動則 `uv run pytest`）

---

### Task 1: 修正 `ChatSession.updatedAt` 不隨對話更新

現行 `AgentOrchestrator.prepare()` 只在第一則 USER 訊息時 `save(session)`，第二輪起 entity 無欄位變更 → Hibernate 不發 UPDATE → `@PreUpdate` 不觸發 → `@LastModifiedDate` 不執行。結果 `updated_at` 等同 `created_at`，現行 30 天 retention 實為「建立後 30 天」。此為現存 bug，且後續所有「最後活動」判定都依賴它。

**Files:**
- Modify: `backend/src/main/java/com/erd/cowork/agent/AgentOrchestrator.java`（`prepare()`，約 158–170 行）
- Modify: `backend/src/main/java/com/erd/cowork/service/FileService.java`（上傳路徑）
- Test: `backend/src/test/java/com/erd/cowork/agent/AgentOrchestratorTest.java`

**Interfaces:**
- Consumes: 無（第一個 task）
- Produces: 「session 每輪對話與每次上傳都會 touch `updatedAt`」的保證。Task 5、6 的 cutoff 判定依賴此行為

- [x] **Step 1: 寫失敗測試**

加到 `AgentOrchestratorTest`（沿用該檔既有的 Mockito 風格與 `@Mock` 欄位）：

```java
  @Test
  void prepare_secondTurn_advancesSessionUpdatedAt() {
    Instant staleTimestamp = Instant.now().minus(Duration.ofDays(10));
    ChatSession session = new ChatSession();
    session.setId(SESSION_ID);
    session.setUserId(USER_ID);
    session.setTitle("existing title");
    session.setUpdatedAt(staleTimestamp);

    ChatMessage existingUserMessage = new ChatMessage();
    existingUserMessage.setSender(Sender.USER);

    when(sessionGuard.loadOrCreateOwnedAs(USER_ID, SESSION_ID)).thenReturn(session);
    when(messages.findBySessionIdOrderByCreatedAtAsc(SESSION_ID))
        .thenReturn(List.of(existingUserMessage));
    when(uploadedFiles.findBySessionId(SESSION_ID)).thenReturn(List.of());

    orchestrator.prepareForTest(USER_ID, SESSION_ID, "second question", null);

    assertThat(session.getUpdatedAt()).isAfter(staleTimestamp);
    Mockito.verify(sessionRepository).save(session);
  }
```

`SESSION_ID`／`USER_ID` 若該檔尚無常數，於類別頂端加：

```java
  private static final String SESSION_ID = "11111111-2222-3333-4444-555555555555";
  private static final String USER_ID = "user-1";
```

`prepare()` 目前是 private。為讓它可測，在 `AgentOrchestrator` 加一個 package-private 轉呼叫（同 package 的測試可見，不擴大公開 API）：

```java
  /** Package-private seam for tests; production callers go through the streaming entry point. */
  PrepareResult prepareForTest(
      String userId, String sessionId, String question, String baseArtifactId) {
    return prepare(userId, sessionId, question, baseArtifactId);
  }
```

- [x] **Step 2: 執行測試確認失敗**

Run: `./mvnw test -Dtest=AgentOrchestratorTest#prepare_secondTurn_advancesSessionUpdatedAt`
Expected: FAIL —— `session.getUpdatedAt()` 仍為 `staleTimestamp`，且 `sessionRepository.save` 未被呼叫

- [x] **Step 3: 實作 touch**

`AgentOrchestrator.prepare()` 中把現有的條件式 save 改為「title 只在首輪設，但每輪都 touch」：

```java
    // Title rule: set on the very first USER message
    boolean hasUserMessage =
        existingMessages.stream().anyMatch(chatMessage -> chatMessage.getSender() == Sender.USER);
    if (!hasUserMessage) {
      session.setTitle(truncate(question, SESSION_TITLE_MAX_LENGTH));
    }
    // Touch every turn so updatedAt means "last activity", not "created". Setting the field is
    // what makes the entity dirty -- save() alone on an unchanged entity issues no UPDATE, so
    // @LastModifiedDate would never fire (auditing overwrites this value with its own now()).
    session.setUpdatedAt(Instant.now());
    sessionRepository.save(session);
```

`FileService` 上傳路徑同樣 touch（已上傳但尚未提問的 session 也算活躍）。在既有取得 session 之處後加入相同兩行，並注入 `ChatSessionRepository`（constructor injection，`@RequiredArgsConstructor` 自動產生）。

- [x] **Step 4: 執行測試確認通過**

Run: `./mvnw test -Dtest=AgentOrchestratorTest`
Expected: PASS（含既有測試）

- [x] **Step 5: 全量回歸**

Run: `./mvnw test`
Expected: 全綠。若 `RetentionCleanupServiceTest` 因 session 時間戳改變而失敗，修正該測試的資料準備而非改回實作。

- [x] **Step 6: Commit**

```bash
git add backend/src/main/java/com/erd/cowork/agent/AgentOrchestrator.java \
        backend/src/main/java/com/erd/cowork/service/FileService.java \
        backend/src/test/java/com/erd/cowork/agent/AgentOrchestratorTest.java
git commit -m "fix: touch session updatedAt on every turn and upload

updatedAt 先前只在建立與第一則 USER 訊息時寫入，等同 createdAt，
使 retention 的「閒置 N 天」實際為「建立後 N 天」。"
```

---

### Task 2: `StorageProperties` 拆出 `Cleanup`／`Retention`，全數改為環境變數

**Files:**
- Modify: `backend/src/main/java/com/erd/cowork/config/StorageProperties.java`
- Modify: `backend/src/main/resources/application.yml`
- Modify: `backend/src/main/java/com/erd/cowork/service/RetentionCleanupService.java`
- Modify: `backend/src/main/java/com/erd/cowork/exception/FilesExpiredException.java`（顯示天數的來源）
- Test: `backend/src/test/java/com/erd/cowork/config/StoragePropertiesBindingTest.java`（新建）

**Interfaces:**
- Consumes: 無
- Produces:
  - `StorageProperties.Cleanup(String cron, boolean dryRun)`，存取子 `properties.cleanup()`
  - `StorageProperties.Retention(Duration uploads, Duration workspace, Duration artifact)`，存取子 `properties.retention()`
  - 移除 `retentionDays()`。Task 4、5 使用 `properties.retention().artifact()` 與 `.workspace()`

- [x] **Step 1: 寫失敗測試**

Create `backend/src/test/java/com/erd/cowork/config/StoragePropertiesBindingTest.java`:

```java
package com.erd.cowork.config;

import static org.assertj.core.api.Assertions.assertThat;

import java.time.Duration;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.TestPropertySource;

@SpringBootTest
@TestPropertySource(
    properties = {
      "erd.storage.cleanup.cron=0 30 4 * * *",
      "erd.storage.cleanup.dry-run=true",
      "erd.storage.retention.uploads=90d",
      "erd.storage.retention.workspace=200d",
      "erd.storage.retention.artifact=730d"
    })
class StoragePropertiesBindingTest {

  @Autowired StorageProperties storageProperties;

  @Test
  void binding_durationValues_parsesSimpleDayNotation() {
    assertThat(storageProperties.retention().uploads()).isEqualTo(Duration.ofDays(90));
    assertThat(storageProperties.retention().workspace()).isEqualTo(Duration.ofDays(200));
    assertThat(storageProperties.retention().artifact()).isEqualTo(Duration.ofDays(730));
  }

  @Test
  void binding_cleanupBlock_readsCronAndDryRun() {
    assertThat(storageProperties.cleanup().cron()).isEqualTo("0 30 4 * * *");
    assertThat(storageProperties.cleanup().dryRun()).isTrue();
  }
}
```

- [x] **Step 2: 執行測試確認失敗**

Run: `./mvnw test -Dtest=StoragePropertiesBindingTest`
Expected: FAIL —— 編譯錯誤，`StorageProperties` 無 `cleanup()`／`retention()`

- [x] **Step 3: 改 `StorageProperties`**

```java
package com.erd.cowork.config;

import java.time.Duration;
import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "erd.storage")
public record StorageProperties(
    String type, String localDir, String workspaceDir, Cleanup cleanup, Retention retention) {

  /** Scheduling knobs for {@code RetentionCleanupService}. */
  public record Cleanup(String cron, boolean dryRun) {}

  /**
   * Per-data-class retention windows. {@code uploads} and {@code workspace} are measured from the
   * session's last activity; {@code artifact} is measured from the artifact's own creation time.
   */
  public record Retention(Duration uploads, Duration workspace, Duration artifact) {}
}
```

注意：`S3` 巢狀 record 與 `type` 欄位在 Task 6 才移除（本 task 只動清理相關設定），此處保留 `type` 但刪掉 `retentionDays` 與 `S3`——若 Task 6 尚未執行會編譯失敗，故本 step **保留 `S3 s3` 欄位**，完整簽名為：

```java
public record StorageProperties(
    String type,
    String localDir,
    String workspaceDir,
    Cleanup cleanup,
    Retention retention,
    S3 s3) {

  public record Cleanup(String cron, boolean dryRun) {}

  public record Retention(Duration uploads, Duration workspace, Duration artifact) {}

  public record S3(String bucket, String region, String endpoint, boolean pathStyleAccess) {}
}
```

- [x] **Step 4: 改 `application.yml`**

把 `erd.storage` 區塊的 `retention-days: 30` 換成：

```yaml
erd:
  storage:
    type: ${ERD_STORAGE_TYPE:local}
    local-dir: ${ERD_STORAGE_LOCAL_DIR:./data/files}
    workspace-dir: ${ERD_STORAGE_WORKSPACE_DIR:./data/workspace}
    cleanup:
      cron: ${ERD_STORAGE_CLEANUP_CRON:0 0 3 * * *}
      dry-run: ${ERD_STORAGE_CLEANUP_DRY_RUN:false}
    retention:
      uploads: ${ERD_STORAGE_RETENTION_UPLOADS:180d}
      workspace: ${ERD_STORAGE_RETENTION_WORKSPACE:180d}
      artifact: ${ERD_STORAGE_RETENTION_ARTIFACT:730d}
```

`s3:` 區塊原樣保留（Task 6 移除）。

- [x] **Step 5: 改 `RetentionCleanupService` 的 cutoff 與 cron 來源**

```java
  @Scheduled(cron = "${erd.storage.cleanup.cron}")
  public void scheduledCleanup() {
    Instant cutoff = Instant.now().minus(properties.retention().uploads());
    int purged = cleanup(cutoff);
    log.info("Retention cleanup complete: purged {} file(s) with cutoff={}", purged, cutoff);
  }
```

`FilesExpiredException` 若以天數建構，改為傳入 `properties.retention().uploads().toDays()`；呼叫端在 `AgentOrchestrator.prepare()`。

- [x] **Step 6: 修既有測試的屬性**

`RetentionCleanupServiceTest` 的 `@TestPropertySource` 把 `erd.storage.retention-days=30` 改為：

```java
      "erd.storage.retention.uploads=30d",
      "erd.storage.retention.workspace=30d",
      "erd.storage.retention.artifact=730d",
      "erd.storage.cleanup.cron=-",
```

`cron=-` 讓排程在測試期間不自動觸發（`Scheduled.CRON_DISABLED`），測試直接呼叫 `cleanup(cutoff)`。

全 repo 搜尋其他引用：`grep -rn "retention-days\|retentionDays" backend/src`，逐一改掉。

- [x] **Step 7: 執行測試確認通過**

Run: `./mvnw test`
Expected: 全綠

- [x] **Step 8: Commit**

```bash
git add backend/src/main/java/com/erd/cowork/config/StorageProperties.java \
        backend/src/main/resources/application.yml \
        backend/src/main/java/com/erd/cowork/service/RetentionCleanupService.java \
        backend/src/main/java/com/erd/cowork/exception/FilesExpiredException.java \
        backend/src/test/java/com/erd/cowork/config/StoragePropertiesBindingTest.java \
        backend/src/test/java/com/erd/cowork/service/RetentionCleanupServiceTest.java
git commit -m "feat: 清理排程與三類保留期改為環境變數

retention-days 先前寫死、cleanup-cron 只存在於 @Scheduled 註解預設值。
改為 Duration 型別的 uploads/workspace/artifact 三個窗，加上 dry-run。"
```

---

### Task 3: storage key 加入 `uploads/`／`artifacts/` 類型前綴

前綴不是清理的前置條件（清理由 DB 查詢驅動），價值在 `du` 分類監控與未來拆兩顆 PVC 的選項。**不需要 migration**：key 完整存於 DB 欄位，舊的扁平 key 照常 resolve。

**Files:**
- Create: `backend/src/main/java/com/erd/cowork/storage/StorageCategory.java`
- Modify: `backend/src/main/java/com/erd/cowork/storage/StorageKeyUtils.java`
- Modify: `backend/src/main/java/com/erd/cowork/storage/FileStorage.java`
- Modify: `backend/src/main/java/com/erd/cowork/storage/LocalDiskStorage.java`
- Modify: `backend/src/main/java/com/erd/cowork/storage/S3FileStorage.java`（Task 6 才刪，此處需同步簽名以維持可編譯）
- Modify: `backend/src/main/java/com/erd/cowork/service/FileService.java:74`
- Modify: `backend/src/main/java/com/erd/cowork/agent/AgentConversationWriter.java:84`
- Modify: `backend/src/main/java/com/erd/cowork/service/ArtifactRepairService.java:126`
- Test: `backend/src/test/java/com/erd/cowork/storage/StorageKeyUtilsTest.java`

**Interfaces:**
- Consumes: 無
- Produces: `FileStorage.store(StorageCategory category, String sessionId, String originalFilename, InputStream in)`；`StorageCategory.UPLOAD`（前綴 `uploads`）與 `StorageCategory.ARTIFACT`（前綴 `artifacts`）

- [x] **Step 1: 寫失敗測試**

加到 `StorageKeyUtilsTest`：

```java
  @Test
  void buildKey_uploadCategory_prefixesWithUploads() {
    String key = StorageKeyUtils.buildKey(StorageCategory.UPLOAD, "session-1", "sales.csv");

    assertThat(key).startsWith("uploads/session-1/");
    assertThat(key).endsWith("_sales.csv");
  }

  @Test
  void buildKey_artifactCategory_prefixesWithArtifacts() {
    String key = StorageKeyUtils.buildKey(StorageCategory.ARTIFACT, "session-1", "abc.html");

    assertThat(key).startsWith("artifacts/session-1/");
  }

  @Test
  void buildKey_traversalFilename_stillReducedToBasenameUnderPrefix() {
    String key = StorageKeyUtils.buildKey(StorageCategory.UPLOAD, "session-1", "../../etc/passwd");

    assertThat(key).startsWith("uploads/session-1/");
    assertThat(key).endsWith("_passwd");
  }
```

- [x] **Step 2: 執行測試確認失敗**

Run: `./mvnw test -Dtest=StorageKeyUtilsTest`
Expected: FAIL —— 編譯錯誤，`StorageCategory` 不存在

- [x] **Step 3: 建立 `StorageCategory`**

```java
package com.erd.cowork.storage;

/**
 * Top-level storage key namespace. Keeps uploads and generated artifacts in separate directory
 * trees so disk usage can be attributed per data class and the two can later live on separate
 * volumes. Legacy keys written before this split have no prefix and resolve unchanged.
 */
public enum StorageCategory {
  UPLOAD("uploads"),
  ARTIFACT("artifacts");

  private final String prefix;

  StorageCategory(String prefix) {
    this.prefix = prefix;
  }

  public String prefix() {
    return prefix;
  }
}
```

- [x] **Step 4: 改 `StorageKeyUtils.buildKey`**

```java
  /**
   * Builds a storage key in the format {@code {category}/{sessionId}/{UUID}_{safeName}}.
   *
   * @param category top-level namespace separating uploads from generated artifacts
   * @param sessionId the session identifier used as the second path component
   * @param originalFilename the original filename (may contain path separators or special chars)
   * @return a safe, unique storage key
   */
  public static String buildKey(
      StorageCategory category, String sessionId, String originalFilename) {
    String safeName = sanitize(originalFilename);
    return category.prefix() + "/" + sessionId + "/" + UUID.randomUUID() + "_" + safeName;
  }
```

- [x] **Step 5: 改介面與兩個實作**

`FileStorage`：

```java
  /** Streams content to storage and returns the storage key. */
  String store(
      StorageCategory category, String sessionId, String originalFilename, InputStream in)
      throws IOException;
```

`LocalDiskStorage.store` 與 `S3FileStorage.store` 同步加參數，內部改呼叫 `StorageKeyUtils.buildKey(category, sessionId, originalFilename)`。`read`／`delete` 不變（key 自我描述，舊扁平 key 照常 resolve）。

- [x] **Step 6: 改三個呼叫端**

- `FileService.java:74` → `storage.store(StorageCategory.UPLOAD, sessionId, filename, in)`
- `AgentConversationWriter.java:84` → `fileStorage.store(StorageCategory.ARTIFACT, sessionId, artifactId + ".html", htmlStream)`
- `ArtifactRepairService.java:126` → `fileStorage.store(StorageCategory.ARTIFACT, sessionId, artifactId + ".html", htmlStream)`

- [x] **Step 7: 執行測試確認通過**

Run: `./mvnw test`
Expected: 全綠。`LocalDiskStorageTest`／`S3FileStorageTest`／`AgentOrchestratorTest` 的 mock 簽名需同步更新。

- [x] **Step 8: Commit**

```bash
git add backend/src/main/java/com/erd/cowork/storage/ \
        backend/src/main/java/com/erd/cowork/service/FileService.java \
        backend/src/main/java/com/erd/cowork/service/ArtifactRepairService.java \
        backend/src/main/java/com/erd/cowork/agent/AgentConversationWriter.java \
        backend/src/test/java/com/erd/cowork/
git commit -m "feat: storage key 加入 uploads/artifacts 類型前綴

讓磁碟用量可依資料類歸屬、並保留未來拆兩顆 PVC 的選項。
舊的扁平 key 完整存於 DB 欄位，照常 resolve，不需要 migration。"
```

---

### Task 4: artifact 兩年清理

刪除 PVC 上的實體 HTML 檔並把 `htmlStorageKey` 設為 null；**保留 artifact 列**——`ChatMessage.artifactId` 是純字串引用，且 `ArtifactService.getHtmlStream()` 在 `htmlStorageKey == null` 時已回 404，與 V6 前舊列的行為一致。`rawHtml` CLOB 不在此範圍（在 DB 不在 PVC，不影響容量）。

**Files:**
- Modify: `backend/src/main/java/com/erd/cowork/repo/ArtifactRepository.java`
- Modify: `backend/src/main/java/com/erd/cowork/service/RetentionCleanupService.java`
- Test: `backend/src/test/java/com/erd/cowork/service/RetentionCleanupServiceTest.java`

**Interfaces:**
- Consumes: `StorageProperties.Retention.artifact()`（Task 2）
- Produces: `RetentionCleanupService.cleanupArtifacts(Instant cutoff)` 回傳 `int`（清理的 artifact 數）

- [x] **Step 1: 寫失敗測試**

```java
  @Test
  void cleanupArtifacts_olderThanCutoff_deletesFileAndNullsStorageKey() throws IOException {
    ChatSession session = persistSession(Instant.now());
    String storageKey =
        fileStorage.store(
            StorageCategory.ARTIFACT,
            session.getId(),
            "old.html",
            new ByteArrayInputStream("<html></html>".getBytes(StandardCharsets.UTF_8)));

    Artifact artifact = new Artifact();
    artifact.setSessionId(session.getId());
    artifact.setTitle("old dashboard");
    artifact.setHtmlStorageKey(storageKey);
    artifact = artifactRepo.save(artifact);
    // createdAt is auditing-managed; force it past the cutoff via a direct update
    artifactRepo.flush();
    setArtifactCreatedAt(artifact.getId(), Instant.now().minus(Duration.ofDays(800)));

    int purged = cleanupService.cleanupArtifacts(Instant.now().minus(Duration.ofDays(730)));

    assertThat(purged).isEqualTo(1);
    assertThat(artifactRepo.findById(artifact.getId())).isPresent();
    assertThat(artifactRepo.findById(artifact.getId()).orElseThrow().getHtmlStorageKey()).isNull();
    assertThatThrownBy(() -> fileStorage.read(storageKey)).isInstanceOf(IOException.class);
  }

  @Test
  void cleanupArtifacts_withinCutoff_keepsFileAndKey() throws IOException {
    ChatSession session = persistSession(Instant.now());
    String storageKey =
        fileStorage.store(
            StorageCategory.ARTIFACT,
            session.getId(),
            "recent.html",
            new ByteArrayInputStream("<html></html>".getBytes(StandardCharsets.UTF_8)));

    Artifact artifact = new Artifact();
    artifact.setSessionId(session.getId());
    artifact.setTitle("recent dashboard");
    artifact.setHtmlStorageKey(storageKey);
    artifact = artifactRepo.save(artifact);

    int purged = cleanupService.cleanupArtifacts(Instant.now().minus(Duration.ofDays(730)));

    assertThat(purged).isZero();
    assertThat(artifactRepo.findById(artifact.getId()).orElseThrow().getHtmlStorageKey())
        .isEqualTo(storageKey);
  }
```

`setArtifactCreatedAt` 輔助方法（`@CreatedDate` 為 `updatable = false`，需繞過 JPA）：

```java
  @Autowired EntityManager entityManager;

  @Transactional
  void setArtifactCreatedAt(String artifactId, Instant createdAt) {
    entityManager
        .createNativeQuery("UPDATE artifact SET created_at = ?1 WHERE id = ?2")
        .setParameter(1, Timestamp.from(createdAt))
        .setParameter(2, artifactId)
        .executeUpdate();
  }
```

- [x] **Step 2: 執行測試確認失敗**

Run: `./mvnw test -Dtest=RetentionCleanupServiceTest`
Expected: FAIL —— `cleanupArtifacts` 不存在

- [x] **Step 3: 加 repository 查詢**

`ArtifactRepository`：

```java
  List<Artifact> findByCreatedAtBeforeAndHtmlStorageKeyIsNotNull(Instant cutoff);
```

- [x] **Step 4: 實作 `cleanupArtifacts`**

`RetentionCleanupService` 目前的欄位是 `sessionRepo`／`fileRepo`／`storage`／`properties`；`ArtifactRepository` 尚未注入，需加一個 final 欄位（`@RequiredArgsConstructor` 會自動納入建構子）：

```java
  private final ArtifactRepository artifactRepo;
```

新方法（沿用既有「逐筆獨立小交易、storage 刪除失敗僅 log.warn」的語意）：

```java
  /**
   * Deletes artifact HTML files older than {@code cutoff} and clears their storage key. The
   * artifact row itself is kept -- chat messages reference artifacts by id, and
   * ArtifactService returns 404 for a null storage key, matching pre-V6 rows.
   */
  public int cleanupArtifacts(Instant cutoff) {
    List<Artifact> staleArtifacts =
        artifactRepo.findByCreatedAtBeforeAndHtmlStorageKeyIsNotNull(cutoff);
    int count = 0;
    for (Artifact artifact : staleArtifacts) {
      String storageKey = artifact.getHtmlStorageKey();
      if (properties.cleanup().dryRun()) {
        log.info("[dry-run] would purge artifact id={} key={}", artifact.getId(), storageKey);
        count++;
        continue;
      }
      try {
        storage.delete(storageKey);
      } catch (IOException exception) {
        log.warn(
            "Failed to delete artifact storage key={}: {}",
            storageKey,
            exception.getMessage(),
            exception);
      }
      artifact.setHtmlStorageKey(null);
      artifactRepo.save(artifact);
      count++;
    }
    return count;
  }
```

在既有的 `cleanup(Instant)`（上傳檔）中同樣加入 dry-run 分支：進入迴圈後若 `properties.cleanup().dryRun()` 為真，只 `log.info("[dry-run] would purge upload key={}", ...)` 並 `continue`。

`scheduledCleanup()` 改為掃三類（workspace 於 Task 5 加入）：

```java
  @Scheduled(cron = "${erd.storage.cleanup.cron}")
  public void scheduledCleanup() {
    Instant now = Instant.now();
    int uploadsPurged = cleanup(now.minus(properties.retention().uploads()));
    int artifactsPurged = cleanupArtifacts(now.minus(properties.retention().artifact()));
    log.info(
        "Retention cleanup complete: uploads={} artifacts={} dryRun={}",
        uploadsPurged,
        artifactsPurged,
        properties.cleanup().dryRun());
  }
```

- [x] **Step 5: 執行測試確認通過**

Run: `./mvnw test -Dtest=RetentionCleanupServiceTest`
Expected: PASS

- [x] **Step 6: 補 dry-run 測試**

Create `backend/src/test/java/com/erd/cowork/service/RetentionCleanupDryRunTest.java`:

```java
package com.erd.cowork.service;

import static org.assertj.core.api.Assertions.assertThat;

import com.erd.cowork.domain.Artifact;
import com.erd.cowork.domain.ChatSession;
import com.erd.cowork.repo.ArtifactRepository;
import com.erd.cowork.repo.ChatSessionRepository;
import com.erd.cowork.storage.FileStorage;
import com.erd.cowork.storage.StorageCategory;
import jakarta.persistence.EntityManager;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.sql.Timestamp;
import java.time.Duration;
import java.time.Instant;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.TestPropertySource;
import org.springframework.transaction.annotation.Transactional;

@SpringBootTest
@TestPropertySource(
    properties = {
      "erd.storage.local-dir=${java.io.tmpdir}/erd-cowork-dryrun-test",
      "erd.storage.cleanup.cron=-",
      "erd.storage.cleanup.dry-run=true",
      "erd.storage.retention.uploads=30d",
      "erd.storage.retention.workspace=30d",
      "erd.storage.retention.artifact=730d"
    })
class RetentionCleanupDryRunTest {

  @Autowired RetentionCleanupService cleanupService;
  @Autowired ChatSessionRepository sessionRepo;
  @Autowired ArtifactRepository artifactRepo;
  @Autowired FileStorage fileStorage;
  @Autowired EntityManager entityManager;

  @Test
  void cleanupArtifacts_dryRunEnabled_countsButLeavesFileAndKeyIntact() throws IOException {
    ChatSession session = new ChatSession();
    session.setId(UUID.randomUUID().toString());
    session.setUserId("dry-run-user");
    session.setTitle("dry run session");
    session.setUpdatedAt(Instant.now());
    sessionRepo.save(session);

    String storageKey =
        fileStorage.store(
            StorageCategory.ARTIFACT,
            session.getId(),
            "old.html",
            new ByteArrayInputStream("<html></html>".getBytes(StandardCharsets.UTF_8)));

    Artifact artifact = new Artifact();
    artifact.setSessionId(session.getId());
    artifact.setTitle("old dashboard");
    artifact.setHtmlStorageKey(storageKey);
    artifact = artifactRepo.saveAndFlush(artifact);
    backdateArtifact(artifact.getId(), Instant.now().minus(Duration.ofDays(800)));

    int purged = cleanupService.cleanupArtifacts(Instant.now().minus(Duration.ofDays(730)));

    assertThat(purged).isEqualTo(1);
    assertThat(artifactRepo.findById(artifact.getId()).orElseThrow().getHtmlStorageKey())
        .isEqualTo(storageKey);
    try (InputStream stored = fileStorage.read(storageKey)) {
      assertThat(new String(stored.readAllBytes(), StandardCharsets.UTF_8))
          .isEqualTo("<html></html>");
    }
  }

  @Transactional
  void backdateArtifact(String artifactId, Instant createdAt) {
    entityManager
        .createNativeQuery("UPDATE artifact SET created_at = ?1 WHERE id = ?2")
        .setParameter(1, Timestamp.from(createdAt))
        .setParameter(2, artifactId)
        .executeUpdate();
  }
}
```

Run: `./mvnw test -Dtest=RetentionCleanupDryRunTest`
Expected: PASS —— 計數為 1，但檔案與 key 都還在

- [x] **Step 7: 全量回歸並 commit**

```bash
./mvnw test
git add backend/src/main/java/com/erd/cowork/repo/ArtifactRepository.java \
        backend/src/main/java/com/erd/cowork/service/RetentionCleanupService.java \
        backend/src/test/java/com/erd/cowork/service/
git commit -m "feat: artifact 兩年清理與 dry-run 模式

刪 PVC 實體檔並清空 htmlStorageKey，保留 artifact 列（訊息以 id 引用，
且 getHtmlStream 對 null key 已回 404）。artifact 是唯一不可重建的資料，
故加 dry-run 供首次上線先看清單。"
```

---

### Task 5: workspace 清理（backend 掛載 `/data/workspace`）

**Files:**
- Create: `backend/src/main/java/com/erd/cowork/service/WorkspaceRetentionService.java`
- Modify: `backend/src/main/java/com/erd/cowork/service/RetentionCleanupService.java`
- Test: `backend/src/test/java/com/erd/cowork/service/WorkspaceRetentionServiceTest.java`

**Interfaces:**
- Consumes: `StorageProperties.workspaceDir()`、`StorageProperties.Retention.workspace()`、`StorageProperties.Cleanup.dryRun()`（Task 2）
- Produces: `WorkspaceRetentionService.purgeStaleSessions(Instant cutoff)` 回傳 `int`（刪除的 session 目錄數）

- [x] **Step 1: 寫失敗測試**

```java
package com.erd.cowork.service;

import static org.assertj.core.api.Assertions.assertThat;

import com.erd.cowork.domain.ChatSession;
import com.erd.cowork.repo.ChatSessionRepository;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.time.Instant;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.TestPropertySource;

@SpringBootTest
@TestPropertySource(
    properties = {
      "erd.storage.workspace-dir=${java.io.tmpdir}/erd-cowork-workspace-test",
      "erd.storage.cleanup.cron=-"
    })
class WorkspaceRetentionServiceTest {

  @Autowired WorkspaceRetentionService workspaceRetentionService;
  @Autowired ChatSessionRepository sessionRepo;

  @Test
  void purgeStaleSessions_sessionIdleBeyondCutoff_removesSessionDirectory() throws IOException {
    ChatSession session = persistSession(Instant.now().minus(Duration.ofDays(400)));
    Path sessionDir = workspaceSessionDir(session);
    Files.createDirectories(sessionDir.resolve("results"));
    Files.writeString(sessionDir.resolve("dashboard.html"), "<html></html>");

    int purged = workspaceRetentionService.purgeStaleSessions(
        Instant.now().minus(Duration.ofDays(180)));

    assertThat(purged).isEqualTo(1);
    assertThat(Files.exists(sessionDir)).isFalse();
  }

  @Test
  void purgeStaleSessions_activeSession_keepsSessionDirectory() throws IOException {
    ChatSession session = persistSession(Instant.now());
    Path sessionDir = workspaceSessionDir(session);
    Files.createDirectories(sessionDir);
    Files.writeString(sessionDir.resolve("dashboard.html"), "<html></html>");

    int purged = workspaceRetentionService.purgeStaleSessions(
        Instant.now().minus(Duration.ofDays(180)));

    assertThat(purged).isZero();
    assertThat(Files.exists(sessionDir)).isTrue();
  }

  @Test
  void purgeStaleSessions_directoryAlreadyAbsent_doesNotCount() {
    persistSession(Instant.now().minus(Duration.ofDays(400)));

    int purged = workspaceRetentionService.purgeStaleSessions(
        Instant.now().minus(Duration.ofDays(180)));

    assertThat(purged).isZero();
  }
}
```

同類別內的兩個輔助方法（`@LastModifiedDate` 會在 save 時覆寫成 now，故需 native update 回填）：

```java
  @Autowired jakarta.persistence.EntityManager entityManager;

  @org.springframework.transaction.annotation.Transactional
  ChatSession persistSession(Instant updatedAt) {
    ChatSession session = new ChatSession();
    session.setId(UUID.randomUUID().toString());
    session.setUserId("workspace-user");
    session.setTitle("workspace session");
    session.setUpdatedAt(updatedAt);
    sessionRepo.saveAndFlush(session);
    entityManager
        .createNativeQuery("UPDATE chat_session SET updated_at = ?1 WHERE id = ?2")
        .setParameter(1, java.sql.Timestamp.from(updatedAt))
        .setParameter(2, session.getId())
        .executeUpdate();
    return session;
  }

  Path workspaceSessionDir(ChatSession session) {
    return Path.of(System.getProperty("java.io.tmpdir"))
        .resolve("erd-cowork-workspace-test")
        .resolve(session.getUserId())
        .resolve("sessions")
        .resolve(session.getId());
  }
```

- [x] **Step 2: 執行測試確認失敗**

Run: `./mvnw test -Dtest=WorkspaceRetentionServiceTest`
Expected: FAIL —— `WorkspaceRetentionService` 不存在

- [x] **Step 3: 實作**

```java
package com.erd.cowork.service;

import com.erd.cowork.config.StorageProperties;
import com.erd.cowork.domain.ChatSession;
import com.erd.cowork.repo.ChatSessionRepository;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.time.Instant;
import java.util.Comparator;
import java.util.List;
import java.util.stream.Stream;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

/**
 * Removes deepagent workspace directories for sessions that have been idle past the retention
 * window. Runs on the backend because the cutoff is driven by {@code chat_session.updated_at},
 * which lives in the backend database; the shared RWX volume makes the files reachable from here.
 */
@Service
@RequiredArgsConstructor
@Slf4j
public class WorkspaceRetentionService {

  private final ChatSessionRepository sessionRepo;
  private final StorageProperties properties;

  public int purgeStaleSessions(Instant cutoff) {
    Path workspaceRoot = Paths.get(properties.workspaceDir()).toAbsolutePath().normalize();
    List<ChatSession> staleSessions = sessionRepo.findByUpdatedAtBefore(cutoff);
    int count = 0;
    for (ChatSession session : staleSessions) {
      Path sessionDir =
          workspaceRoot
              .resolve(session.getUserId())
              .resolve("sessions")
              .resolve(session.getId())
              .normalize();
      // userId and sessionId come from the database, but the join is still verified so a
      // malformed row can never reach outside the workspace root.
      if (!sessionDir.startsWith(workspaceRoot)) {
        log.warn("Skipping workspace path outside root: {}", sessionDir);
        continue;
      }
      if (!Files.isDirectory(sessionDir)) {
        continue;
      }
      if (properties.cleanup().dryRun()) {
        log.info("[dry-run] would purge workspace dir={}", sessionDir);
        count++;
        continue;
      }
      try {
        deleteRecursively(sessionDir);
        count++;
      } catch (IOException exception) {
        log.warn(
            "Failed to delete workspace dir={}: {}", sessionDir, exception.getMessage(), exception);
      }
    }
    return count;
  }

  private void deleteRecursively(Path directory) throws IOException {
    try (Stream<Path> paths = Files.walk(directory)) {
      List<Path> ordered = paths.sorted(Comparator.reverseOrder()).toList();
      for (Path path : ordered) {
        Files.deleteIfExists(path);
      }
    }
  }
}
```

- [x] **Step 4: 接進排程**

`RetentionCleanupService` 注入 `WorkspaceRetentionService`，`scheduledCleanup()` 加一行：

```java
    int workspacesPurged =
        workspaceRetentionService.purgeStaleSessions(now.minus(properties.retention().workspace()));
```

並把它加進結尾的 log 欄位。

- [x] **Step 5: 執行測試確認通過**

Run: `./mvnw test -Dtest=WorkspaceRetentionServiceTest`
Expected: PASS

- [x] **Step 6: 全量回歸並 commit**

```bash
./mvnw test
git add backend/src/main/java/com/erd/cowork/service/WorkspaceRetentionService.java \
        backend/src/main/java/com/erd/cowork/service/RetentionCleanupService.java \
        backend/src/test/java/com/erd/cowork/service/WorkspaceRetentionServiceTest.java
git commit -m "feat: workspace 依 session 最後活動清理

workspace 先前無任何清理、只長不消。cutoff 需要 chat_session.updated_at
（在 backend DB），共享 RWX volume 讓 backend 直接刪檔，免跨服務 API。"
```

---

### Task 6: 移除 backend 的 S3 路線

**Files:**
- Delete: `backend/src/main/java/com/erd/cowork/storage/S3FileStorage.java`
- Delete: `backend/src/main/java/com/erd/cowork/config/S3StorageConfig.java`
- Delete: `backend/src/test/java/com/erd/cowork/storage/S3FileStorageTest.java`
- Delete: `backend/src/test/java/com/erd/cowork/storage/StorageConditionalRegistrationTest.java`
- Modify: `backend/src/main/java/com/erd/cowork/config/StorageProperties.java`（移除 `type`、`S3`）
- Modify: `backend/src/main/java/com/erd/cowork/storage/LocalDiskStorage.java`（移除 `@ConditionalOnProperty`）
- Modify: `backend/src/main/java/com/erd/cowork/agent/provider/analysis/LangGraphAnalysisProvider.java:205-210`
- Modify: `backend/pom.xml`（移除 `software.amazon.awssdk:s3` 與 BOM）
- Modify: `backend/src/main/resources/application.yml`

**Interfaces:**
- Consumes: Task 3 的 `store(StorageCategory, ...)` 簽名
- Produces: `FileStorage` 只剩 `LocalDiskStorage` 一個無條件註冊的實作

- [x] **Step 1: 刪除檔案**

```bash
git rm backend/src/main/java/com/erd/cowork/storage/S3FileStorage.java \
       backend/src/main/java/com/erd/cowork/config/S3StorageConfig.java \
       backend/src/test/java/com/erd/cowork/storage/S3FileStorageTest.java \
       backend/src/test/java/com/erd/cowork/storage/StorageConditionalRegistrationTest.java
```

- [x] **Step 2: `LocalDiskStorage` 拿掉條件註冊**

刪去 `@ConditionalOnProperty(...)` 整個註解與其 import，只留 `@Component`。

- [x] **Step 3: `StorageProperties` 收斂**

```java
@ConfigurationProperties(prefix = "erd.storage")
public record StorageProperties(
    String localDir, String workspaceDir, Cleanup cleanup, Retention retention) {

  public record Cleanup(String cron, boolean dryRun) {}

  public record Retention(Duration uploads, Duration workspace, Duration artifact) {}
}
```

- [x] **Step 4: `resolveSourcePath` 拿掉 s3 分支**

```java
  String resolveSourcePath(String storageKey) {
    return analysisProperties.sourceRoot() + "/" + storageKey;
  }
```

移除 `storageProperties` 欄位若已無其他用途（先 `grep -n storageProperties` 確認）。

- [x] **Step 5: `application.yml` 移除 s3 區塊與 `type`**

```yaml
erd:
  storage:
    local-dir: ${ERD_STORAGE_LOCAL_DIR:./data/files}
    workspace-dir: ${ERD_STORAGE_WORKSPACE_DIR:./data/workspace}
    cleanup:
      cron: ${ERD_STORAGE_CLEANUP_CRON:0 0 3 * * *}
      dry-run: ${ERD_STORAGE_CLEANUP_DRY_RUN:false}
    retention:
      uploads: ${ERD_STORAGE_RETENTION_UPLOADS:180d}
      workspace: ${ERD_STORAGE_RETENTION_WORKSPACE:180d}
      artifact: ${ERD_STORAGE_RETENTION_ARTIFACT:730d}
```

`application-local.yml` 若有 `erd.storage.type` 或 s3 設定，一併移除。

- [x] **Step 6: `pom.xml` 移除 AWS SDK**

刪除 `<awssdk.version>` property、`dependencyManagement` 內的 `software.amazon.awssdk:bom`、以及 `<dependency>` 的 `software.amazon.awssdk:s3`（約 129–131 行）。

- [x] **Step 7: 清掉殘留引用**

```bash
grep -rn "erd.storage.type\|ERD_STORAGE_TYPE\|awssdk\|S3FileStorage\|S3StorageConfig" backend/src backend/pom.xml
```

逐一處理（含測試的 `@TestPropertySource`）。

- [x] **Step 8: 執行測試確認通過**

Run: `./mvnw test`
Expected: 全綠

- [x] **Step 9: Commit**

```bash
git add -A backend/
git commit -m "refactor: 移除 backend S3 儲存路線

改走 PVC RWX 後 S3FileStorage 無使用者。FileStorage 介面保留為測試接縫，
只剩 LocalDiskStorage 一個無條件註冊的實作；AWS SDK 依賴一併移除。"
```

---

### Task 7: 移除 deepagent-service 的 S3 路線與 DuckDB httpfs

**Files:**
- Delete: `deepagent-service/app/engine/workspace_s3.py`
- Delete: `deepagent-service/tests/test_workspace_s3.py`（若存在）
- Create: `deepagent-service/app/engine/workspace_factory.py`（承接 `build_workspace_store`）
- Modify: `deepagent-service/app/engine/duck.py`
- Modify: `deepagent-service/app/main.py:43,236,458`
- Modify: `deepagent-service/pyproject.toml`（移除 boto3）
- Test: `deepagent-service/tests/test_duck.py`

**Interfaces:**
- Consumes: 無
- Produces: `app.engine.workspace_factory.build_workspace_store() -> WorkspaceStore`（永遠回傳 `LocalWorkspaceStore`）

- [x] **Step 1: 寫失敗測試**

加到 `deepagent-service/tests/test_duck.py`：

```python
def test_open_locked_connection_never_loads_httpfs(tmp_path, monkeypatch):
    """全 local 之後不該再有任何網路 extension 被載入。"""
    csv_path = tmp_path / "sales.csv"
    csv_path.write_text("region,amount\nnorth,10\n", encoding="utf-8")

    connection = open_locked_connection([Source(alias="sales", path=str(csv_path), file_type="csv")])

    loaded = connection.execute(
        "SELECT extension_name FROM duckdb_extensions() WHERE loaded"
    ).fetchall()
    assert all(name != "httpfs" for (name,) in loaded)
```

新建 `deepagent-service/tests/test_workspace_factory.py`：

```python
from app.engine.workspace import LocalWorkspaceStore
from app.engine.workspace_factory import build_workspace_store


def test_build_workspace_store_always_returns_local(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path))

    store = build_workspace_store()

    assert isinstance(store, LocalWorkspaceStore)
```

- [x] **Step 2: 執行測試確認失敗**

Run: `cd deepagent-service && uv run pytest tests/test_workspace_factory.py -v`
Expected: FAIL —— `app.engine.workspace_factory` 不存在

- [x] **Step 3: 建立 `workspace_factory.py`**

```python
"""WorkspaceStore 建構點。

engine 純度規則（見 workspace.py 檔頭）：僅 stdlib，LLM 框架禁止（ruff TID251 會擋）。
"""

from app.engine.workspace import LocalWorkspaceStore, WorkspaceStore


def build_workspace_store() -> WorkspaceStore:
    """每個 request 呼叫一次（不做 module 層單例）——env 值凍結在 import 期會讓測試的
    monkeypatch.setenv 失效，理由同 resolve_workspace_root()。"""
    from app.engine.workspace import resolve_workspace_root

    return LocalWorkspaceStore(resolve_workspace_root())
```

- [x] **Step 4: 改 `duck.py`**

刪除 `_s3_config()` 整個函式與 `has_s3_source` 相關分支：

```python
def open_locked_connection(
    sources: list[Source], memory_limit: str = "2GB"
) -> duckdb.DuckDBPyConnection:
    """先掛資料(materialize)、後鎖門——回傳的連線上任何 SQL 都無法再碰檔案系統/網路。
    資料源一律為本地掛載路徑（PVC），不載入任何網路 extension。"""
    _validate_memory_limit(memory_limit)
    config: dict[str, object] = {"memory_limit": memory_limit, "threads": 2}
    connection = duckdb.connect(":memory:", config=config)
    for source in sources:
        reader = _READERS.get(source.file_type)
        if reader is None:
            raise ValueError(f"unsupported file type: {source.file_type}")
        _validate_alias(source.alias)
        connection.execute(
            f'CREATE TABLE "{source.alias}" AS SELECT * FROM {reader}(?)', [source.path]
        )
    connection.execute("SET enable_external_access = false")
    connection.execute("SET lock_configuration = true")
    return connection
```

`Source.path` 的註解改為「本地掛載路徑（由 Java 端 `resolveSourcePath` 組出）」。

- [x] **Step 5: 改 `main.py` 與刪檔**

```bash
git rm deepagent-service/app/engine/workspace_s3.py
git rm --ignore-unmatch deepagent-service/tests/test_workspace_s3.py
```

`main.py:43` 的 import 改為：

```python
from app.engine.workspace_factory import build_workspace_store
```

`main.py:233` 的註解移除 `AGENT_WORKSPACE_BACKEND` 字樣。第 236、458 行的呼叫不變。

- [x] **Step 6: 移除 boto3 依賴**

`deepagent-service/pyproject.toml` 移除 `boto3`（含任何 type stub），然後 `uv lock`。

- [x] **Step 7: 清掉殘留引用**

```bash
grep -rn "AGENT_S3\|AGENT_WORKSPACE_BACKEND\|boto3\|workspace_s3\|httpfs" \
  deepagent-service/app deepagent-service/tests deepagent-service/pyproject.toml
```

- [x] **Step 8: 執行測試確認通過**

Run: `cd deepagent-service && uv run pytest && uv run ruff check .`
Expected: 全綠

- [x] **Step 9: Commit**

```bash
git add -A deepagent-service/
git commit -m "refactor: 移除 deepagent S3 workspace 與 DuckDB httpfs

RWX 共享檔案系統下 workspace 即單一 source of truth，lazy pull/turn-end
push 連同其跨 pod stale read 缺陷一併消失。DuckDB 不再載入網路 extension。"
```

---

### Task 8: compose、`.env.example` 與文件收尾

**Files:**
- Modify: `docker-compose.yml`
- Modify: `.env.example`
- Modify: `docs/architecture.md`
- Modify: `README.md`（若提及 minio profile）

**Interfaces:**
- Consumes: Task 2、6、7 的最終環境變數集合
- Produces: 無（收尾）

- [x] **Step 1: `docker-compose.yml`**

- 刪除 `minio` 與 `minio-init` 兩個 service，以及 `volumes:` 下的 `minio-data`
- **保留 `lf-minio` 與 `lf-minio-data`**（Langfuse self-host topology，`observability` profile）
- `backend` 的 environment：刪 `ERD_STORAGE_TYPE`、`ERD_STORAGE_S3_*`、`AWS_ACCESS_KEY_ID`、`AWS_SECRET_ACCESS_KEY`；加上

```yaml
      ERD_STORAGE_WORKSPACE_DIR: ${ERD_STORAGE_WORKSPACE_DIR:-/data/workspace}
      ERD_STORAGE_CLEANUP_CRON: ${ERD_STORAGE_CLEANUP_CRON:-0 0 3 * * *}
      ERD_STORAGE_CLEANUP_DRY_RUN: ${ERD_STORAGE_CLEANUP_DRY_RUN:-false}
      ERD_STORAGE_RETENTION_UPLOADS: ${ERD_STORAGE_RETENTION_UPLOADS:-180d}
      ERD_STORAGE_RETENTION_WORKSPACE: ${ERD_STORAGE_RETENTION_WORKSPACE:-180d}
      ERD_STORAGE_RETENTION_ARTIFACT: ${ERD_STORAGE_RETENTION_ARTIFACT:-730d}
```

- `backend` 的 volumes 加上 workspace（清理需要寫入權限）：

```yaml
    volumes:
      - cowork-files:/data/files
      - deepagent-workspace:/data/workspace
```

- `deepagent-service` 的 environment：刪 `AGENT_WORKSPACE_BACKEND`、`AGENT_WORKSPACE_S3_*`、`AGENT_S3_*`

- [x] **Step 2: `.env.example`**

刪除第 28–38 行的「S3-compatible 檔案儲存」整段與第 55–63 行的 `AGENT_S3_*` 段落，新增：

```bash
# ── 檔案保留與清理排程 ────────────────────────────────────────────────────────
# 上傳原始檔與 workspace 依 session 最後活動時間；artifact 依自身建立時間。
# ERD_STORAGE_CLEANUP_CRON=0 0 3 * * *   # "-" 停用排程
# ERD_STORAGE_CLEANUP_DRY_RUN=false      # true：只記錄將刪除什麼，不實際刪除
# ERD_STORAGE_RETENTION_UPLOADS=180d
# ERD_STORAGE_RETENTION_WORKSPACE=180d
# ERD_STORAGE_RETENTION_ARTIFACT=730d
```

- [x] **Step 3: `docs/architecture.md` 由「已決議」改為「已完成」**

- 對外連線總覽第 4 列：整列刪除（連線已不存在），並更新第 11–12 行的邊界定義（容器清單移除 `minio`，保留 `lf-*`）
- mermaid：刪除 `MinIO` 節點與三條虛線邊（`FileStorage -.s3 mode.->`、`DuckDBEngine -.httpfs, s3 sources.->`、`WorkspaceStore -.s3 backend.->`），`FileStorage`／`WorkspaceStore` 節點文字改為只列本地實作
- workspace 生命週期表：刪除 S3 欄，只留一欄
- 「已結案：S3 workspace 耐久性」小節：保留（決策記錄有價值），把「改走 RWX PVC」的時態由未來式改為完成式
- 「儲存後端決策」節內的「已決議移除」字樣改為「已移除」

- [x] **Step 4: 驗證 compose 可啟動**

```bash
docker compose config --quiet
docker compose --profile deepagent up -d backend deepagent-service oracle
docker compose logs backend | grep -i "retention\|storage"
docker compose down
```

Expected: `config` 無錯誤；backend 啟動無 missing property 例外。

- [x] **Step 5: 全量回歸**

```bash
./mvnw -f backend/pom.xml test
cd deepagent-service && uv run pytest && uv run ruff check . && cd ..
cd frontend && npm test -- --run && cd ..
```

Expected: 三側全綠

- [x] **Step 6: Commit**

```bash
git add -A
git commit -m "chore: compose/.env/architecture 移除 S3 殘留設定

lf-minio 屬 Langfuse self-host topology，刻意保留。backend 新增掛載
/data/workspace 以執行 workspace 清理。"
```

---

## 落地前仍待處理（不在本計畫範圍）

以下為 spec §9 列出、需向平台團隊確認後才能定案的事項，**不阻塞上述實作**：

1. RWX provisioner 型別與 2 GB CSV 順序讀實測 latency
2. CSI 是否支援線上擴容
3. 平台的 PVC 備份能力 —— spec §5 備份策略待此結果
4. 平台可提供的 RWX PVC 容量上限（規劃值 `/data/files` 2 TB、`/data/workspace` 200 GB）
5. openai/dashboard 線是否上 prod —— 若是，`ArtifactAssembler` 的 `__ERD_DATA__` 全量注入須先解（spec §3.2 條件式風險）

k8s manifest／Helm chart 本 repo 目前不存在，PVC 宣告與掛載屬部署層，不在本計畫內。

**分類用量監控刻意不做成 app 端點**：spec §3.5 要求按 `uploads/`／`artifacts/` 前綴分別監控用量。實作為應用層 endpoint 會在 2 TB volume 上走整棵目錄樹，慢且會阻塞請求執行緒。Task 3 的 key 前綴已讓兩類資料分屬不同目錄，用量交由平台層取得（volume metrics 或 node exporter 的 `du`）——這是部署層設定，非程式碼。
