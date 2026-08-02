# Upload Decryption Hook (UploadDecryptor) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 在 Java 上傳路徑加入一個解密接縫，讓公司環境能以內部 API 解密上傳檔，dev 環境維持零行為改變。

**Architecture:** 新增 `UploadDecryptor` 介面（`InputStream → InputStream`），由 `FileService.upload()` 在 `storage.store()` **之前**呼叫。預設綁定 `PassthroughUploadDecryptor`（原樣回傳）；公司環境以 `erd.upload.decryption.enabled=true` 切換到自家實作。同時修正 `sizeBytes` 記錄密文大小的既有缺陷，改用 `CountingInputStream` 取實際落地位元組數。

**Tech Stack:** Java 17、Spring Boot 3.4.1、Lombok、commons-io（`CountingInputStream`）、JUnit 5 + Mockito + AssertJ

**Spec:** [`docs/superpowers/specs/2026-08-02-upload-decryption-hook-design.md`](../specs/2026-08-02-upload-decryption-hook-design.md)

**Branch:** `feat/upload-decryption-hook`

## Global Constraints

- Java 17；NEVER 使用 18+ API
- 一律 constructor injection；NEVER `@Autowired` field injection
- 使用 `@RequiredArgsConstructor` 產生 constructor，不手寫 boilerplate
- 變數／參數 NEVER 用 1–2 字元名稱；一律描述性單詞
- 類別命名分類法：`*Decryptor` 為 Spring bean，MUST 有 stereotype 註解，NEVER 用 `new` 建立（測試除外）
- NEVER 空的 catch block；拋新例外 MUST 包裝原始 cause
- 所有 IO 資源 MUST 用 try-with-resources
- 測試方法命名：`methodName_condition_expectedBehavior`
- 日誌 NEVER 記錄檔案內容、金鑰或完整 prompt；僅記檔名／長度／計數
- google-java-format 由 Claude hook 自動執行，**勿手動調整格式風格**
- 每個 task 結束前 MUST 跑 `./mvnw test` 全綠才 commit

---

### Task 1: UploadDecryptor 介面與 passthrough 預設實作

**Files:**
- Create: `backend/src/main/java/com/erd/cowork/storage/UploadDecryptor.java`
- Create: `backend/src/main/java/com/erd/cowork/storage/PassthroughUploadDecryptor.java`
- Test: `backend/src/test/java/com/erd/cowork/storage/PassthroughUploadDecryptorTest.java`

**Interfaces:**
- Consumes: 無（本 task 為起點）
- Produces: `UploadDecryptor.decrypt(InputStream ciphertext, String originalFilename) throws IOException` → `InputStream`；Spring bean `PassthroughUploadDecryptor` 實作之。Task 2 會把 `UploadDecryptor` 注入 `FileService`。

- [x] **Step 1: 寫失敗測試**

建立 `backend/src/test/java/com/erd/cowork/storage/PassthroughUploadDecryptorTest.java`：

```java
package com.erd.cowork.storage;

import static org.assertj.core.api.Assertions.assertThat;

import java.io.ByteArrayInputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import org.junit.jupiter.api.Test;

class PassthroughUploadDecryptorTest {

  private final PassthroughUploadDecryptor decryptor = new PassthroughUploadDecryptor();

  @Test
  void decrypt_anyContent_returnsBytesUnchanged() throws Exception {
    byte[] original = "col\n1\n".getBytes(StandardCharsets.UTF_8);

    try (InputStream result = decryptor.decrypt(new ByteArrayInputStream(original), "data.csv")) {
      assertThat(result.readAllBytes()).isEqualTo(original);
    }
  }

  @Test
  void decrypt_emptyContent_returnsEmptyStream() throws Exception {
    try (InputStream result = decryptor.decrypt(new ByteArrayInputStream(new byte[0]), "e.csv")) {
      assertThat(result.readAllBytes()).isEmpty();
    }
  }
}
```

- [x] **Step 2: 執行測試確認失敗**

Run: `cd backend && ./mvnw -q test -Dtest=PassthroughUploadDecryptorTest`
Expected: 編譯失敗（`PassthroughUploadDecryptor` 不存在）

- [x] **Step 3: 建立介面**

建立 `backend/src/main/java/com/erd/cowork/storage/UploadDecryptor.java`：

