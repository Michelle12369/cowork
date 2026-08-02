# xlsx → CSV Normalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 上傳的 xlsx 在落地前轉成 CSV（只取第一個 sheet），讓 deepagent 線的 DuckDB 讀得到；UI 仍顯示原始的 `.xlsx`。

**Architecture:** 新增 `UploadNormalizer`（Spring bean），在 `FileService.upload()` 的解密之後、落地之前執行。csv 直接 passthrough；xlsx 用既有 `XlsxParsingService` 同一套 `StreamingReader` + `DataFormatter` 讀第一個 sheet，以 commons-csv 寫成暫存 CSV 檔。`uploaded_file.type` 改記**落地格式**（`csv`），`name` 維持原始檔名，前端圖示改由檔名副檔名推導。

**Tech Stack:** Java 17、Spring Boot 3.4.1、Lombok、excel-streaming-reader（POI）、commons-csv、JUnit 5 + Mockito + AssertJ；前端 React 18 + Vitest；deepagent-service Python + pytest

**Spec:** [`docs/superpowers/specs/2026-08-02-xlsx-to-csv-normalization-design.md`](../specs/2026-08-02-xlsx-to-csv-normalization-design.md)

**Branch:** `feat/xlsx-csv-normalization`（已 rebase 到含 PR #7／#8 的 master）

## Global Constraints

- Java 17；NEVER 使用 18+ API
- 一律 constructor injection；NEVER `@Autowired` field injection；用 `@RequiredArgsConstructor`
- 變數／參數 NEVER 用 1–2 字元名稱；一律描述性單詞
- 類別命名分類法：`*Normalizer` 為 Spring bean，MUST 有 stereotype 註解；`*Utils` 為 final + private 建構子 + 全 static
- NEVER 空的 catch block；拋新例外 MUST 包裝原始 cause
- 所有 IO 資源 MUST 用 try-with-resources；暫存檔 MUST 在失敗路徑也被刪除
- 日誌 NEVER 記錄檔案內容；僅記檔名／長度／計數
- 測試方法命名：`methodName_condition_expectedBehavior`
- google-java-format 由 Claude hook 自動執行，**勿手動調整格式風格**
- 前端：`React.FC` + props interface；NEVER `any`；測試斷言元素級行為
- Python 一律 `uv run`；`engine/` 層 NEVER import langchain/langgraph/deepagents
- 每個 task 結束前 MUST 跑對應測試全綠才 commit

---

### Task 1: UploadNormalizer（csv passthrough + xlsx→CSV）

**Files:**
- Create: `backend/src/main/java/com/erd/cowork/parsing/NormalizedUpload.java`
- Create: `backend/src/main/java/com/erd/cowork/parsing/UploadNormalizer.java`
- Test: `backend/src/test/java/com/erd/cowork/parsing/UploadNormalizerTest.java`

**Interfaces:**
- Consumes: 既有 `XlsxParsingService` 的讀法（`StreamingReader.builder().rowCacheSize(100).bufferSize(8192).open(in)`、`workbook.getSheetAt(0)`、`DataFormatter`）
- Produces: `NormalizedUpload(Path content, String type)`；`UploadNormalizer.normalize(InputStream source, String originalFilename) throws IOException` → `NormalizedUpload`。Task 2 會在 `FileService` 呼叫它。

**關鍵**：寫出的 CSV MUST 能被既有 `CsvParsingService.csvFormat()` 讀回——它是
`CSVFormat.DEFAULT.builder().setHeader().setSkipHeaderRecord(true).setIgnoreEmptyLines(false).build()`，
即**第一列為標題列**。因此轉檔時第一列直接寫 sheet 的第一列即可（`XlsxParsingService`
也是把第一列當標題）。用 `CSVFormat.DEFAULT` 的 `CSVPrinter` 寫入，跳脫由它處理。

- [ ] **Step 1: 寫失敗測試**

