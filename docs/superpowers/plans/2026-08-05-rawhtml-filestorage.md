# rawHtml 搬遷 FileStorage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 artifact 的模型原始輸出（rawHtml）從 DB CLOB 改存 FileStorage；deepagent 線（raw == assembled）不寫 raw 檔，讀取時 fallback 到 assembled 檔；retention cleanup 一併清理；同步修正 `docs/architecture.md` 的 ER diagram 與相關註解。

**Architecture:** `artifact.raw_html CLOB` 換成 `raw_html_storage_key VARCHAR2(500)`。寫入端（`AgentConversationWriter`）在 assemble 後比較 raw 與 assembled——不相等才存 raw 檔（`{artifactId}.raw.html`）；相等（deepagent 線 assemble 是 no-op）就不存，key 留 null。讀取端集中在 `ArtifactService.loadRawHtml(Artifact)`：優先讀 `rawHtmlStorageKey`，null 時 fallback `htmlStorageKey`，兩者皆 null 視為無 raw。三個既有讀者（`GET /raw`、迭代回餵、repair）與 repair 覆寫、retention cleanup 全部改走新路徑。Migration 走單一 baseline 慣例——直接改 `V1__init.sql`（尚未上 prod，已套用舊 V1 的 DB 需重建 schema，此為既有約定）。

**Tech Stack:** Java 17 / Spring Boot 3.4 / JPA / Flyway（單一 V1 baseline）/ JUnit 5 + Mockito / 既有 `FileStorage`（`LocalDiskStorage`，key 格式 `artifacts/{sessionId}/{UUID}_{safeName}`，每次 store 產生新 unique key）。

## Global Constraints

- 撰寫 Java 前先讀 `.claude/skills/java/SKILL.md` 及對應 references（spring-boot、spring-data、jpa-patterns、spring-testing、code-quality）
- 變數 NEVER 用 1–2 字元名稱；描述性命名（domain 語彙優先）
- Constructor injection（`@RequiredArgsConstructor`）；NEVER `@Autowired` field injection
- 例外放 `com.erd.cowork.exception`；拋新例外 MUST 包裝原始 cause
- IO 資源 MUST try-with-resources
- 測試命名 `methodName_condition_expectedBehavior`；controller 測試 `@WebMvcTest` + `@MockitoBean`
- google-java-format 由 hook 自動執行，勿手動調格式
- NEVER log 完整 HTML 內容
- 註解精簡：1–2 行寫目的＋做法；NEVER spec 編號/commit hash/事故敘事
- 每個 task 結束時 `./mvnw -q test`（或至少該模組相關測試）必須綠，然後 commit
- 執行 branch：`feat/rawhtml-filestorage`

## 現況地圖（實作者必讀）

`rawHtml` 目前的所有讀寫點（本 plan 會全部改掉）：

| 位置 | 動作 |
|---|---|
| `AgentConversationWriter.persistHtmlResult`（`agent/AgentConversationWriter.java:61-94`） | 生成時寫入：`artifact.setRawHtml(html)`＋assembled 檔寫 FileStorage |
| `ArtifactService.getRawHtml`（`service/ArtifactService.java:77-82`） | `GET /api/artifacts/{id}/raw` 讀 CLOB |
| `AgentOrchestrator.resolveArtifactHtml`（`agent/AgentOrchestrator.java:271-291`） | 迭代回餵：讀指定版 rawHtml，fallback 該 session 最新版 |
| `ArtifactRepairService.repairFromBrowserErrors`（`service/ArtifactRepairService.java:105-135`） | 讀 rawHtml 修復；成功後覆寫 rawHtml＋assembled 檔 |
| `RetentionCleanupService.cleanupArtifacts`（`service/RetentionCleanupService.java:111-135`） | 不碰 rawHtml（projection 特意避開 CLOB）——本 plan 讓它連 raw 檔一起清 |
| `ArtifactRepository`（`repo/ArtifactRepository.java`） | `findStaleArtifactStorageKeys` projection、`clearHtmlStorageKey` targeted update |

關鍵事實：
- deepagent 線的 HTML 已含注入資料，`ArtifactAssembler.assemble` 對它**不做資料注入**（無 `__ERD_DATA__` marker）。⚠️ 但 `head-inject.vm` 的錯誤回報 script＋字型 style 是無條件注入的，assemble 在生產環境**永遠不是位元組級 no-op**——所以「存 raw 與否」的偵測 MUST 用 marker（`ArtifactAssembler.injectsData(rawHtml)`，即 `rawHtml.contains("__ERD_DATA__")`），NEVER 用 assembled 與 raw 的等值比較（永遠不相等）。deepagent 線 fallback 讀 assembled 檔時會多出這 ~2KB serve 期 boilerplate，屬可接受取捨（內容不變，僅前綴功能性 script）
- `LocalDiskStorage.store` 每次產生 UUID 前綴的新 key（`StorageKeyUtils.buildKey`），同名檔案不會互相覆蓋 → repair 的「存新檔→刪舊 key」模式安全
- `GET /api/artifacts/{id}/raw` 語意：artifact 不存在或無 raw → 404（`NotFoundException`）；repair 對無 raw → 409（`ConflictException`）——維持不變
- `getRawHtml` 刻意不做 CDN 改寫（迭代 prompt 要引用原始 URL）——assembled 檔落地時也未改寫（改寫在 serve 時），所以 fallback 讀 assembled 檔不違反此語意

