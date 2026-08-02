# 公司環境：`UploadDecryptor` 實作指引

**這份文件寫給在公司環境接手實作的 AI agent（sonnet / opus）與人類工程師。**
你不需要讀懂整個 repo，也不需要理解上游的設計討論——這裡有你需要的全部。

**你的任務**：新增**一個類別**，實作 `UploadDecryptor` 介面，呼叫公司內部 API 解密上傳檔。
**你不需要、也不應該**修改介面、`FileService`、或任何既有檔案。

---

## 0. 先確認你在對的地方

```bash
# 介面應該已經存在。若不存在，代表 feat/upload-decryption-hook 尚未 merge 進來。
ls backend/src/main/java/com/erd/cowork/storage/UploadDecryptor.java
ls backend/src/main/java/com/erd/cowork/storage/PassthroughUploadDecryptor.java
```

背景（一句話）：使用者上傳的檔案在公司環境是加密的，必須在**寫入儲存體之前**解密，
因為下游的 deepagent-service 會用 DuckDB **直接讀磁碟上的檔案**，讀到密文就是亂碼。

---

## 1. 介面契約（照抄，勿改）

```java
public interface UploadDecryptor {
  InputStream decrypt(InputStream ciphertext, String originalFilename) throws IOException;
}
```

呼叫端長這樣（`FileService.upload()`，你不會改到它）。順序是**解密 → 正規化 → 落地**——
中間多了一層 `UploadNormalizer`，它把 xlsx 轉成 CSV（deepagent 的 DuckDB 沒有 xlsx reader），
csv 則原樣複製；兩者都先落一份暫存檔，再串進 storage：

```java
NormalizedUpload normalized = null;
try {
  try (InputStream in = upload.getInputStream();
      InputStream plaintext = decryptor.decrypt(in, filename)) {
    normalized = normalizer.normalize(plaintext, filename);   // 你的明文在這裡被讀完
  } catch (IOException exception) {
    throw new UncheckedIOException("failed to normalize upload: " + filename, exception);
  }
  storedType = normalized.type();
  try (InputStream content =
          Files.newInputStream(normalized.content(), StandardOpenOption.DELETE_ON_CLOSE);
      CountingInputStream counting = new CountingInputStream(content)) {
    storageKey = storage.store(StorageCategory.UPLOAD, sessionId, filename, counting);
    storedKeys.add(storageKey);
    storedBytes = counting.getByteCount();
  } catch (IOException exception) {
    throw new UncheckedIOException("failed to store upload: " + filename, exception);
  }
  // ... 接著讀回落地檔做 profiling
} finally {
  if (normalized != null) {
    deleteNormalizedTempFileQuietly(normalized.content());
  }
}
```

從這段可以推出四件你必須遵守的事：

| # | 規則 | 為什麼 |
|---|---|---|
| 1 | 回傳的串流 `close()` **MUST 冪等** | 它在 try-with-resources 裡，且若你回傳傳入的 `ciphertext` 本身，同一個物件會被關閉多次 |
| 2 | 若你包裝 `ciphertext`，你的 wrapper 關閉 delegate **MUST 能承受重複關閉** | 呼叫端也會獨立關閉 `ciphertext` |
| 3 | 解密失敗 **MUST 拋 `IOException`**（不要回 null、不要回空串流） | 呼叫端靠這個中止上傳並清理已落地的檔案 |
| 4 | 若你把明文暫存到檔案，**刪除該暫存檔是你的責任** | 呼叫端不知道它存在 |

**你的串流會被完整讀到底（由 `UploadNormalizer` 讀），不能只支援部分讀取。**
`close()` **允許**拋 `IOException`（呼叫端已能在該路徑清掉自己的暫存檔），但仍以不拋為佳。

**`size_bytes` 記的是「正規化之後落地的位元組數」，不是你回傳的明文長度。**
csv 上傳時兩者相同；**xlsx 上傳時 `size_bytes` 是轉出的 CSV 長度**，會明顯小於或大於你回傳的
xlsx 明文大小。所以驗收時不要拿「明文大小 == `size_bytes`」當通則——只對 csv 成立。
但回傳的仍必須是**完整明文**，不能多包一層 header 或少截尾，否則 `UploadNormalizer` 解不開。

---

## 2. 你的實作骨架

檔案位置：`backend/src/main/java/com/erd/cowork/storage/InternalApiUploadDecryptor.java`
（類別名可自訂，但 **MUST 以 `Decryptor` 結尾**——這個 repo 的命名分類法規定 `*Decryptor` 是 Spring bean）