```java
package com.erd.cowork.storage;

import java.io.IOException;
import java.io.InputStream;

/**
 * Decrypts an uploaded file before it is written to storage.
 *
 * <p>Decryption MUST happen before {@link FileStorage#store}, not lazily on read: deepagent-service
 * points DuckDB at the stored file path directly (it never goes through {@link FileStorage#read}),
 * so the bytes at rest have to be plaintext or the Python side would need a second decryption
 * implementation.
 *
 * <p>The contract is stream-in/stream-out so that an implementation whose backing API cannot stream
 * may buffer internally — that choice stays inside the implementation instead of forcing every
 * caller to hold a whole file (uploads reach 2GB) in memory.
 */
public interface UploadDecryptor {

  /**
   * Returns a plaintext stream for {@code ciphertext}.
   *
   * @param ciphertext the uploaded bytes as received
   * @param originalFilename the client-supplied filename, for implementations that key off it
   * @return a stream of plaintext bytes; closing it MUST NOT be the caller's only cleanup path for
   *     resources the implementation owns
   * @throws IOException when decryption fails; the upload is then aborted and any partially stored
   *     object is cleaned up by the caller
   */
  InputStream decrypt(InputStream ciphertext, String originalFilename) throws IOException;
}
```

- [x] **Step 4: 建立 passthrough 實作**

建立 `backend/src/main/java/com/erd/cowork/storage/PassthroughUploadDecryptor.java`：

```java
package com.erd.cowork.storage;

import java.io.InputStream;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;

/**
 * Default {@link UploadDecryptor}: returns the upload untouched.
 *
 * <p>Active unless {@code erd.upload.decryption.enabled=true}, so environments without an internal
 * decryption API (local dev, this repo's docker stacks) behave exactly as before.
 */
@Component
@ConditionalOnProperty(
    prefix = "erd.upload.decryption",
    name = "enabled",
    havingValue = "false",
    matchIfMissing = true)
public class PassthroughUploadDecryptor implements UploadDecryptor {

  @Override
  public InputStream decrypt(InputStream ciphertext, String originalFilename) {
    return ciphertext;
  }
}
```

- [x] **Step 5: 執行測試確認通過**

Run: `cd backend && ./mvnw -q test -Dtest=PassthroughUploadDecryptorTest`
Expected: PASS（2 個測試）

- [x] **Step 6: 全套測試**

Run: `cd backend && ./mvnw test`
Expected: BUILD SUCCESS，0 failures

- [x] **Step 7: Commit**

```bash
git add backend/src/main/java/com/erd/cowork/storage/UploadDecryptor.java \
        backend/src/main/java/com/erd/cowork/storage/PassthroughUploadDecryptor.java \
        backend/src/test/java/com/erd/cowork/storage/PassthroughUploadDecryptorTest.java
git commit -m "feat(backend): add UploadDecryptor seam with passthrough default"
```

---

### Task 2: 接線進 FileService

**Files:**
- Modify: `backend/src/main/java/com/erd/cowork/service/FileService.java`（欄位宣告區、`upload()` 的 store 區塊）
- Modify: `backend/src/test/java/com/erd/cowork/service/FileServiceUploadTest.java`（建構子多一個參數 + 新增測試）
- Modify: `backend/src/test/java/com/erd/cowork/service/FileServiceDeleteTest.java`（建構子多一個參數）

**Interfaces:**
- Consumes: Task 1 的 `UploadDecryptor.decrypt(InputStream, String)`
- Produces: `FileService` 建構子新增第 9 個參數 `UploadDecryptor decryptor`（由 `@RequiredArgsConstructor` 依欄位順序產生，**加在 `sessionRepository` 之後**）。Task 3 會再改同一段 store 區塊。

- [x] **Step 1: 寫失敗測試**

在 `FileServiceUploadTest.java` 加入欄位與測試。先在 class 頂端（`FileService service;` 之上）加一個可捕捉落地內容的欄位：

```java
  /** Captures what FileService actually handed to storage, so tests can assert on the bytes. */
  String storedContent;
```

在 `setUp()` 中，把既有的 `storage.store` stub **換成**會真正消費串流的版本（`CountingInputStream` 在 Task 3 才需要真實計數，這裡先確保串流被讀完）：