---

### Task 1: Schema 與 Entity——新增 `raw_html_storage_key`（暫時保留 `raw_html`）

舊欄位的移除放在 Task 7 sweep，讓中間每個 task 都能編譯、測試綠。

**Files:**
- Modify: `backend/src/main/resources/db/migration/V1__init.sql:41-50`
- Modify: `backend/src/main/java/com/erd/cowork/domain/Artifact.java`
- Modify: `backend/src/main/java/com/erd/cowork/repo/ArtifactRepository.java`

**Interfaces:**
- Produces: `Artifact.getRawHtmlStorageKey()` / `setRawHtmlStorageKey(String)`；`ArtifactRepository.clearRawHtmlStorageKey(String id)`；`ArtifactStorageKeyView.getRawHtmlStorageKey()`；`findStaleArtifactStorageKeys` 改為撈「任一 key 非 null」的列

- [ ] **Step 1: 改 V1__init.sql 的 artifact 表**

`artifact` 表新增一欄（`raw_html CLOB` 這一版先留著，Task 7 移除）：

```sql
CREATE TABLE artifact (
    id                   VARCHAR2(36)  PRIMARY KEY,
    session_id           VARCHAR2(36)  NOT NULL,
    title                VARCHAR2(300) NOT NULL,
    raw_html             CLOB,
    raw_html_storage_key VARCHAR2(500),
    html_storage_key     VARCHAR2(500),
    asset_profile        VARCHAR2(40),
    created_at           TIMESTAMP     NOT NULL,
    CONSTRAINT fk_artifact_session FOREIGN KEY (session_id) REFERENCES chat_session (id)
);
```

- [ ] **Step 2: Entity 加欄位**

`Artifact.java` 在 `rawHtml` 欄位後新增：

```java
  /**
   * Storage key for the raw (pre-assembly) model HTML in {@link
   * com.erd.cowork.storage.FileStorage}. Null when raw equals the assembled HTML (deepagent line);
   * readers then fall back to {@link #htmlStorageKey}.
   */
  @Column(length = 500)
  private String rawHtmlStorageKey;
```

- [ ] **Step 3: Repository——projection 與 targeted update**

`ArtifactRepository.java` 改成：

```java
  /**
   * Projects only id/keys for retention cleanup — never the full entity, so stale artifacts are
   * not materialized in heap at once.
   */
  @Query(
      "select a.id as id, a.htmlStorageKey as htmlStorageKey, "
          + "a.rawHtmlStorageKey as rawHtmlStorageKey from Artifact a "
          + "where a.createdAt < :cutoff "
          + "and (a.htmlStorageKey is not null or a.rawHtmlStorageKey is not null)")
  List<ArtifactStorageKeyView> findStaleArtifactStorageKeys(@Param("cutoff") Instant cutoff);

  /** Targeted column update; the row itself is kept for message references. */
  @Modifying
  @Transactional
  @Query("update Artifact a set a.htmlStorageKey = null where a.id = :id")
  void clearHtmlStorageKey(@Param("id") String id);

  /** Targeted column update; the row itself is kept for message references. */
  @Modifying
  @Transactional
  @Query("update Artifact a set a.rawHtmlStorageKey = null where a.id = :id")
  void clearRawHtmlStorageKey(@Param("id") String id);

  /** Narrow read projection backing {@link #findStaleArtifactStorageKeys(Instant)}. */
  interface ArtifactStorageKeyView {
    String getId();

    String getHtmlStorageKey();

    String getRawHtmlStorageKey();
  }
```

- [ ] **Step 4: 跑測試確認綠**

Run: `cd backend && ./mvnw -q test`
Expected: PASS（純加法，既有測試不受影響；若有 retention 測試 mock `ArtifactStorageKeyView` 缺新 getter 導致編譯錯，補 `getRawHtmlStorageKey()` 回傳 null 的 stub）

- [ ] **Step 5: Commit**

```bash
git add backend/src/main/resources/db/migration/V1__init.sql backend/src/main/java/com/erd/cowork/domain/Artifact.java backend/src/main/java/com/erd/cowork/repo/ArtifactRepository.java
git commit -m "feat: artifact 新增 raw_html_storage_key 欄位與 cleanup projection"
```

---

### Task 2: `ArtifactService`——raw 讀取集中點（key 優先、fallback assembled）

**Files:**
- Modify: `backend/src/main/java/com/erd/cowork/service/ArtifactService.java`
- Test: `backend/src/test/java/com/erd/cowork/service/ArtifactServiceTest.java`