建立 `UploadNormalizerTest.java`。需要一個能產生測試用 xlsx 的 helper——用 POI 的
`XSSFWorkbook`（`poi-ooxml` 已是相依）在測試內建檔：

```java
package com.erd.cowork.parsing;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.erd.cowork.exception.ParseException;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.List;
import org.apache.poi.xssf.usermodel.XSSFWorkbook;
import org.junit.jupiter.api.Test;

class UploadNormalizerTest {

  private final UploadNormalizer normalizer = new UploadNormalizer();

  /** Builds an in-memory xlsx; each inner list is one row, first row is the header. */
  private static byte[] xlsxBytes(List<String> sheetNames, List<List<String>> firstSheetRows)
      throws Exception {
    try (XSSFWorkbook workbook = new XSSFWorkbook();
        ByteArrayOutputStream output = new ByteArrayOutputStream()) {
      var sheet = workbook.createSheet(sheetNames.get(0));
      for (int rowIndex = 0; rowIndex < firstSheetRows.size(); rowIndex++) {
        var row = sheet.createRow(rowIndex);
        List<String> cells = firstSheetRows.get(rowIndex);
        for (int columnIndex = 0; columnIndex < cells.size(); columnIndex++) {
          row.createCell(columnIndex).setCellValue(cells.get(columnIndex));
        }
      }
      for (int extraSheet = 1; extraSheet < sheetNames.size(); extraSheet++) {
        workbook.createSheet(sheetNames.get(extraSheet)).createRow(0).createCell(0)
            .setCellValue("ignored");
      }
      workbook.write(output);
      return output.toByteArray();
    }
  }

  @Test
  void normalize_csvUpload_passesContentThroughUnchanged() throws Exception {
    byte[] original = "col\n1\n".getBytes(StandardCharsets.UTF_8);

    NormalizedUpload result = normalizer.normalize(new ByteArrayInputStream(original), "data.csv");

    assertThat(result.type()).isEqualTo("csv");
    assertThat(Files.readAllBytes(result.content())).isEqualTo(original);
    Files.deleteIfExists(result.content());
  }

  @Test
  void normalize_xlsxUpload_writesFirstSheetAsCsv() throws Exception {
    byte[] xlsx =
        xlsxBytes(List.of("Sheet1"), List.of(List.of("name", "qty"), List.of("apple", "3")));

    NormalizedUpload result = normalizer.normalize(new ByteArrayInputStream(xlsx), "data.xlsx");

    assertThat(result.type()).isEqualTo("csv");
    assertThat(Files.readString(result.content(), StandardCharsets.UTF_8))
        .isEqualTo("name,qty\r\napple,3\r\n");
    Files.deleteIfExists(result.content());
  }

  @Test
  void normalize_xlsxWithMultipleSheets_usesOnlyFirstSheet() throws Exception {
    byte[] xlsx =
        xlsxBytes(
            List.of("First", "Second", "Third"), List.of(List.of("col"), List.of("kept")));

    NormalizedUpload result = normalizer.normalize(new ByteArrayInputStream(xlsx), "multi.xlsx");

    String csv = Files.readString(result.content(), StandardCharsets.UTF_8);
    assertThat(csv).isEqualTo("col\r\nkept\r\n");
    assertThat(csv).doesNotContain("ignored");
    Files.deleteIfExists(result.content());
  }

  @Test
  void normalize_xlsxCellsNeedingEscaping_roundTripsThroughCsv() throws Exception {
    byte[] xlsx =
        xlsxBytes(
            List.of("Sheet1"),
            List.of(List.of("note"), List.of("a,b"), List.of("say \"hi\"")));

    NormalizedUpload result = normalizer.normalize(new ByteArrayInputStream(xlsx), "esc.xlsx");

    // Reading back with the same CSVFormat CsvParsingService uses must yield the original cells.
    try (InputStream stored = Files.newInputStream(result.content())) {
      var parsed = new CsvParsingService(null).readAll(stored);
      assertThat(parsed.rows()).containsExactly(List.of("a,b"), List.of("say \"hi\""));
    }
    Files.deleteIfExists(result.content());
  }

  @Test
  void normalize_xlsxWithNoRows_throwsParseException() throws Exception {
    byte[] xlsx = xlsxBytes(List.of("Sheet1"), List.of());

    assertThatThrownBy(() -> normalizer.normalize(new ByteArrayInputStream(xlsx), "empty.xlsx"))
        .isInstanceOf(ParseException.class)
        .hasMessageContaining("no rows");
  }
}
```