```java
    when(storage.store(eq(StorageCategory.UPLOAD), anyString(), anyString(), any()))
        .thenAnswer(
            invocation -> {
              InputStream suppliedStream = invocation.getArgument(3);
              storedContent = new String(suppliedStream.readAllBytes(), StandardCharsets.UTF_8);
              return "storage-key";
            });
```

新增 import：`java.io.InputStream`、`com.erd.cowork.storage.UploadDecryptor`。

新增測試方法：

```java
  @Test
  void upload_decryptorTransformsContent_storesDecryptedBytes() {
    ChatSession session = new ChatSession();
    session.setId("session-1");
    session.setUserId("user-1");
    when(sessionGuard.loadOrCreateOwned("session-1")).thenReturn(session);

    MockMultipartFile upload =
        new MockMultipartFile(
            "file", "data.csv", "text/csv", "ENC:col\n1\n".getBytes(StandardCharsets.UTF_8));

    service.upload("session-1", List.of(upload));

    assertThat(storedContent).isEqualTo("col\n1\n");
  }
```

並把 `setUp()` 建立 service 的地方改成注入一個「去掉 `ENC:` 前綴」的 fake decryptor：

```java
    UploadDecryptor stripPrefixDecryptor =
        (ciphertext, originalFilename) ->
            new ByteArrayInputStream(
                new String(ciphertext.readAllBytes(), StandardCharsets.UTF_8)
                    .replace("ENC:", "")
                    .getBytes(StandardCharsets.UTF_8));

    service =
        new FileService(
            sessionGuard,
            files,
            storage,
            parsing,
            limits,
            mapper,
            transactionTemplate,
            sessionRepository,
            stripPrefixDecryptor);
```

- [x] **Step 2: 執行測試確認失敗**

Run: `cd backend && ./mvnw -q test -Dtest=FileServiceUploadTest`
Expected: 編譯失敗（`FileService` 建構子只有 8 個參數）

- [x] **Step 3: 修改 FileService**

在 `FileService` 的欄位區最後（`sessionRepository` 之後）加入：

```java
  private final UploadDecryptor decryptor;
```

新增 import：`com.erd.cowork.storage.UploadDecryptor`。

把 `upload()` 內的 store 區塊由：

```java
        try (InputStream in = upload.getInputStream()) {
          storageKey = storage.store(StorageCategory.UPLOAD, sessionId, filename, in);
        } catch (IOException exception) {
          throw new UncheckedIOException("failed to store upload: " + filename, exception);
        }
```

改為：

```java
        // Decrypt before storing, never on read: deepagent-service points DuckDB at this file
        // path directly, so the bytes at rest must already be plaintext.
        try (InputStream in = upload.getInputStream();
            InputStream plaintext = decryptor.decrypt(in, filename)) {
          storageKey = storage.store(StorageCategory.UPLOAD, sessionId, filename, plaintext);
        } catch (IOException exception) {
          throw new UncheckedIOException("failed to store upload: " + filename, exception);
        }
```

- [x] **Step 4: 修正 FileServiceDeleteTest 建構子**

在 `FileServiceDeleteTest.java` 的 `setUp()` 中，把建構子呼叫改為多帶一個 passthrough（delete 路徑不解密，給什麼都行）：

```java
    service =
        new FileService(
            sessionGuard,
            files,
            storage,
            parsing,
            limits,
            mapper,
            transactionTemplate,
            sessionRepository,
            (ciphertext, originalFilename) -> ciphertext);
```

- [x] **Step 5: 執行測試確認通過**

Run: `cd backend && ./mvnw -q test -Dtest=FileServiceUploadTest+FileServiceDeleteTest`
Expected: PASS

- [x] **Step 6: 全套測試**

Run: `cd backend && ./mvnw test`
Expected: BUILD SUCCESS，0 failures

- [x] **Step 7: Commit**

```bash
git add backend/src/main/java/com/erd/cowork/service/FileService.java \
        backend/src/test/java/com/erd/cowork/service/FileServiceUploadTest.java \
        backend/src/test/java/com/erd/cowork/service/FileServiceDeleteTest.java
git commit -m "feat(backend): decrypt uploads before storing them"
```

---

### Task 3: sizeBytes 改記實際落地位元組數

