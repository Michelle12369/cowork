# 上傳 xlsx 正規化為 CSV 設計

**日期**：2026-08-02
**狀態**：設計完成，待實作
**範圍**：backend（Java）上傳鏈 + 前端圖示一行（deepagent 側不改）

---

## 問題

上傳允許 `csv` 與 `xlsx`，檔案**原樣落地**（不轉檔）。但 deepagent（analysis）線的 DuckDB
只認得 CSV：

```python
_READERS = {"csv": "read_csv_auto", "parquet": "read_parquet"}   # engine/duck.py
```

`_READERS.get("xlsx")` → `None` → `raise ValueError("unsupported file type: xlsx")`。

**且失敗得無聲**：`open_locked_connection()` 在 `main.py:259` 被呼叫，而該行位於 try 區塊
（261 行起）**之外**，例外不會轉成 SSE 的 `ERROR` 事件，串流直接斷掉——使用者看到的是
模糊的連線失敗，不是「不支援 xlsx」。

兩側支援的型別是**雙向**對不上的：

| | csv | xlsx | parquet |
|---|---|---|---|
| Java 上傳驗證接受 | ✅ | ✅ | ❌ |
| duck.py 讀得到 | ✅ | ❌ | ✅（但永遠收不到） |

交集只有 csv。llm api 線不受影響（Java 端用 `XlsxParsingService` 自行解析 xlsx）。

---

## 決策：上傳時轉檔，只取第一個 sheet

xlsx 在落地前轉成 CSV，**落地後系統中只存在 CSV**。

### 為什麼轉檔的成本比想像中低

**公司環境只有 xlsx 需要解密，csv 不用。** 也就是說 xlsx 本來就必須被完整讀取處理一次，
轉檔的邊際成本趨近於零。而且「需要解密的」與「DuckDB 讀不到的」是**同一批檔案**——
一個階段解決兩個問題。

### 為什麼「只取第一個 sheet」不是新的行為損失

**這已經是現況。** `XlsxParsingService` 的 `profile()` 與 `readAll()` 都寫死
`workbook.getSheetAt(0)`——今天上傳多 sheet 的 xlsx，llm api 線本來就只用第一個 sheet，
其餘靜默忽略。轉檔只是把這個既有行為**顯性化**，不是新增限制。

進一步：轉檔可直接沿用 `XlsxParsingService` 既有的
`StreamingReader` + `DataFormatter` + `extractCells` 路徑，產出的 cell 字串與今天
llm api 線讀到的**完全相同**。因此型別推斷的輸入不變——`FileProfileHelper` 與 CSV parser
都是吃字串，轉檔前後拿到同一批字串。**這消除了「動到既有正常路徑」的主要風險。**

### 為什麼不是其他兩條路

- **讓 DuckDB 讀 xlsx**：需要 install/load excel extension，而該連線的設計是
  materialize 後 `SET enable_external_access=false` + `lock_configuration=true`。載 extension
  必須在鎖門前做，等於為了單一格式擴大攻擊面——與鎖門設計的意圖相衝突。
- **analysis 線拒收 xlsx**：功能縮水，且使用者要自己在 Excel 裡另存 CSV——把問題丟回去。

### 為什麼不是「只在 analysis 模式才轉檔」

會讓**落地內容取決於當下的 provider 設定**：今天用 openai-compatible 存了 xlsx，日後切到
langgraph-analysis，那些舊檔就讀不到了。要轉就統一轉。

---

## 資料模型

| 欄位 | 值 | 誰在用 |
|---|---|---|
| `name` | `sales.xlsx`（**原樣不動**） | UI 顯示（`FileChips`／`AttachmentsPopover`／`UploadModal` 都渲染 `file.name`） |
| `type` | **`csv`**（轉檔後的真實格式） | `FileParsingService` 分派、`LangGraphAnalysisProvider` 的 `fileType`（→ DuckDB reader） |
| `storage_key` 指向的 bytes | 第一個 sheet 的 CSV | DuckDB `read_csv_auto` 讀得到 |
| `size_bytes` | 轉檔後的 CSV 位元組數 | 既有的 `CountingInputStream` 計數自然涵蓋 |

**`type` 的語意定為「落地格式」**，因為它的兩個消費者（parsing 分派、DuckDB reader）
問的都是「磁碟上的 bytes 是什麼格式」。原始格式可由 `name` 的副檔名推導。