> 若 `CsvParsingService` 的建構子不接受 `null`（它注入 `UploadProperties`），改用
> `new CsvParsingService(uploadPropertiesStub)`，其中 stub 只需回傳 `sampleRows()`。
> 執行 Step 2 時會看到實際錯誤，依實際簽名調整。

- [ ] **Step 2: 執行測試確認失敗**

Run: `cd backend && ./mvnw -q test -Dtest=UploadNormalizerTest`
Expected: 編譯失敗（`UploadNormalizer`／`NormalizedUpload` 不存在）

- [ ] **Step 3: 建立 NormalizedUpload record**

```java
package com.erd.cowork.parsing;

import java.nio.file.Path;

/**
 * Result of normalizing one upload: the bytes to store and the format they are in.
 *
 * @param content path to the content to store. For xlsx this is a temp file the caller MUST
 *     delete after storing (open it with {@code StandardOpenOption.DELETE_ON_CLOSE}); for csv it
 *     is a temp copy of the upload, deleted the same way.
 * @param type the on-disk format, which is what {@code uploaded_file.type} records — always
 *     {@code csv} today, since xlsx is converted. NEVER the uploaded file's extension.
 */
public record NormalizedUpload(Path content, String type) {}
```

- [ ] **Step 4: 建立 UploadNormalizer**

```java
package com.erd.cowork.parsing;

import com.erd.cowork.exception.ParseException;
import com.github.pjfanning.xlsx.StreamingReader;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStreamWriter;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.ArrayList;
import java.util.List;
import lombok.extern.slf4j.Slf4j;
import org.apache.commons.csv.CSVFormat;
import org.apache.commons.csv.CSVPrinter;
import org.apache.poi.ss.usermodel.Cell;
import org.apache.poi.ss.usermodel.DataFormatter;
import org.apache.poi.ss.usermodel.Row;
import org.apache.poi.ss.usermodel.Sheet;
import org.apache.poi.ss.usermodel.Workbook;
import org.springframework.stereotype.Component;

/**
 * Normalizes an upload to the single format everything downstream reads: CSV.
 *
 * <p>xlsx is converted here rather than at read time because deepagent-service points DuckDB at
 * the stored file directly and DuckDB has no xlsx reader. Converting once at upload keeps a single
 * format at rest instead of teaching every reader about spreadsheets.
 *
 * <p>Only the first sheet is used. That is not a new restriction: {@link XlsxParsingService}
 * already pins {@code getSheetAt(0)}, so multi-sheet workbooks have always been read this way.
 */
@Slf4j
@Component
public class UploadNormalizer {

  private static final String CSV_TYPE = "csv";

  public NormalizedUpload normalize(InputStream source, String originalFilename)
      throws IOException {
    Path temporaryFile = Files.createTempFile("erd-upload-", ".tmp");
    try {
      if ("xlsx".equals(FileParsingService.extension(originalFilename))) {
        convertFirstSheetToCsv(source, temporaryFile, originalFilename);
      } else {
        Files.copy(source, temporaryFile, StandardCopyOption.REPLACE_EXISTING);
      }
      return new NormalizedUpload(temporaryFile, CSV_TYPE);
    } catch (RuntimeException | IOException exception) {
      Files.deleteIfExists(temporaryFile);
      throw exception;
    }
  }

  private void convertFirstSheetToCsv(InputStream source, Path target, String originalFilename)
      throws IOException {
    DataFormatter formatter = new DataFormatter();
    try (Workbook workbook =
            StreamingReader.builder().rowCacheSize(100).bufferSize(8192).open(source);
        Writer writer = new OutputStreamWriter(Files.newOutputStream(target),
            StandardCharsets.UTF_8);
        CSVPrinter printer = new CSVPrinter(writer, CSVFormat.DEFAULT)) {
      int sheetCount = workbook.getNumberOfSheets();
      if (sheetCount > 1) {
        // Data silently disappearing is the worst failure mode -- leave a trace.
        log.warn(
            "xlsx {} has {} sheets; only the first is converted", originalFilename, sheetCount);
      }
      Sheet sheet = workbook.getSheetAt(0);
      boolean wroteAnyRow = false;
      for (Row row : sheet) {
        printer.printRecord(extractCells(row, formatter));
        wroteAnyRow = true;
      }
      if (!wroteAnyRow) {
        throw new ParseException("xlsx has no rows");
      }
    } catch (ParseException exception) {
      throw exception;
    } catch (IOException exception) {
      throw exception;
    } catch (Exception exception) {
      throw new ParseException("failed to convert xlsx: " + exception.getMessage(), exception);
    }
  }

  private static List<String> extractCells(Row row, DataFormatter formatter) {
    List<String> cells = new ArrayList<>();
    for (Cell cell : row) {
      cells.add(formatter.formatCellValue(cell));
    }
    return cells;
  }
}
```