**Files:**
- Modify: `backend/pom.xml`（顯式宣告 commons-io）
- Modify: `backend/src/main/java/com/erd/cowork/service/FileService.java`（store 區塊 + `setSizeBytes`）
- Modify: `backend/src/test/java/com/erd/cowork/service/FileServiceUploadTest.java`（新增測試）

**Interfaces:**
- Consumes: Task 2 完成的 store 區塊（含 `decryptor.decrypt(in, filename)` 呼叫）
- Produces: `UploadedFile.sizeBytes` 語意由「multipart 密文大小」改為「實際寫入 storage 的明文位元組數」。無新增公開方法。

**背景**：`entity.setSizeBytes(upload.getSize())` 記的是 multipart 的密文大小。解密啟用後，DB 的 `size_bytes` 與磁碟實際大小、以及 session 5GB 配額累計都會失準。

- [x] **Step 1: 寫失敗測試**

在 `FileServiceUploadTest.java` 新增（需 import `org.mockito.ArgumentCaptor` 與 `com.erd.cowork.domain.UploadedFile`——後者已存在）：

```java
  @Test
  void upload_decryptionChangesLength_recordsDecryptedByteCount() {
    ChatSession session = new ChatSession();
    session.setId("session-1");
    session.setUserId("user-1");
    when(sessionGuard.loadOrCreateOwned("session-1")).thenReturn(session);

    // 密文 10 bytes（"ENC:col\n1\n"），解密後 6 bytes（"col\n1\n"）——兩者必須不同才驗得出來。
    MockMultipartFile upload =
        new MockMultipartFile(
            "file", "data.csv", "text/csv", "ENC:col\n1\n".getBytes(StandardCharsets.UTF_8));
    assertThat(upload.getSize()).isEqualTo(10L);

    service.upload("session-1", List.of(upload));

    ArgumentCaptor<UploadedFile> savedEntity = ArgumentCaptor.forClass(UploadedFile.class);
    verify(files).save(savedEntity.capture());
    assertThat(savedEntity.getValue().getSizeBytes()).isEqualTo(6L);
  }
```

- [x] **Step 2: 執行測試確認失敗**

Run: `cd backend && ./mvnw -q test -Dtest=FileServiceUploadTest#upload_decryptionChangesLength_recordsDecryptedByteCount`
Expected: FAIL — expected 6L but was 10L（目前記的是密文大小）

- [x] **Step 3: pom 顯式宣告 commons-io**

`commons-io` 目前是 poi-ooxml 帶進來的傳遞依賴。因為要直接 import `CountingInputStream`，MUST 顯式宣告，避免上游換依賴時無聲斷裂。在 `backend/pom.xml` 的 `<dependencies>` 內加入：

```xml
    <!-- CountingInputStream：計算解密後實際寫入 storage 的位元組數。此前為 poi 的傳遞依賴，
         因為直接使用而顯式宣告。 -->
    <dependency>
      <groupId>commons-io</groupId>
      <artifactId>commons-io</artifactId>
      <version>2.17.0</version>
    </dependency>
```

- [x] **Step 4: 修改 FileService**

新增 import：`org.apache.commons.io.input.CountingInputStream`。

把 Task 2 完成的 store 區塊改為：

```java
        String storageKey;
        long storedBytes;
        FileProfile profile;
        // Decrypt before storing, never on read: deepagent-service points DuckDB at this file
        // path directly, so the bytes at rest must already be plaintext. The counting wrapper
        // records the post-decryption length — upload.getSize() is the ciphertext size and would
        // desync sizeBytes (and the session quota) from what is actually on disk.
        try (InputStream in = upload.getInputStream();
            InputStream plaintext = decryptor.decrypt(in, filename);
            CountingInputStream counting = new CountingInputStream(plaintext)) {
          storageKey = storage.store(StorageCategory.UPLOAD, sessionId, filename, counting);
          storedBytes = counting.getByteCount();
        } catch (IOException exception) {
          throw new UncheckedIOException("failed to store upload: " + filename, exception);
        }
```

注意：原本的 `String storageKey;` 與 `FileProfile profile;` 宣告在 try 之前，改動後 `long storedBytes;` 一併宣告在該處；`getByteCount()` MUST 在 try 區塊**內**取得。

再把建 entity 的那行：

