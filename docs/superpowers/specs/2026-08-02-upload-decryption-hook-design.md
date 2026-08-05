# 上傳檔解密掛鉤（UploadDecryptor）設計

**日期**：2026-08-02
**狀態**：設計完成，待實作
**範圍**：backend（Java）單點改動；前端與 deepagent-service 零改動

---

## 問題

internal 環境的上傳檔是加密的，落地前需要先呼叫 internal API 解密。本專案 dev 環境沒有該 API，
也不該有。因此需要一個**接縫（seam）**：介面與接線進主線，實作留給 internal 環境。

撰寫本設計時，**internal 解密 API 的形式尚未確定**（可能可串流、可能要收整份 body、
也可能吃檔案路徑）。這個不確定性是本設計最主要的約束。

---

## 硬約束：明文必須落地

deepagent-service 的 DuckDB **直接從共用 volume 讀原始檔**，不經過 Java 的 `FileStorage.read()`：

- Java：`LangGraphAnalysisProvider.resolveSourcePath()` → `sourceRoot + "/" + storageKey`
- Python：`engine/duck.py` → `CREATE TABLE "{alias}" AS SELECT * FROM read_csv_auto(?)`

因此**儲存體上的位元組必須是明文**。若採「密文落地、讀取時才解密」，DuckDB 會讀到亂碼，
除非在 Python 端再實作一次解密——兩個語言各一份解密邏輯，不可接受。

**結論：解密 MUST 發生在 `storage.store()` 之前。** 這不是偏好，是架構決定的。

---

## 介面

```java
package com.erd.cowork.storage;

public interface UploadDecryptor {
  /**
   * Returns a plaintext stream for the given upload. Implementations that cannot stream may
   * buffer internally — that choice stays inside the implementation, not in this contract.
   */
  InputStream decrypt(InputStream ciphertext, String originalFilename) throws IOException;
}
```

### 為什麼是 `InputStream → InputStream`

這是**唯一三種 API 形式都不用改簽名**的選擇：

| internal API 實際形式 | 實作怎麼做 | 介面要改嗎 |
|---|---|---|
| 可串流 | 直接包裝 | 否 |
| 要整份 body | 實作內部自行 buffer | 否 |
| 吃檔案路徑 | 實作內部先落暫存檔 | 否 |

反例：若簽名選 `byte[] → byte[]`，而 internal API 其實可串流，則 2GB CSV 永遠會 OOM，
且要修就得改介面與所有呼叫端。串流簽名把「要不要整份讀進記憶體」這個決定**收進實作類別內部**。

`originalFilename` 一併傳入：解密 API 可能需要檔名或副檔名做為判斷依據；不需要時忽略即可。

### 命名

`UploadDecryptor` 為 Spring bean，沿用 repo 既有的 `*Rewriter`／`*Repairer`／`*Validator`
命名慣例（見 CLAUDE.md 類別命名分類法），置於 `com.erd.cowork.storage` package——它與
`FileStorage` 同屬「位元組落地」這一層的關注點。

---

## 開關與實作選擇

設定鍵：`erd.upload.decryption.enabled`（預設 `false`）。

| Bean | 條件 | 行為 |
|---|---|---|
| `PassthroughUploadDecryptor` | `matchIfMissing = true`、`havingValue = "false"` | 原樣回傳傳入的串流，零成本 |
| （internal 環境實作） | `havingValue = "true"` | 呼叫 internal API 解密 |

dev／本專案不設該鍵即為 passthrough，行為與現況完全一致。internal 環境翻一個設定並提供實作類別。

**刻意不做**（YAGNI）：副檔名（`.enc`）判斷、magic bytes 偵測、多種解密演算法選擇。
目前的需求是「internal 環境的上傳檔一律要解密」，加偵測邏輯只是憑空造出未驗證的分支。
真的出現混合加密／未加密上傳時再加。

---

## 接線位置

`FileService.upload()` 的 IO 階段，`storage.store()` 之前：

```java
long storedBytes;                       // 宣告在 try 之外，供後續建 entity 使用
try (InputStream in = upload.getInputStream();
     InputStream plaintext = decryptor.decrypt(in, filename);
     CountingInputStream counting = new CountingInputStream(plaintext)) {
  storageKey = storage.store(StorageCategory.UPLOAD, sessionId, filename, counting);
  storedBytes = counting.getByteCount();   // MUST 在 try 內取，關閉後才讀語意不保證
} catch (IOException exception) {
  throw new UncheckedIOException("failed to store upload: " + filename, exception);
}
...
entity.setSizeBytes(storedBytes);       // 取代原本的 upload.getSize()
```

下游全部不動：`storage.read(storageKey)` 讀到的是明文，`parsing.profile()`、DuckDB、
deepagent-service 皆維持原狀。

---

## 大小計算修正

現況 `entity.setSizeBytes(upload.getSize())` 記的是 **multipart 的位元組數＝密文大小**。
解密啟用後，密文與明文大小不同，會造成：

- DB 的 `size_bytes` 與磁碟實際檔案大小不符
- session 配額（5GB）以密文大小累加，與實際佔用不符

修正：用 `CountingInputStream` 包住解密後的串流，`store()` 回來後取 `getByteCount()` 寫入
`sizeBytes`。passthrough 模式下該值等於 `upload.getSize()`，行為不變。

`commons-io`（`CountingInputStream` 來源）目前是 poi-ooxml 帶進來的**傳遞依賴**；因為要直接
使用，MUST 在 `pom.xml` 顯式宣告，避免上游改依賴時無聲斷裂。

### 上限檢查維持用密文大小

`validate()` 在讀取任何位元組前就跑（依 `MultipartFile.getSize()`），無法得知明文大小。
維持現狀：以密文大小做上限檢查。這偏保守（一般加密後 ≥ 原始大小），可接受。
**不**為此把 validate 移到解密後——那會讓「超大檔」必須先完整解密才能被拒絕，
把 DoS 面放大。

---

## 錯誤處理

- 解密失敗（IO 或 API 錯誤）→ 實作拋 `IOException`。因為 `decrypt()` 就位在既有 store
  try-with-resources 內，會被同一個 `catch (IOException)` 包成 `UncheckedIOException`
  （與現行 store 失敗的處理方式一致），再由外層 `catch (RuntimeException)` 的清理路徑
  刪除本批已落地的 storage 物件，不留孤兒。此行為由「解密失敗測試」驗證。
- NEVER 在 log 中輸出密文、明文內容或金鑰；僅記錄檔名與長度（沿用 CLAUDE.md 日誌規範）。

---

## 測試

| 測試 | 內容 |
|---|---|
| `PassthroughUploadDecryptor` 單元測試 | 傳入串流原樣回傳、內容不變 |
| `FileService` 上傳測試（既有） | 注入 passthrough，行為與現況一致（回歸保護） |
| `FileService` 解密測試（新增） | 注入 fake decryptor（例如把內容反轉／去掉前綴），驗證**落地的是解密後內容**、且 `sizeBytes` 等於解密後長度而非上傳大小 |
| 解密失敗測試 | fake decryptor 拋 `IOException` → 上傳失敗且 storage 無殘留物件 |

測試方法命名沿用 `methodName_condition_expectedBehavior`。

---

## 不在範圍

- internal API 的實際實作（由 internal 環境提供）
- artifact／workspace 的加密（本設計只處理**上傳檔**）
- 下載時的重新加密
- 加密金鑰管理／輪替