> 若 `FileParsingService.extension(...)` 不是 public static，改為在本類別內以
> 相同規則取副檔名（小寫、取最後一個 `.` 之後），不要改動 `FileParsingService` 的可見性。

- [ ] **Step 5: 執行測試確認通過**

Run: `cd backend && ./mvnw -q test -Dtest=UploadNormalizerTest`
Expected: PASS（5 個測試）。若 CSV 換行符斷言失敗，先印出實際值再調整——`CSVFormat.DEFAULT`
的 record separator 是 `\r\n`。

- [ ] **Step 6: 全套測試**

Run: `cd backend && ./mvnw test`
Expected: BUILD SUCCESS，0 failures

- [ ] **Step 7: Commit**

```bash
git add backend/src/main/java/com/erd/cowork/parsing/NormalizedUpload.java \
        backend/src/main/java/com/erd/cowork/parsing/UploadNormalizer.java \
        backend/src/test/java/com/erd/cowork/parsing/UploadNormalizerTest.java
git commit -m "feat(backend): add UploadNormalizer converting xlsx to CSV"
```

---

### Task 2: 接線進 FileService

**Files:**
- Modify: `backend/src/main/java/com/erd/cowork/service/FileService.java`
- Modify: `backend/src/test/java/com/erd/cowork/service/FileServiceUploadTest.java`
- Modify: `backend/src/test/java/com/erd/cowork/service/FileServiceDeleteTest.java`
- Modify: `backend/src/test/java/com/erd/cowork/service/FileServiceDecryptionFailureTest.java`
- Modify: `backend/src/test/java/com/erd/cowork/service/FileServicePassthroughDecryptorTest.java`

**Interfaces:**
- Consumes: Task 1 的 `UploadNormalizer.normalize(InputStream, String)` → `NormalizedUpload(Path, String)`
- Produces: `FileService` 建構子新增第 10 個參數 `UploadNormalizer normalizer`（`@RequiredArgsConstructor` 依欄位順序，**加在 `decryptor` 之後**）。`entity.setType(...)` 改用 `normalized.type()`。

現況的 store 區塊（PR #7 之後）：