**不新增 `original_type` 欄位**——可從 `name` 推導，YAGNI。

### UI：仍要看得出是 xlsx

- **檔名已經是** `sales.xlsx`，三個顯示元件都渲染 `file.name`，**無需改動** ✓
- **唯一要改的是圖示**：`getFileIcon(file.type)` 只認 `type === 'xlsx'` 給 Excel 綠圖示。
  改為由 `file.name` 的副檔名判斷即可（一個 util 函式的改動）。

---

## 元件與位置

轉檔**不放進 `UploadDecryptor`**：`decrypt()` 的契約是 stream → stream、內容邏輯不變，
而轉檔必須同時改變 `type`——那是 `decrypt()` 的簽名表達不了的。硬塞進去會導致
`type` 仍是 `xlsx`，deepagent 照樣失敗、`FileParsingService` 還會拿 xlsx parser 去解 CSV。

新增 `UploadNormalizer`（Spring bean，`com.erd.cowork.parsing`），回傳內容與結果型別：

```java
public record NormalizedUpload(Path content, String type) {}

public NormalizedUpload normalize(InputStream source, String originalFilename) throws IOException;
```

`FileService.upload()` 的順序：**解密 → 正規化 → 落地**

```java
// 解密後才轉檔：公司環境的 xlsx 是加密的，未解密前無法解析
try (InputStream in = upload.getInputStream();
    InputStream plaintext = decryptor.decrypt(in, filename)) {
  normalized = normalizer.normalize(plaintext, filename);
}
try (InputStream content = Files.newInputStream(normalized.content(), DELETE_ON_CLOSE);
    CountingInputStream counting = new CountingInputStream(content)) {
  storageKey = storage.store(StorageCategory.UPLOAD, sessionId, filename, counting);
  storedKeys.add(storageKey);
  storedBytes = counting.getByteCount();
}
entity.setType(normalized.type());   // 取代 entity.setType(ext)
```

**回傳 `Path` 而非 `InputStream`**：xlsx 是 zip 容器，無法真正串流；上限 200MB，轉出的 CSV
可能 2–3 倍。落暫存檔而非進 heap，避免 ~600MB 的 in-heap 尖峰。

**csv 也會落一份暫存檔**（本節初版寫成「直接 passthrough（不複製、不轉檔）」，與實作不符，
已更正）。`normalize()` 對 csv 走的是 `Files.copy(source, temporaryFile, ...)`：型別維持 `csv`、
內容不變，但確實多複製一次。原因是回傳型別統一為 `Path`——csv 若不落檔就得回傳 stream，
`NormalizedUpload` 得變成兩種形狀，呼叫端的暫存檔生命週期也得分兩條路。

**為什麼現在不改成 csv 免複製**：暫存檔的生命週期正是這條線已經出過四個缺陷的地方
（列寬對不齊兩次、明文暫存檔外洩、分派用錯識別碼）。為了省一次複製而重塑生命週期，
風險遠大於收益。等這條線穩定後再單獨處理。

**暫存檔位置**：`java.io.tmpdir` 在容器內預設是可寫層（ephemeral），而暫存檔最大可達
`erd.upload.max-csv-bytes`＝2GB。因此 `backend/Dockerfile` 將 `java.io.tmpdir` 指到
files PVC 掛載點底下（`ERD_TMP_DIR`，預設 `/data/files/.tmp`），`docker-compose.app.yml`
以與 `ERD_STORAGE_LOCAL_DIR` 相同的方式覆寫。

---

## 邊界行為

| 情況 | 行為 |
|---|---|
| xlsx 有多個 sheet | 取第一個，其餘忽略；**MUST log 一筆 warn**（檔名 + sheet 數），資料靜默消失是最糟的失敗模式 |
| xlsx 完全沒有列 | 拋 `ParseException("xlsx has no rows")`——沿用 `XlsxParsingService` 既有語意 |
| xlsx 只有標題列 | 正常轉出「只有標題列的 CSV」，不特別處理 |
| xlsx 標題列有空白格 | 以 1-based 位置產生名稱（`column_2`）；與既有真實標題撞名時往後找 `column_2_2`。**只有標題列**如此，資料列的空白格維持空字串 |
| 轉出的 CSV 超過 `max-csv-bytes` | 寫入端邊寫邊計數，超過即中止、刪除半成品、拋 `UploadLimitException`（訊息只帶上限數字，NEVER 帶內容） |
| csv 上傳 | 複製到暫存檔（見上），`type` 維持 `csv`、內容不變 |
| 轉檔失敗 | 拋 `IOException`／`ParseException`，由 `FileService` 既有清理路徑刪除已落地物件 |
| 暫存檔 | `DELETE_ON_CLOSE` 讀完即刪；失敗路徑一律由 `FileService` 的 `finally` 無條件刪（含解密串流 `close()` 拋例外的路徑） |