**Interfaces:**
- Consumes: Task 1 的 `Artifact.getRawHtmlStorageKey()`
- Produces: `public Optional<String> loadRawHtml(Artifact artifact)`——非 null artifact 進來；`rawHtmlStorageKey` 非 null 讀該檔，否則 `htmlStorageKey` 非 null 讀該檔，兩者皆 null 回 `Optional.empty()`；`IOException` 包成 `RuntimeException`。`getRawHtml(String artifactId)` 簽名不變，改走 `loadRawHtml`，找不到 artifact 或無 raw 一律拋 `NotFoundException`（404 語意不變）

- [ ] **Step 1: 寫失敗測試**

在 `ArtifactServiceTest.java` 新增（沿用該檔既有的 mock 建構方式；`FileStorage` 已是 `ArtifactService` 的依賴之一——若不是，本 task Step 3 會加入，測試先照下列寫）：

```java
  @Test
  void getRawHtml_rawKeyPresent_readsRawFile() throws IOException {
    Artifact artifact = new Artifact();
    artifact.setRawHtmlStorageKey("artifacts/s1/uuid_a.raw.html");
    artifact.setHtmlStorageKey("artifacts/s1/uuid_a.html");
    when(artifactRepository.findById("art-1")).thenReturn(Optional.of(artifact));
    when(fileStorage.read("artifacts/s1/uuid_a.raw.html"))
        .thenReturn(new ByteArrayInputStream("<html>raw</html>".getBytes(StandardCharsets.UTF_8)));

    assertThat(artifactService.getRawHtml("art-1")).isEqualTo("<html>raw</html>");
  }

  @Test
  void getRawHtml_rawKeyNull_fallsBackToAssembledFile() throws IOException {
    Artifact artifact = new Artifact();
    artifact.setHtmlStorageKey("artifacts/s1/uuid_a.html");
    when(artifactRepository.findById("art-1")).thenReturn(Optional.of(artifact));
    when(fileStorage.read("artifacts/s1/uuid_a.html"))
        .thenReturn(
            new ByteArrayInputStream("<html>assembled</html>".getBytes(StandardCharsets.UTF_8)));

    assertThat(artifactService.getRawHtml("art-1")).isEqualTo("<html>assembled</html>");
  }

  @Test
  void getRawHtml_bothKeysNull_throwsNotFound() {
    when(artifactRepository.findById("art-1")).thenReturn(Optional.of(new Artifact()));

    assertThatThrownBy(() -> artifactService.getRawHtml("art-1"))
        .isInstanceOf(NotFoundException.class);
  }
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && ./mvnw -q test -Dtest=ArtifactServiceTest`
Expected: FAIL（`setRawHtmlStorageKey` 存在但讀取仍走 CLOB → 第一、二個測試失敗）

- [ ] **Step 3: 實作**

`ArtifactService.java`——`getRawHtml` 改寫＋新增 `loadRawHtml`（`FileStorage` 若尚未是依賴則加入 `private final FileStorage fileStorage;`，實際上 `streamWithCdnRewrite` 已在用，確認即可）：

```java
  /**
   * Returns the raw (pre-assembly) HTML for the given artifact ID. CDN URLs are intentionally NOT
   * rewritten here so iterative prompts continue to reference original LLM-generated URLs.
   *
   * @param artifactId artifact UUID
   * @return raw HTML string (unmodified)
   * @throws NotFoundException if no artifact with the given ID exists or it has no stored HTML
   */
  public String getRawHtml(String artifactId) {
    Artifact artifact =
        artifacts
            .findById(artifactId)
            .orElseThrow(() -> new NotFoundException("Artifact not found: " + artifactId));
    return loadRawHtml(artifact)
        .orElseThrow(() -> new NotFoundException("Artifact not found: " + artifactId));
  }

  /**
   * Loads the raw model HTML for an artifact from {@link FileStorage}. Falls back to the assembled
   * file when no dedicated raw file exists — the deepagent line skips the raw file because its
   * assemble step is a no-op, so both files would be identical.
   *
   * @param artifact the artifact entity (never null)
   * @return the raw HTML, or empty when the artifact has no stored HTML at all
   */
  public Optional<String> loadRawHtml(Artifact artifact) {
    String storageKey =
        artifact.getRawHtmlStorageKey() != null
            ? artifact.getRawHtmlStorageKey()
            : artifact.getHtmlStorageKey();
    if (storageKey == null) {
      return Optional.empty();
    }
    try (InputStream storageStream = fileStorage.read(storageKey)) {
      return Optional.of(new String(storageStream.readAllBytes(), StandardCharsets.UTF_8));
    } catch (IOException ioException) {
      throw new RuntimeException(
          "Failed to read raw HTML key=" + storageKey + " for artifact " + artifact.getId(),
          ioException);
    }
  }
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd backend && ./mvnw -q test -Dtest=ArtifactServiceTest`
Expected: PASS（該檔既有測試若以 `setRawHtml` 建 fixture 而失敗，改成 `setRawHtmlStorageKey` + mock `fileStorage.read`）

- [ ] **Step 5: Commit**

```bash
git add backend/src/main/java/com/erd/cowork/service/ArtifactService.java backend/src/test/java/com/erd/cowork/service/ArtifactServiceTest.java
git commit -m "feat: ArtifactService raw 讀取改走 FileStorage，無 raw 檔 fallback assembled"
```

---