```java
package com.erd.cowork.storage;

import java.io.IOException;
import java.io.InputStream;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;

/**
 * Decrypts uploads via the internal decryption API.
 *
 * <p>Active only when {@code erd.upload.decryption.enabled=true}; otherwise
 * {@link PassthroughUploadDecryptor} handles uploads unchanged.
 */
@Slf4j
@Component
@ConditionalOnProperty(
    prefix = "erd.upload.decryption",
    name = "enabled",
    havingValue = "true")
@RequiredArgsConstructor
public class InternalApiUploadDecryptor implements UploadDecryptor {

  private final DecryptionApiClient decryptionApiClient;   // constructor injection

  @Override
  public InputStream decrypt(InputStream ciphertext, String originalFilename) throws IOException {
    // 見第 3 節：依你們 API 的形式三選一
  }
}
```

`@ConditionalOnProperty(..., havingValue = "true")` 這行**不可省略**：沒有它，
你的 bean 與 `PassthroughUploadDecryptor` 會同時存在，Spring 會因為找到兩個
`UploadDecryptor` 候選而啟動失敗。

---

## 3. 依公司 API 的形式，三選一

**先搞清楚公司解密 API 是哪一種**，再照對應的模式寫。介面設計成串流進串流出，
就是為了讓這三種都不用改介面。

### 形式 A：API 可以串流（最理想）

例如回傳 chunked response，或有本地 SDK 能邊讀邊解。

```java
  @Override
  public InputStream decrypt(InputStream ciphertext, String originalFilename) throws IOException {
    try {
      return decryptionApiClient.openDecryptedStream(ciphertext, originalFilename);
    } catch (SomeApiException exception) {
      throw new IOException("decryption failed for " + originalFilename, exception);
    }
  }
```

記憶體用量與檔案大小無關。**若做得到就選這個**——上傳檔上限是 CSV 2GB。

### 形式 B：API 只能收整份 body

```java
  @Override
  public InputStream decrypt(InputStream ciphertext, String originalFilename) throws IOException {
    try {
      byte[] encryptedBytes = ciphertext.readAllBytes();      // ⚠️ 見下方警告
      byte[] plaintextBytes = decryptionApiClient.decrypt(encryptedBytes);
      return new ByteArrayInputStream(plaintextBytes);
    } catch (SomeApiException exception) {
      throw new IOException("decryption failed for " + originalFilename, exception);
    }
  }
```

> ⚠️ **這會把整份檔案讀進 heap，2GB 的 CSV 會 OOM。**
> 選這個模式時 MUST 一併處理：
> 1. 確認公司環境實際的檔案大小上限，並把 `ERD_UPLOAD_MAX_CSV_BYTES` 之類的上限
>    （見 `application.yml` 的 `erd.upload.max-csv-bytes`）調到 heap 撐得住的值；或
> 2. 改用形式 C（落暫存檔）避開 heap。
>
> `ByteArrayInputStream.close()` 是 no-op，天然滿足冪等要求。

### 形式 C：API 吃檔案路徑，或你需要避開 heap

```java
  @Override
  public InputStream decrypt(InputStream ciphertext, String originalFilename) throws IOException {
    Path encryptedTempFile = Files.createTempFile("erd-enc-", ".tmp");
    Path plaintextTempFile = null;
    try {
      Files.copy(ciphertext, encryptedTempFile, StandardCopyOption.REPLACE_EXISTING);
      plaintextTempFile = decryptionApiClient.decryptToFile(encryptedTempFile);
      // 串流讀完即刪：DELETE_ON_CLOSE 讓呼叫端關閉串流時自動清掉明文暫存檔
      return Files.newInputStream(plaintextTempFile, StandardOpenOption.DELETE_ON_CLOSE);
    } catch (SomeApiException exception) {
      deleteQuietly(plaintextTempFile);
      throw new IOException("decryption failed for " + originalFilename, exception);
    } finally {
      deleteQuietly(encryptedTempFile);   // 密文暫存檔一定要刪
    }
  }
```

> ⚠️ **明文暫存檔是敏感資料。** 用 `DELETE_ON_CLOSE`（或等效機制）確保串流關閉即刪，
> 且失敗路徑也要刪。暫存目錄的權限要能擋住其他使用者。
>
> `Files.newInputStream` 回傳的串流 `close()` 是冪等的，滿足契約。

---

## 4. 三個一定要知道的陷阱

### 4.1 示範資料集會送**明文**進來

`SampleDatasetService` 會把 repo 內建的示範 CSV（**未加密**）走同一條上傳鏈，
所以你的 `decrypt()` 會收到明文。

**你的實作 MUST 讓未加密內容原樣通過，不能直接拋錯。** 否則使用者一點「載入示範資料」就 500。

上游刻意沒有做副檔名／magic bytes 偵測（YAGNI），所以這個判斷落在你這裡。做法建議：