```java
        entity.setSizeBytes(upload.getSize());
```

改為：

```java
        entity.setSizeBytes(storedBytes);
```

- [x] **Step 5: 執行測試確認通過**

Run: `cd backend && ./mvnw -q test -Dtest=FileServiceUploadTest`
Expected: PASS（3 個測試）

- [x] **Step 6: 全套測試**

Run: `cd backend && ./mvnw test`
Expected: BUILD SUCCESS，0 failures

- [x] **Step 7: Commit**

```bash
git add backend/pom.xml \
        backend/src/main/java/com/erd/cowork/service/FileService.java \
        backend/src/test/java/com/erd/cowork/service/FileServiceUploadTest.java
git commit -m "fix(backend): record post-decryption byte count as sizeBytes"
```

---

### Task 4: 解密失敗的清理行為測試

**Files:**
- Modify: `backend/src/test/java/com/erd/cowork/service/FileServiceUploadTest.java`

**Interfaces:**
- Consumes: Task 2／3 完成的 `FileService`
- Produces: 無新程式碼，僅補上失敗路徑的迴歸保護

**背景**：spec 要求解密失敗時不得留下孤兒 storage 物件。此行為由既有的 `catch (RuntimeException)` 清理路徑提供，本 task 用測試把它釘住。

- [x] **Step 1: 寫測試**

在 `FileServiceUploadTest.java` 新增（需 import `java.io.IOException`、`org.junit.jupiter.api.Assertions.assertThrows` 或 AssertJ 的 `assertThatThrownBy`，以及 `java.io.UncheckedIOException`）：

```java
  @Test
  void upload_decryptionFails_abortsAndLeavesNoStoredObject() throws Exception {
    ChatSession session = new ChatSession();
    session.setId("session-1");
    session.setUserId("user-1");
    when(sessionGuard.loadOrCreateOwned("session-1")).thenReturn(session);

    FileService failingService =
        new FileService(
            sessionGuard,
            files,
            storage,
            parsing,
            limits,
            mapper,
            transactionTemplate,
            sessionRepository,
            (ciphertext, originalFilename) -> {
              throw new IOException("decryption API unavailable");
            });

    MockMultipartFile upload =
        new MockMultipartFile(
            "file", "data.csv", "text/csv", "ENC:col\n1\n".getBytes(StandardCharsets.UTF_8));

    assertThatThrownBy(() -> failingService.upload("session-1", List.of(upload)))
        .isInstanceOf(UncheckedIOException.class)
        .hasMessageContaining("data.csv");

    // 解密在 store 之前就失敗，因此不該有任何物件被寫入，也就沒有東西需要清理。
    verify(storage, never()).store(any(), anyString(), anyString(), any());
    verify(files, never()).save(any(UploadedFile.class));
  }
```

新增 static import：`org.assertj.core.api.Assertions.assertThatThrownBy`、`org.mockito.Mockito.never`。

- [x] **Step 2: 執行測試**

Run: `cd backend && ./mvnw -q test -Dtest=FileServiceUploadTest#upload_decryptionFails_abortsAndLeavesNoStoredObject`
Expected: PASS（行為已由 Task 2 的實作提供，本測試為迴歸保護）

若失敗，檢查 `decrypt()` 拋出的 `IOException` 是否確實落在 store 的 try-with-resources 內、被同一個 `catch (IOException)` 包成 `UncheckedIOException`。

- [x] **Step 3: 全套測試**

Run: `cd backend && ./mvnw test`
Expected: BUILD SUCCESS，0 failures

- [x] **Step 4: Commit**

```bash
git add backend/src/test/java/com/erd/cowork/service/FileServiceUploadTest.java
git commit -m "test(backend): pin decryption-failure cleanup behaviour"
```

---

### Task 5: 設定說明與文件

**Files:**
- Modify: `backend/src/main/resources/application.yml`（`erd.upload` 區塊加註解）
- Modify: `.env.example`（[0] 共用區）
- Modify: `docs/architecture.md`（上傳流程說明）

**Interfaces:**
- Consumes: Task 1 的設定鍵 `erd.upload.decryption.enabled`
- Produces: 無程式碼

- [x] **Step 1: application.yml 加註解**

在 `backend/src/main/resources/application.yml` 的 `erd.upload` 區塊末尾（`sample-rows` 之後）加入：