### Task 3: `AgentConversationWriter`——raw != assembled 才存 raw 檔，停寫 CLOB

**Files:**
- Modify: `backend/src/main/java/com/erd/cowork/agent/AgentConversationWriter.java:61-115`
- Test: `backend/src/test/java/com/erd/cowork/agent/AgentConversationWriterTest.java`

**Interfaces:**
- Consumes: Task 1 的 `setRawHtmlStorageKey`
- Produces: raw 檔命名 `artifactId + ".raw.html"`（`StorageCategory.ARTIFACT` 同 category）；deepagent 線（assemble 回傳與輸入相等）不存 raw、key 為 null

- [ ] **Step 1: 寫失敗測試**

在 `AgentConversationWriterTest.java` 新增（沿用該檔既有 mock/TransactionTemplate 建構；`fileStorage.store` 的既有 stub 方式照抄該檔）：

```java
  @Test
  void persistHtmlResult_assembleChangesHtml_storesRawFileAndNoClob() throws IOException {
    when(artifactAssembler.assemble(eq("session-1"), eq("<html>raw</html>")))
        .thenReturn("<html>assembled</html>");
    when(fileStorage.store(eq(StorageCategory.ARTIFACT), eq("session-1"), anyString(), any()))
        .thenReturn("key-assembled", "key-raw");

    writer.persistHtmlResult("session-1", "<html>raw</html>", "[]", null, "answer", "Version 1", null);

    ArgumentCaptor<Artifact> savedArtifact = ArgumentCaptor.forClass(Artifact.class);
    verify(artifactRepository, atLeastOnce()).save(savedArtifact.capture());
    Artifact finalState = savedArtifact.getValue();
    assertThat(finalState.getRawHtmlStorageKey()).isEqualTo("key-raw");
    verify(fileStorage)
        .store(eq(StorageCategory.ARTIFACT), eq("session-1"), endsWith(".raw.html"), any());
  }

  @Test
  void persistHtmlResult_assembleIsNoOp_skipsRawFile() throws IOException {
    when(artifactAssembler.assemble(eq("session-1"), eq("<html>same</html>")))
        .thenReturn("<html>same</html>");
    when(fileStorage.store(eq(StorageCategory.ARTIFACT), eq("session-1"), anyString(), any()))
        .thenReturn("key-assembled");

    writer.persistHtmlResult("session-1", "<html>same</html>", "[]", null, "answer", "Version 1", null);

    ArgumentCaptor<Artifact> savedArtifact = ArgumentCaptor.forClass(Artifact.class);
    verify(artifactRepository, atLeastOnce()).save(savedArtifact.capture());
    assertThat(savedArtifact.getValue().getRawHtmlStorageKey()).isNull();
    verify(fileStorage, never())
        .store(eq(StorageCategory.ARTIFACT), eq("session-1"), endsWith(".raw.html"), any());
  }
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && ./mvnw -q test -Dtest=AgentConversationWriterTest`
Expected: FAIL（尚未存 raw 檔）

- [ ] **Step 3: 實作**

`persistHtmlResult` 的 artifact 寫入段改為（`artifact.setRawHtml(html)` 移除；assembled 存檔段照舊）：

```java
          Artifact artifact = new Artifact();
          artifact.setSessionId(sessionId);
          artifact.setTitle(artifactTitle);
          artifact.setAssetProfile(artifactRewriteProperties.currentProfile());
          artifact = artifacts.save(artifact);
          String artifactId = artifact.getId();

          // Store assembled HTML in FileStorage keyed by artifactId.
          byte[] htmlBytes = injectedHtml.getBytes(StandardCharsets.UTF_8);
          try (ByteArrayInputStream htmlStream = new ByteArrayInputStream(htmlBytes)) {
            String storageKey =
                fileStorage.store(
                    StorageCategory.ARTIFACT, sessionId, artifactId + ".html", htmlStream);
            artifact.setHtmlStorageKey(storageKey);
          } catch (IOException ioException) {
            throw new RuntimeException(
                "Failed to store artifact HTML for session " + sessionId, ioException);
          }

          // Raw file only when assemble injects data (marker present) — the deepagent line has no
          // marker, so readers fall back to the assembled file instead. Equality comparison is
          // unusable here: head-inject boilerplate makes assemble never a byte-level no-op.
          if (artifactAssembler.injectsData(html)) {
            byte[] rawBytes = html.getBytes(StandardCharsets.UTF_8);
            try (ByteArrayInputStream rawStream = new ByteArrayInputStream(rawBytes)) {
              String rawStorageKey =
                  fileStorage.store(
                      StorageCategory.ARTIFACT, sessionId, artifactId + ".raw.html", rawStream);
              artifact.setRawHtmlStorageKey(rawStorageKey);
            } catch (IOException ioException) {
              throw new RuntimeException(
                  "Failed to store raw artifact HTML for session " + sessionId, ioException);
            }
          }

          artifact = artifacts.save(artifact);
```

同步把方法 Javadoc 的「raw HTML (LLM output, not yet data-injected)」段落補一句 raw 檔條件式存放。

- [ ] **Step 4: 跑測試確認通過**