```java
        try (InputStream in = upload.getInputStream();
            InputStream plaintext = decryptor.decrypt(in, filename);
            CountingInputStream counting = new CountingInputStream(plaintext)) {
          storageKey = storage.store(StorageCategory.UPLOAD, sessionId, filename, counting);
          storedKeys.add(storageKey);
          storedBytes = counting.getByteCount();
        } catch (IOException exception) {
          throw new UncheckedIOException("failed to store upload: " + filename, exception);
        }
```

- [ ] **Step 1: 寫失敗測試**

在 `FileServiceUploadTest.java` 的 `@Mock` 區塊加入 `@Mock UploadNormalizer normalizer;`，
建構子多帶一個參數，並在 `setUp()` 加入預設 stub（把輸入原樣寫進暫存檔、type 回 `csv`）：

```java
    when(normalizer.normalize(any(), anyString()))
        .thenAnswer(
            invocation -> {
              InputStream suppliedStream = invocation.getArgument(0);
              Path temporaryFile = Files.createTempFile("test-normalized-", ".csv");
              Files.copy(suppliedStream, temporaryFile, StandardCopyOption.REPLACE_EXISTING);
              return new NormalizedUpload(temporaryFile, "csv");
            });
```

新增測試：

```java
  @Test
  void upload_xlsxUpload_recordsCsvTypeNotTheUploadedExtension() {
    ChatSession session = new ChatSession();
    session.setId("session-1");
    session.setUserId("user-1");
    when(sessionGuard.loadOrCreateOwned("session-1")).thenReturn(session);

    MockMultipartFile upload =
        new MockMultipartFile(
            "file",
            "sales.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "col\n1\n".getBytes(StandardCharsets.UTF_8));

    service.upload("session-1", List.of(upload));

    ArgumentCaptor<UploadedFile> savedEntity = ArgumentCaptor.forClass(UploadedFile.class);
    verify(files).save(savedEntity.capture());
    // type is the on-disk format; the original extension survives only in name.
    assertThat(savedEntity.getValue().getType()).isEqualTo("csv");
    assertThat(savedEntity.getValue().getName()).isEqualTo("sales.xlsx");
  }
```

`limits.maxXlsxBytes()` 需要 stub（`validate()` 對 xlsx 分支會讀）——加進 `setUp()`：
`when(limits.maxXlsxBytes()).thenReturn(209_715_200L);`

- [ ] **Step 2: 執行測試確認失敗**

Run: `cd backend && ./mvnw -q test -Dtest=FileServiceUploadTest`
Expected: 編譯失敗（建構子只有 9 個參數）

- [ ] **Step 3: 修改 FileService**

欄位區在 `decryptor` 之後加入：

```java
  private final UploadNormalizer normalizer;
```

新增 import：`com.erd.cowork.parsing.NormalizedUpload`、`com.erd.cowork.parsing.UploadNormalizer`、
`java.nio.file.Files`、`java.nio.file.StandardOpenOption`。

store 區塊改為：

```java
        String storageKey;
        long storedBytes;
        String storedType;
        FileProfile profile;
        // Decrypt first (company uploads are encrypted), then normalize to CSV: deepagent-service
        // points DuckDB at this file directly and DuckDB has no xlsx reader, so only CSV may land.
        NormalizedUpload normalized;
        try (InputStream in = upload.getInputStream();
            InputStream plaintext = decryptor.decrypt(in, filename)) {
          normalized = normalizer.normalize(plaintext, filename);
        } catch (IOException exception) {
          throw new UncheckedIOException("failed to normalize upload: " + filename, exception);
        }
        storedType = normalized.type();
        // DELETE_ON_CLOSE removes the normalizer's temp file once it has been streamed to storage.
        try (InputStream content =
                Files.newInputStream(normalized.content(), StandardOpenOption.DELETE_ON_CLOSE);
            CountingInputStream counting = new CountingInputStream(content)) {
          storageKey = storage.store(StorageCategory.UPLOAD, sessionId, filename, counting);
          storedKeys.add(storageKey);
          storedBytes = counting.getByteCount();
        } catch (IOException exception) {
          throw new UncheckedIOException("failed to store upload: " + filename, exception);
        }
```