```yaml
    # 公司環境的上傳檔為加密檔，需在落地前呼叫內部 API 解密。
    # true 時 MUST 另外提供一個 UploadDecryptor 實作 bean；未設或 false 走 passthrough。
    decryption:
      enabled: ${ERD_UPLOAD_DECRYPTION_ENABLED:false}
```

注意：`UploadProperties` record 不需要新增欄位——`@ConfigurationProperties` 預設
`ignoreUnknownFields = true`，此鍵由 `@ConditionalOnProperty` 直接讀取。

- [x] **Step 2: .env.example 補上變數**

在 `.env.example` 的「[0] 共用設定」區、`BACKEND_PORT` 之後加入：

```
# 上傳檔解密（公司環境）：true 時每個上傳檔在落地前會先過 UploadDecryptor。
# ⚠️ 設為 true 前 MUST 先提供公司環境的 UploadDecryptor 實作 bean，否則啟動時找不到 bean 會失敗。
# ERD_UPLOAD_DECRYPTION_ENABLED=false
```

- [x] **Step 3: architecture.md 補一段**

在 `docs/architecture.md` 的「檔案 alias 機制」章節**之前**，新增一節：

```markdown
## 上傳檔解密掛鉤（UploadDecryptor）

公司環境的上傳檔為加密檔。`FileService.upload()` 在 `storage.store()` **之前**呼叫
`UploadDecryptor.decrypt(InputStream, String)`，因此**落地的位元組一律是明文**。

**為什麼不能改成「讀取時才解密」**：deepagent-service 的 DuckDB 直接讀共用 volume 上的檔案
（`read_csv_auto(path)`，路徑由 `LangGraphAnalysisProvider.resolveSourcePath` 組出），
不經過 Java 的 `FileStorage.read()`——密文落地會讓 Python 端讀到亂碼，除非再實作一次解密。

介面刻意採 `InputStream → InputStream`：實作若無法串流可在內部自行 buffer，不必讓呼叫端
把 2GB 檔案讀進記憶體。預設 `PassthroughUploadDecryptor` 原樣回傳；
`erd.upload.decryption.enabled=true` 時改綁公司環境的實作。

`uploaded_file.size_bytes` 記錄的是**解密後**實際寫入 storage 的位元組數（`CountingInputStream`
計得），非 multipart 的密文大小。上傳上限檢查仍以密文大小為準——它在讀取任何位元組前就執行，
若移到解密後，超大檔會變成「必須先完整解密才能被拒絕」，反而放大 DoS 面。
```

- [x] **Step 4: 驗證後端仍可啟動**

Run: `cd backend && ./mvnw -q test`
Expected: BUILD SUCCESS（確認新增的 yml 區塊不會破壞 context 載入）

- [x] **Step 5: Commit**

```bash
git add backend/src/main/resources/application.yml .env.example docs/architecture.md
git commit -m "docs: document the upload decryption hook and its config key"
```

---

## 完成後的驗收

- [x] `cd backend && ./mvnw test` 全綠（基準 532，本計畫新增 5 個測試 → 應為 537）
- [x] `git log --oneline` 顯示 5 個 task commit
- [x] 確認 dev 行為零改變：未設 `ERD_UPLOAD_DECRYPTION_ENABLED` 時走 `PassthroughUploadDecryptor`
- [ ] 開 PR 併回 master（gate：`./mvnw test` 全綠 + opus 終審）

## 公司環境接手指引（不在本計畫範圍）

在公司 repo／環境提供一個實作即可，無需改動本計畫產出的任何檔案：

```java
@Component
@ConditionalOnProperty(prefix = "erd.upload.decryption", name = "enabled", havingValue = "true")
@RequiredArgsConstructor
public class InternalApiUploadDecryptor implements UploadDecryptor {

  private final SomeInternalApiClient client;   // constructor injection

  @Override
  public InputStream decrypt(InputStream ciphertext, String originalFilename) throws IOException {
    // 可串流 → 直接包裝；只能整份收 → 在此 buffer；吃檔案路徑 → 在此落暫存檔。
    // 三種都不需要改 UploadDecryptor 介面或任何呼叫端。
  }
}
```

並設定 `ERD_UPLOAD_DECRYPTION_ENABLED=true`。