Run: `cd backend && ./mvnw -q test -Dtest=AgentConversationWriterTest`
Expected: PASS（既有測試若斷言 `getRawHtml()` 值，改斷言 `getRawHtmlStorageKey()`；fixture 相應調整）

- [ ] **Step 5: Commit**

```bash
git add backend/src/main/java/com/erd/cowork/agent/AgentConversationWriter.java backend/src/test/java/com/erd/cowork/agent/AgentConversationWriterTest.java
git commit -m "feat: 生成寫入改存 raw 檔（assemble 有變更才存），停寫 rawHtml CLOB"
```

---

### Task 4: `AgentOrchestrator.resolveArtifactHtml`——迭代回餵改走 `loadRawHtml`

**Files:**
- Modify: `backend/src/main/java/com/erd/cowork/agent/AgentOrchestrator.java:271-291`
- Test: `backend/src/test/java/com/erd/cowork/agent/AgentOrchestratorTest.java`

**Interfaces:**
- Consumes: Task 2 的 `ArtifactService.loadRawHtml(Artifact)`
- Produces: `resolveArtifactHtml` 行為不變（指定版不存在/非本 session → fallback 最新版；皆無 → null），僅資料來源改為 FileStorage

- [ ] **Step 1: 寫失敗測試**

`AgentOrchestratorTest.java` 中找到現有 resolveArtifactHtml／迭代相關測試（以 `setRawHtml` 建 fixture 者），新增一個走新路徑的測試（mock 建構沿用該檔既有 helper）：

```java
  @Test
  void resolveArtifactHtml_baseArtifactWithRawKey_feedsRawFileContent() {
    Artifact baseArtifact = new Artifact();
    baseArtifact.setSessionId("session-1");
    baseArtifact.setRawHtmlStorageKey("artifacts/session-1/uuid_a.raw.html");
    when(artifactRepository.findById("art-base")).thenReturn(Optional.of(baseArtifact));
    when(artifactService.loadRawHtml(baseArtifact)).thenReturn(Optional.of("<html>v1</html>"));
    // ……依該檔既有測試模式驅動迭代流程，斷言回餵內容為 "<html>v1</html>"
  }
```

（若該檔測 `resolveArtifactHtml` 是透過完整事件流間接測，照既有模式改寫；核心斷言＝回餵內容來自 `artifactService.loadRawHtml` 的回傳值。）

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && ./mvnw -q test -Dtest=AgentOrchestratorTest`
Expected: FAIL（orchestrator 尚未依賴 `ArtifactService`）

- [ ] **Step 3: 實作**

`AgentOrchestrator` 注入 `ArtifactService`（`@RequiredArgsConstructor` 加一個 `private final ArtifactService artifactService;`，確認無循環依賴——`ArtifactService` 不依賴 agent 包），`resolveArtifactHtml` 改為：

```java
  private String resolveArtifactHtml(String sessionId, String baseArtifactId) {
    if (StringUtils.hasText(baseArtifactId)) {
      var specified =
          artifacts
              .findById(baseArtifactId)
              .filter(artifact -> sessionId.equals(artifact.getSessionId()))
              .flatMap(artifactService::loadRawHtml)
              .orElse(null);
      if (specified != null) {
        return specified;
      }
      log.debug(
          "baseArtifactId {} not found or not owned by session {}; falling back to most-recent",
          baseArtifactId,
          sessionId);
    }
    return artifacts
        .findFirstBySessionIdOrderByCreatedAtDesc(sessionId)
        .flatMap(artifactService::loadRawHtml)
        .orElse(null);
  }
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd backend && ./mvnw -q test -Dtest=AgentOrchestratorTest`
Expected: PASS（既有 fixture 用 `setRawHtml` 者改為 mock `artifactService.loadRawHtml`）

- [ ] **Step 5: Commit**

```bash
git add backend/src/main/java/com/erd/cowork/agent/AgentOrchestrator.java backend/src/test/java/com/erd/cowork/agent/AgentOrchestratorTest.java
git commit -m "feat: 迭代回餵改讀 FileStorage raw 檔"
```

---

### Task 5: `ArtifactRepairService`——修復讀寫改走 raw 檔

**Files:**
- Modify: `backend/src/main/java/com/erd/cowork/service/ArtifactRepairService.java:105-153`
- Test: `backend/src/test/java/com/erd/cowork/service/ArtifactRepairServiceTest.java`

**Interfaces:**
- Consumes: Task 2 的 `loadRawHtml`；Task 1 的 `setRawHtmlStorageKey`
- Produces: 無 raw（兩 key 皆 null）→ 409 `ConflictException` 語意不變；成功修復後 assembled 新檔＋（若 raw != assembled）raw 新檔，舊 key 皆 best-effort 刪除

- [ ] **Step 1: 寫失敗測試**

`ArtifactRepairServiceTest.java` 新增／改寫（沿用該檔既有 fixture helper）：

```java
  @Test
  void repairFromBrowserErrors_success_storesNewRawFileAndDeletesOldKeys() throws IOException {
    // fixture: artifact 帶 rawHtmlStorageKey="old-raw-key"、htmlStorageKey="old-html-key"
    // mock artifactService.loadRawHtml(artifact) → Optional.of("<html>broken</html>")
    // mock repairer 成功回傳 outcome.html()="<html>fixed</html>"
    // mock assembler.assemble → "<html>fixed-assembled</html>"（與 raw 不同）
    // mock fileStorage.store → "new-html-key", "new-raw-key"（依呼叫順序）

    boolean repaired = artifactRepairService.repairFromBrowserErrors("art-1", errors);

    assertThat(repaired).isTrue();
    // 斷言 artifact.getHtmlStorageKey()=="new-html-key"、getRawHtmlStorageKey()=="new-raw-key"
    verify(fileStorage).delete("old-html-key");
    verify(fileStorage).delete("old-raw-key");
  }

  @Test
  void repairFromBrowserErrors_noStoredHtmlAtAll_throwsConflict() {
    // fixture: artifact 兩個 key 皆 null；mock loadRawHtml → Optional.empty()
    assertThatThrownBy(() -> artifactRepairService.repairFromBrowserErrors("art-1", errors))
        .isInstanceOf(ConflictException.class);
  }