並把 `entity.setType(ext);` 改為 `entity.setType(storedType);`。

> `ext` 仍用於 `validate()` 的上限判斷（依**上傳的**副檔名），維持不變——只有 entity 記錄的
> type 改成落地格式。

- [ ] **Step 4: 修正其餘三個測試類別的建構子**

`FileServiceDeleteTest`、`FileServiceDecryptionFailureTest`、`FileServicePassthroughDecryptorTest`
的 `new FileService(...)` 都要多帶第 10 個參數。delete 路徑不會用到，可傳 `@Mock`
或簡單 fake；解密失敗測試在 normalize 之前就中止，同樣不會被呼叫。

- [ ] **Step 5: 執行測試確認通過**

Run: `cd backend && ./mvnw -q test -Dtest=FileServiceUploadTest,FileServiceDeleteTest,FileServiceDecryptionFailureTest,FileServicePassthroughDecryptorTest`
Expected: PASS

- [ ] **Step 6: 全套測試**

Run: `cd backend && ./mvnw test`
Expected: BUILD SUCCESS，0 failures

- [ ] **Step 7: Commit**

```bash
git add backend/src/main/java/com/erd/cowork/service/FileService.java \
        backend/src/test/java/com/erd/cowork/service/
git commit -m "feat(backend): normalize uploads to CSV before storing them"
```

---

### Task 3: 前端圖示改由檔名判斷

**Files:**
- Modify: `frontend/src/utils/fileIcon.tsx`
- Test: `frontend/src/utils/fileIcon.test.tsx`（若不存在則建立）

**Interfaces:**
- Consumes: 無（純前端）
- Produces: `getFileIcon` 改為接受檔名。**三個呼叫端 MUST 一併更新**：
  `FileChips.tsx:25`、`UploadModal.tsx:183`、`AttachmentsPopover.tsx:47`——目前都傳 `file.type`，改傳 `file.name`。

現況：

```tsx
export const getFileIcon = (type: string, size = 17): React.ReactNode => {
  if (type.toLowerCase() === 'xlsx')
    return <FileExcelOutlined style={{ color: '#52c41a', fontSize: size }} />;
  return <FileTextOutlined style={{ color: '#1677ff', fontSize: size }} />;
};
```

轉檔後 `file.type` 一律是 `csv`，xlsx 上傳會顯示成文字圖示——故改由檔名副檔名判斷。

- [ ] **Step 1: 寫失敗測試**

建立 `frontend/src/utils/fileIcon.test.tsx`：

```tsx
import { render } from '@testing-library/react';
import { getFileIcon } from './fileIcon';

test('getFileIcon_xlsxFilename_rendersExcelIcon', () => {
  const { container } = render(<>{getFileIcon('sales.xlsx')}</>);
  expect(container.querySelector('[aria-label="file-excel"]')).not.toBeNull();
});

test('getFileIcon_csvFilename_rendersTextIcon', () => {
  const { container } = render(<>{getFileIcon('sales.csv')}</>);
  expect(container.querySelector('[aria-label="file-text"]')).not.toBeNull();
});

test('getFileIcon_uppercaseExtension_stillRendersExcelIcon', () => {
  const { container } = render(<>{getFileIcon('SALES.XLSX')}</>);
  expect(container.querySelector('[aria-label="file-excel"]')).not.toBeNull();
});

test('getFileIcon_filenameWithoutExtension_rendersTextIcon', () => {
  const { container } = render(<>{getFileIcon('README')}</>);
  expect(container.querySelector('[aria-label="file-text"]')).not.toBeNull();
});
```

> antd 圖示的 `aria-label` 實際值請以 Step 2 的失敗輸出為準；若不同，改用
> `container.querySelector('.anticon-file-excel')` 之類的 class 選擇器。