```java
    if (!looksEncrypted(ciphertext)) {   // 例如讀前 N bytes 比對你們的格式標頭
      return ciphertext;                 // 原樣通過
    }
```

若要偷看開頭的位元組，記得用 `BufferedInputStream` + `mark()`/`reset()` 包起來，
不要把已讀掉的位元組弄丟。

### 4.2 開了設定卻沒有實作 bean → **啟動失敗**

`erd.upload.decryption.enabled=true` 時 `PassthroughUploadDecryptor` 會停用。
如果那時沒有任何 `UploadDecryptor` bean，`FileService` 注入不到，**應用程式起不來**。

這是**刻意的**：加密環境若靜默不解密，會把密文當 CSV 存進去，
後面整條分析線（DuckDB、dashboard、給使用者看的數字）全是垃圾資料，且不會有任何錯誤訊息。
寧可開不起來。

所以：**設定與實作 MUST 同一次部署上線。**

### 4.3 NEVER 把內容或金鑰寫進 log

這個 repo 的規範：關鍵路徑只記檔名／長度／計數。
`log.debug("decrypting {}", originalFilename)` 可以；記密文、明文、金鑰、token 一律禁止。
例外訊息也一樣——它可能被寫進 log。

---

## 5. 設定

```bash
ERD_UPLOAD_DECRYPTION_ENABLED=true
```

對應 `application.yml` 的：

```yaml
erd:
  upload:
    decryption:
      enabled: ${ERD_UPLOAD_DECRYPTION_ENABLED:false}
```

docker compose 已把這個變數接到 backend 服務（`docker-compose.app.yml`）。
K8s 部署則加進 backend 的環境變數。

API 端點與憑證另外用你們自己的設定鍵，並遵守：
**secrets NEVER 進 `application.properties`／`application.yml`，一律走環境變數。**

---

## 6. 測試（MUST 寫）

放在 `backend/src/test/java/com/erd/cowork/storage/`，命名格式
`methodName_condition_expectedBehavior`。這個 repo 用 JUnit 5 + Mockito + AssertJ。

至少涵蓋這五項：

| 測試 | 斷言 |
|---|---|
| 正常解密 | 回傳串流的位元組 == 預期明文 |
| API 失敗 | 拋 `IOException`，且**原始例外被包成 cause** |
| 明文輸入（示範資料集情境） | 原樣通過，不拋錯 |
| 重複 `close()` | 對回傳串流連續呼叫兩次 `close()` 不拋錯 |
| 暫存檔清理（僅形式 C） | 串流關閉後暫存檔已不存在；失敗路徑也不殘留 |

若要做端到端驗證，`FileServiceUploadTest` 有現成範例：`UploadDecryptor` 是單一方法介面，
測試可以直接用 lambda 塞假實作。

---

## 7. 驗收

```bash
cd backend && ./mvnw clean test  # 應全綠（本機基準 561 tests）
```

實機驗一次（不要只靠單元測試）：

1. 設 `ERD_UPLOAD_DECRYPTION_ENABLED=true` 啟動 backend
2. 上傳一個**真的加密過**的 CSV
3. 確認 dashboard 出得來、數字正確
4. 確認磁碟上 `ERD_STORAGE_LOCAL_DIR` 底下那份檔案是**明文**（`head` 看得懂）
5. 確認 DB `uploaded_file.size_bytes` == 該檔案**落地後**的大小（不是上傳時的密文大小）。
   csv 上傳時就等於明文大小；xlsx 上傳時等於**轉出的 CSV** 大小（`type` 也會是 `csv`，
   `name` 仍保留 `.xlsx`）
6. 再測「載入示範資料集」仍正常（驗證 4.1）

---

## 8. 不要做的事

- ❌ 不要改 `UploadDecryptor` 介面簽名——它刻意設計成三種 API 形式都不用改
- ❌ 不要改 `FileService`——解密的接線已經完成且經過審查
- ❌ 不要改 `PassthroughUploadDecryptor`——那是非公司環境的預設路徑
- ❌ 不要改成「讀取時才解密」——deepagent-service 的 DuckDB 直接讀磁碟檔案，密文落地會讓 Python 端讀到亂碼
- ❌ 不要在 `FileService` 裡加「這個檔要不要解密」的判斷——若真的需要偵測，放在你的實作內部（見 4.1）

---

## 參考

- 設計脈絡：`docs/superpowers/specs/2026-08-02-upload-decryption-hook-design.md`
- 架構說明中的一節：`docs/architecture.md` →「上傳檔解密掛鉤（UploadDecryptor）」
- 這個 repo 的 Java 規範：`.claude/skills/java/SKILL.md` 與根目錄 `CLAUDE.md`