```

（註解行換成該檔實際 fixture 寫法；斷言用 ArgumentCaptor 拿 `artifacts.save` 的實體。）

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && ./mvnw -q test -Dtest=ArtifactRepairServiceTest`
Expected: FAIL

- [ ] **Step 3: 實作**

`ArtifactRepairService`：注入 `ArtifactService`。`repairFromBrowserErrors` 中：

讀取段（原 105–108 行）改為：

```java
    String rawHtml =
        artifactService
            .loadRawHtml(artifact)
            .orElseThrow(
                () -> new ConflictException("Artifact has no raw HTML to repair: " + artifactId));
```

寫入段（原 122–150 行）改為：

```java
    String assembledHtml = artifactAssembler.assemble(sessionId, outcome.html());
    String oldStorageKey = artifact.getHtmlStorageKey();
    String oldRawStorageKey = artifact.getRawHtmlStorageKey();

    byte[] htmlBytes = assembledHtml.getBytes(StandardCharsets.UTF_8);
    try (ByteArrayInputStream htmlStream = new ByteArrayInputStream(htmlBytes)) {
      String newStorageKey =
          fileStorage.store(StorageCategory.ARTIFACT, sessionId, artifactId + ".html", htmlStream);
      artifact.setHtmlStorageKey(newStorageKey);
    } catch (IOException ioException) {
      throw new RuntimeException(
          "Failed to store repaired artifact HTML for artifact " + artifactId, ioException);
    }

    // Same rule as generation: a dedicated raw file only when assemble injects data.
    if (artifactAssembler.injectsData(outcome.html())) {
      byte[] rawBytes = outcome.html().getBytes(StandardCharsets.UTF_8);
      try (ByteArrayInputStream rawStream = new ByteArrayInputStream(rawBytes)) {
        String newRawStorageKey =
            fileStorage.store(
                StorageCategory.ARTIFACT, sessionId, artifactId + ".raw.html", rawStream);
        artifact.setRawHtmlStorageKey(newRawStorageKey);
      } catch (IOException ioException) {
        throw new RuntimeException(
            "Failed to store repaired raw HTML for artifact " + artifactId, ioException);
      }
    } else {
      artifact.setRawHtmlStorageKey(null);
    }

    deleteBestEffort(oldStorageKey, artifactId);
    deleteBestEffort(oldRawStorageKey, artifactId);

    artifacts.save(artifact);
    persistRepairRecord(sessionId, errors, true);
    return true;
```

新增 private helper（取代原本 inline 的 best-effort 刪除區塊）：

```java
  /** Best-effort deletion of a superseded storage file; failure only warns, never blocks. */
  private void deleteBestEffort(String storageKey, String artifactId) {
    if (storageKey == null) {
      return;
    }
    try {
      fileStorage.delete(storageKey);
    } catch (IOException ioException) {
      log.warn(
          "Failed to delete old artifact HTML key={} for artifact={}",
          storageKey,
          artifactId,
          ioException);
    }
  }
```

同步更新 class 上方 Javadoc：「A null rawHtml (artifact created before the raw-HTML column was introduced) surfaces as 409」改為「An artifact with no stored HTML at all (both storage keys null) surfaces as 409」。

- [ ] **Step 4: 跑測試確認通過**