- [ ] **Step 2: 執行測試確認失敗**

Run: `cd frontend && npm test -- fileIcon`
Expected: FAIL（傳入檔名時，`'sales.xlsx' !== 'xlsx'` 所以走到文字圖示）

- [ ] **Step 3: 改寫 fileIcon.tsx**

```tsx
/** Picks the icon from the file NAME, not the stored type: xlsx uploads are converted to CSV at
 *  upload time, so `type` is always 'csv' and only the name still shows what the user uploaded. */
export const getFileIcon = (fileName: string, size = 17): React.ReactNode => {
  const extension = fileName.toLowerCase().split('.').pop() ?? '';
  if (extension === 'xlsx')
    return <FileExcelOutlined style={{ color: '#52c41a', fontSize: size }} />;
  return <FileTextOutlined style={{ color: '#1677ff', fontSize: size }} />;
};
```

- [ ] **Step 4: 更新三個呼叫端**

把 `getFileIcon(file.type, ...)` 改為 `getFileIcon(file.name, ...)`：
`FileChips.tsx:25`、`UploadModal.tsx:183`、`AttachmentsPopover.tsx:47`。

> `UploadModal` 的 `file` 可能是尚未上傳的 `File` 物件——確認該處的 `file.name` 存在
> （原生 `File` 有 `name`）。若該處的型別不同，依實際型別取檔名。

- [ ] **Step 5: 執行測試確認通過**

Run: `cd frontend && npm test`
Expected: 全綠（含既有 319 + 新增 4）

- [ ] **Step 6: typecheck 與 lint**

Run: `cd frontend && npx tsc --noEmit && npm run lint`
Expected: 皆無錯誤

- [ ] **Step 7: Commit**

```bash
git add frontend/src/utils/fileIcon.tsx frontend/src/utils/fileIcon.test.tsx \
        frontend/src/components/files/
git commit -m "fix(frontend): derive file icon from filename, not stored type"
```

---

### Task 4: deepagent 的型別錯誤要有聲

**Files:**
- Modify: `deepagent-service/app/engine/duck.py`
- Modify: `deepagent-service/app/main.py`
- Test: `deepagent-service/tests/test_duck.py`（若不存在則建立）

**Interfaces:**
- Consumes: 無（獨立於 Java 側改動）
- Produces: 無新 API；僅錯誤處理與死設定清理

**背景**：轉檔後 xlsx 不會再以 xlsx 身分到達 deepagent，但不支援的型別仍應**明確報錯**而非
斷線。現況 `open_locked_connection()` 在 `main.py` 的 try 區塊**之外**被呼叫，
`ValueError` 會讓 SSE 直接斷掉。

- [ ] **Step 1: 寫測試**

在 `deepagent-service/tests/test_duck.py` 新增（若檔案不存在則連同 import 一起建立）：

```python
import pytest

from app.engine.duck import Source, open_locked_connection


def test_open_locked_connection_unsupported_file_type_raises_value_error():
    with pytest.raises(ValueError, match="unsupported file type"):
        open_locked_connection([Source(alias="data", path="/tmp/x.xlsx", file_type="xlsx")])
```

- [ ] **Step 2: 執行測試**

Run: `cd deepagent-service && uv run pytest tests/test_duck.py -q`
Expected: PASS（此行為已存在，本測試為迴歸保護）

- [ ] **Step 3: 清掉死設定並讓錯誤有聲**

`duck.py`：把 `_READERS` 的 `parquet` 移除（Java 上傳驗證只接受 csv／xlsx，parquet 永遠
到不了），並在註解說明為何只剩 csv：

```python
# 只剩 csv:xlsx 在上傳時已由 Java 端的 UploadNormalizer 轉成 CSV,parquet 從未被上傳驗證接受。
_READERS = {"csv": "read_csv_auto"}
```