### 為什麼空白標題要在「寫入端」補名稱

這份 CSV 有**兩個互不相干的消費者**：Java 的 commons-csv 與 DuckDB 的 `read_csv_auto`。
標題留空的話，Java 端看到 `""`（實際上 commons-csv 直接以 `A header name is missing` 拒收，
`ParseException` → HTTP 400，且因為 profiling 在逐檔迴圈內，整批上傳會一起中止），
而 DuckDB 會自行生成一個名字——兩邊對「這一欄叫什麼」的認知會分岔。寫入時就寫死一個名字，
兩邊讀到的才是同一件事，而這正是整條正規化存在的理由。

因此**不採用**把 `CsvParsingService` 放寬成 `allowMissingColumnNames(true)` 的做法：那只讓
Java 端不再報錯，兩邊名稱分岔的問題原封不動。

**CSV 輸出用 commons-csv**（已是相依）寫入，確保引號/逗號/換行正確跳脫；讀回時同一套
parser 得到相同字串。

---

## deepagent 側不改

**本次不動 `duck.py` 與 `main.py`**（既有的 `_READERS` 與錯誤處理維持原狀）。

⚠️ **已知限制（不要寫成「不可達」）**：本分支**沒有 migration**，因此
- **新上傳**的檔案 `type` 一律是 `csv`——這些確實不會再走到 `_READERS.get("xlsx")`；
- **改動之前**已經存在的列仍是 `type='xlsx'`＋真正的 xlsx bytes。`LangGraphAnalysisProvider`
  原樣轉發 `file.type()`，所以這些舊列**仍會**打到 `_READERS.get("xlsx")` → `None` →
  `ValueError`，而該呼叫位在 `main.py` 的 try 區塊之外，SSE 串流會直接斷掉、不產生 `ERROR`
  事件——使用者看到的是模糊的連線失敗。

**期限**：上傳原始檔的保留窗是 180 天（依 session 最後活動），所以這批舊列會在該窗內自然消失。
在那之前 analysis 線對它們是「失敗且沒有乾淨錯誤訊息」。若要提早收斂，選項是補一支
migration 重轉舊檔，或把 `main.py` 的 `open_locked_connection()` 移進 try 區塊——兩者都不在
本次範圍（使用者明確排除 deepagent 側改動）。

---

## 測試

| 測試 | 斷言 |
|---|---|
| `UploadNormalizer` csv passthrough | 內容不變、`type` 為 `csv`、不產生暫存檔 |
| `UploadNormalizer` xlsx 轉換 | 輸出為 CSV、內容等於第一個 sheet、`type` 為 `csv` |
| `UploadNormalizer` 多 sheet | 只取第一個 sheet，且有 warn log |
| `UploadNormalizer` 空 xlsx | 拋 `ParseException` |
| CSV 跳脫 | cell 含逗號/引號/換行時，轉出的 CSV 讀回後字串相同 |
| `FileService` xlsx 上傳 | 落地的是 CSV bytes、`type` 為 `csv`、`name` 仍為 `.xlsx`、`sizeBytes` 為 CSV 長度 |
| `FileService` 轉檔失敗 | 上傳中止且無殘留 storage 物件 |
| 前端 `getFileIcon` | 由檔名判斷：`sales.xlsx` → Excel 圖示、`sales.csv` → 文字圖示 |

---

## 不在範圍

- 多 sheet 各自成為獨立 alias（已決定只取第一個）
- xls（舊格式）、ods 等其他試算表格式
- 轉檔後回存原始 xlsx 供下載
- 前端顯示「只取了第一個 sheet」的提示（先只在後端 log；若使用者實際踩到再加）