Run: `cd backend && ./mvnw -q test -Dtest=ArtifactRepairServiceTest`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/main/java/com/erd/cowork/service/ArtifactRepairService.java backend/src/test/java/com/erd/cowork/service/ArtifactRepairServiceTest.java
git commit -m "feat: 瀏覽器修復讀寫改走 FileStorage raw 檔"
```

---

### Task 6: `RetentionCleanupService`——raw 檔一併清理

**Files:**
- Modify: `backend/src/main/java/com/erd/cowork/service/RetentionCleanupService.java:100-135`
- Test: `backend/src/test/java/com/erd/cowork/service/RetentionCleanupServiceTest.java`（若測試在別檔，grep `cleanupArtifacts` 找到既有測試檔）

**Interfaces:**
- Consumes: Task 1 的 `getRawHtmlStorageKey()` projection 與 `clearRawHtmlStorageKey`
- Produces: 兩個 key 各自獨立「檔案刪成功才清 key」；任一 key 清掉即計數一次

- [ ] **Step 1: 寫失敗測試**

既有 cleanup 測試檔新增（mock `ArtifactStorageKeyView` 照該檔既有寫法）：

```java
  @Test
  void cleanupArtifacts_staleArtifactWithRawKey_deletesBothFilesAndClearsBothKeys()
      throws IOException {
    ArtifactStorageKeyView staleView = mock(ArtifactStorageKeyView.class);
    when(staleView.getId()).thenReturn("art-1");
    when(staleView.getHtmlStorageKey()).thenReturn("html-key");
    when(staleView.getRawHtmlStorageKey()).thenReturn("raw-key");
    when(artifactRepository.findStaleArtifactStorageKeys(any())).thenReturn(List.of(staleView));

    int purgedCount = retentionCleanupService.cleanupArtifacts(Instant.now());

    assertThat(purgedCount).isEqualTo(1);
    verify(fileStorage).delete("html-key");
    verify(fileStorage).delete("raw-key");
    verify(artifactRepository).clearHtmlStorageKey("art-1");
    verify(artifactRepository).clearRawHtmlStorageKey("art-1");
  }

  @Test
  void cleanupArtifacts_htmlDeleteFails_rawStillCleanedIndependently() throws IOException {
    ArtifactStorageKeyView staleView = mock(ArtifactStorageKeyView.class);
    when(staleView.getId()).thenReturn("art-1");
    when(staleView.getHtmlStorageKey()).thenReturn("html-key");
    when(staleView.getRawHtmlStorageKey()).thenReturn("raw-key");
    when(artifactRepository.findStaleArtifactStorageKeys(any())).thenReturn(List.of(staleView));
    doThrow(new IOException("disk error")).when(fileStorage).delete("html-key");

    retentionCleanupService.cleanupArtifacts(Instant.now());

    verify(artifactRepository, never()).clearHtmlStorageKey("art-1");
    verify(fileStorage).delete("raw-key");
    verify(artifactRepository).clearRawHtmlStorageKey("art-1");
  }
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && ./mvnw -q test -Dtest=RetentionCleanupServiceTest`
Expected: FAIL

- [ ] **Step 3: 實作**

`cleanupArtifacts` 改為（Javadoc 的 CLOB 段落同步改寫——row 保留、按 key 各自清）：

```java
  public int cleanupArtifacts(Instant cutoff) {
    List<ArtifactStorageKeyView> staleArtifacts = artifactRepo.findStaleArtifactStorageKeys(cutoff);
    int count = 0;
    for (ArtifactStorageKeyView artifact : staleArtifacts) {
      if (properties.cleanup().dryRun()) {
        log.info(
            "[dry-run] would purge artifact id={} htmlKey={} rawKey={}",
            artifact.getId(),
            artifact.getHtmlStorageKey(),
            artifact.getRawHtmlStorageKey());
        count++;
        continue;
      }
      boolean purgedAny = false;
      if (artifact.getHtmlStorageKey() != null
          && deleteAndClear(
              artifact.getHtmlStorageKey(), artifact.getId(), artifactRepo::clearHtmlStorageKey)) {
        purgedAny = true;
      }
      if (artifact.getRawHtmlStorageKey() != null
          && deleteAndClear(
              artifact.getRawHtmlStorageKey(),
              artifact.getId(),
              artifactRepo::clearRawHtmlStorageKey)) {
        purgedAny = true;
      }
      if (purgedAny) {
        count++;
      }
    }
    return count;
  }

  /**
   * Deletes one storage file and clears its column only after the delete succeeded — the key is
   * the sole pointer to the file, so clearing on failure would orphan it. Returns true on success.
   */
  private boolean deleteAndClear(
      String storageKey, String artifactId, java.util.function.Consumer<String> clearColumn) {
    try {
      storage.delete(storageKey);
    } catch (IOException exception) {
      log.warn(
          "Failed to delete artifact storage key={}, keeping key for retry: {}",
          storageKey,
          exception.getMessage(),
          exception);
      return false;
    }
    clearColumn.accept(artifactId);
    return true;
  }
```

（`java.util.function.Consumer` 改成 import；method reference 傳 repo 的 clear 方法。）

- [ ] **Step 4: 跑測試確認通過**

Run: `cd backend && ./mvnw -q test -Dtest=RetentionCleanupServiceTest`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/main/java/com/erd/cowork/service/RetentionCleanupService.java backend/src/test/java/com/erd/cowork/service/RetentionCleanupServiceTest.java
git commit -m "feat: retention cleanup 一併清理 raw 檔並清 key"
```

---

### Task 7: 移除 `rawHtml` CLOB——entity、schema、殘餘引用 sweep