`main.py`：把 `open_locked_connection(...)` 移進既有的 try 區塊內（或另包一層 try），
讓 `ValueError` 轉成 SSE 的 `ERROR` 事件而非斷線。參考同檔既有的錯誤事件格式：

```python
yield {"type": "ERROR", "code": "AGENT_FAILURE", "message": message}
```

實作時請先讀 `main.py` 該段的既有結構，沿用同樣的 yield 形狀與 `code` 值慣例；
若既有錯誤碼不合適，用 `UNSUPPORTED_FILE_TYPE`，並確認 Java 端
`LangGraphAnalysisProvider` 對未知 code 的處理不會炸（它會原樣轉發 ERROR 事件）。

- [ ] **Step 4: 測試與 lint**

Run: `cd deepagent-service && uv run pytest -q && uv run ruff check .`
Expected: 全綠

- [ ] **Step 5: Commit**

```bash
git add deepagent-service/app/engine/duck.py deepagent-service/app/main.py \
        deepagent-service/tests/test_duck.py
git commit -m "fix(deepagent): surface unsupported file types as an ERROR event"
```

---

### Task 5: 文件

**Files:**
- Modify: `docs/architecture.md`

**Interfaces:**
- Consumes: Task 1–4 的成果
- Produces: 無程式碼

- [ ] **Step 1: 更新 architecture.md**

在「上傳檔解密掛鉤（UploadDecryptor）」一節**之後**新增：

```markdown
## 上傳格式正規化（xlsx → CSV）

上傳允許 `csv` 與 `xlsx`，但**落地的一律是 CSV**：`FileService.upload()` 在解密後、落地前
呼叫 `UploadNormalizer`，把 xlsx 的**第一個 sheet** 轉成 CSV。

**為什麼在上傳時轉，而不是讓 DuckDB 讀 xlsx**：deepagent-service 用 DuckDB 直接讀磁碟檔，
而 DuckDB 沒有 xlsx reader；載入 excel extension 必須在 `enable_external_access=false`
鎖門之前做，等於為單一格式擴大攻擊面。轉檔後系統中只有一種格式，兩條線都受益。

**只取第一個 sheet 不是新限制**：`XlsxParsingService` 的 `profile()`／`readAll()` 一直都是
`getSheetAt(0)`。轉檔沿用同一套 `StreamingReader` + `DataFormatter`，產出的 cell 字串與
llm api 線原本讀到的相同，型別推斷不變。多 sheet 時後端記一筆 warn。

**欄位語意**：`uploaded_file.type` 記的是**落地格式**（永遠 `csv`），`name` 保留使用者上傳的
原始檔名（`sales.xlsx`）。前端的檔案圖示因此改由**檔名副檔名**判斷，而非 `type`。
```

並更新 erDiagram 中 `type` 的註解為：`"落地格式（一律 csv；xlsx 於上傳時轉檔）"`。

- [ ] **Step 2: 確認測試仍綠**

Run: `cd backend && ./mvnw test`
Expected: BUILD SUCCESS

- [ ] **Step 3: Commit**

```bash
git add docs/architecture.md
git commit -m "docs: document xlsx-to-CSV upload normalization"
```

---

## 完成後的驗收

- [ ] `cd backend && ./mvnw test` 全綠（基準 539 + 本計畫新增約 6 → 約 545）
- [ ] `cd frontend && npm test` 全綠（基準 319 + 4 → 323）
- [ ] `cd deepagent-service && uv run pytest -q && uv run ruff check .` 全綠
- [ ] 實機驗證：上傳一個**多 sheet 的 xlsx** → 確認 (a) UI 顯示 `.xlsx` 檔名與 Excel 圖示、
      (b) 磁碟上該檔是 CSV 文字、(c) DB 的 `type` 為 `csv`、(d) analysis 線問一題能正常出圖、
      (e) 後端 log 有 multi-sheet 的 warn
- [ ] 開 PR 併回 master