**Files:**
- Modify: `backend/src/main/java/com/erd/cowork/domain/Artifact.java`（刪 `@Lob private String rawHtml;`）
- Modify: `backend/src/main/resources/db/migration/V1__init.sql`（刪 `raw_html CLOB,` 一行）
- Modify: 所有殘餘引用（`grep -rn "rawHtml\|setRawHtml\|getRawHtml()" backend/src` 逐一處理；預期只剩測試 fixture 與 `ArtifactController` Javadoc）

**Interfaces:**
- Consumes: Tasks 2–6 已把所有 production 讀寫換掉
- Produces: `rawHtml` 欄位自 codebase 消失；`ArtifactController.getArtifact` Javadoc 的「(or legacy CLOB fallback)」字樣移除（該 fallback 實作上早已不存在，Javadoc 過時）

- [ ] **Step 1: 刪 entity 欄位與 DDL 欄**

`Artifact.java` 刪除 `@Lob private String rawHtml;` 與 `Lob` import；`V1__init.sql` 刪 `raw_html             CLOB,` 行。

- [ ] **Step 2: sweep 殘餘引用**

Run: `grep -rn "rawHtml" backend/src --include="*.java" | grep -v rawHtmlStorageKey`

逐一處理：測試 fixture 的 `setRawHtml(...)` 改為 `setRawHtmlStorageKey(...)`＋對應 `fileStorage.read` mock（涉及檔案見現況地圖後測試清單：`ArtifactControllerTest`、`ArtifactRepairControllerTest`、`MessageControllerTest`、`AgentOrchestratorRepairTest`、`ArtifactAssemblerTest` 等）；`ArtifactController.java:46` Javadoc 移除 "(or legacy CLOB fallback)"；`ArtifactRepository` 上残留的 CLOB 註解字樣一併清掉。

- [ ] **Step 3: 全量測試**

Run: `cd backend && ./mvnw -q test`
Expected: 全綠

- [ ] **Step 4: Commit**

```bash
git add -A backend
git commit -m "refactor: 移除 artifact.rawHtml CLOB，raw 一律走 FileStorage"
```

---

### Task 8: `docs/architecture.md`——ER diagram 與註解修正

**Files:**
- Modify: `docs/architecture.md:443`、`:448-506`（erDiagram 區塊與其後設計慣例）

**Interfaces:**
- Consumes: Tasks 1–7 落地後的實際 schema／行為
- Produces: 文件與現況一致

- [ ] **Step 1: erDiagram 區塊修正**

1. 關聯區（450–452 行）加一條軟關聯線：

```
    chat_message |o..o| artifact : "artifact_id 軟關聯（無 FK）；版本鏈由訊息序推導"
```

2. `chat_session.id` 註解（455 行）改為：

```
        VARCHAR2_36 id PK "client 指定 UUID（session upsert；Persistable，非 @UuidGenerator）"
```

3. `artifact` 區塊：`CLOB raw_html "模型原始輸出——迭代回餵與修復的來源（小、留 DB）"` 改為：

```
        VARCHAR2_500 raw_html_storage_key "模型原始輸出檔（FileStorage）；null＝無資料注入（deepagent 線），讀取 fallback html_storage_key（含 serve 期 head 注入）"
```

- [ ] **Step 2: 周邊敘述修正**

1. 443 行「版本鏈的 `raw_html`（每版 10–200KB）會讓文件無上限成長」——raw_html 改為「raw HTML」用詞並註明現已一律落 FileStorage，DB 只剩 KB 級中繼資料（維持該段落論證成立，敘述對齊現況）。
2. 499 行「ID 全為 String UUID」補「（`chat_session` 為 client 指定，其餘 `@UuidGenerator`）」。
3. 503 行 append-only 註記：「覆寫 storage 檔＋raw_html」改為「覆寫 assembled 與 raw 兩個 storage 檔（舊 key 盡力刪除）」。
4. 504 行「大 payload……不再隨版本鏈複製進 DB」段落改寫：兩線的 HTML（assembled 與 raw）全部落 FileStorage，DB 不再持有任何 HTML payload；deepagent 線 raw==assembled 只落一份檔。
5. 檢查 496 行索引清單、500 行已知限制段——不受本變更影響，維持原樣。

- [ ] **Step 3: 檢視 diff、commit**

Run: `git diff docs/architecture.md`（人工確認 mermaid 語法正確：`|o..o|` 為虛線非識別關聯）

```bash
git add docs/architecture.md
git commit -m "docs: ER diagram 補 chat_message-artifact 軟關聯，raw_html 改 storage key，修 chat_session id 註解"
```

---

## Self-Review 紀錄

- Spec 覆蓋：schema（T1/T7）、寫入含 deepagent 跳過（T3）、三個讀者（T2/T4/T5）、repair 覆寫（T5）、retention（T6）、docs（T8）——齊。
- 型別一致：`loadRawHtml(Artifact) : Optional<String>` 於 T2 定義、T4/T5 消費；`clearRawHtmlStorageKey` 於 T1 定義、T6 消費；raw 檔名 `artifactId + ".raw.html"` 於 T3/T5 一致。
- 已知妥協：raw 檔 store 失敗 rollback 時 assembled 檔可能殘留為孤兒檔（與既有「storage 寫入失敗回滾 DB、檔案孤兒」風險同級，不另處理）。
