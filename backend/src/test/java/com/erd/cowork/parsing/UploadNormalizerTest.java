package com.erd.cowork.parsing;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import ch.qos.logback.classic.Level;
import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import com.erd.cowork.config.UploadProperties;
import com.erd.cowork.exception.ParseException;
import com.erd.cowork.exception.UploadLimitException;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.List;
import org.apache.poi.xssf.usermodel.XSSFWorkbook;
import org.junit.jupiter.api.Test;
import org.slf4j.LoggerFactory;

class UploadNormalizerTest {

  private static final UploadProperties DEFAULT_LIMITS =
      new UploadProperties(5, 5_000_000_000L, 2_000_000_000L, 200_000_000L, 20);

  private final UploadNormalizer normalizer = new UploadNormalizer(DEFAULT_LIMITS);

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
        workbook
            .createSheet(sheetNames.get(extraSheet))
            .createRow(0)
            .createCell(0)
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
        xlsxBytes(List.of("First", "Second", "Third"), List.of(List.of("col"), List.of("kept")));

    NormalizedUpload result = normalizer.normalize(new ByteArrayInputStream(xlsx), "multi.xlsx");

    String csv = Files.readString(result.content(), StandardCharsets.UTF_8);
    assertThat(csv).isEqualTo("col\r\nkept\r\n");
    assertThat(csv).doesNotContain("ignored");
    Files.deleteIfExists(result.content());
  }

  /** The dropped-sheet warn is a spec MUST — silently discarded data needs a trace in the log. */
  @Test
  void normalize_xlsxWithMultipleSheets_warnsThatOnlyTheFirstSheetIsConverted() throws Exception {
    byte[] xlsx =
        xlsxBytes(List.of("First", "Second", "Third"), List.of(List.of("col"), List.of("kept")));
    Logger normalizerLogger = (Logger) LoggerFactory.getLogger(UploadNormalizer.class);
    ListAppender<ILoggingEvent> warnings = new ListAppender<>();
    warnings.start();
    normalizerLogger.addAppender(warnings);

    try {
      NormalizedUpload result = normalizer.normalize(new ByteArrayInputStream(xlsx), "multi.xlsx");
      Files.deleteIfExists(result.content());
    } finally {
      normalizerLogger.detachAppender(warnings);
    }

    assertThat(warnings.list)
        .filteredOn(event -> event.getLevel() == Level.WARN)
        .extracting(ILoggingEvent::getFormattedMessage)
        .anySatisfy(message -> assertThat(message).contains("multi.xlsx").contains("3 sheets"));
  }

  @Test
  void normalize_xlsxSingleSheet_doesNotWarnAboutDroppedSheets() throws Exception {
    byte[] xlsx = xlsxBytes(List.of("Only"), List.of(List.of("col"), List.of("kept")));
    Logger normalizerLogger = (Logger) LoggerFactory.getLogger(UploadNormalizer.class);
    ListAppender<ILoggingEvent> warnings = new ListAppender<>();
    warnings.start();
    normalizerLogger.addAppender(warnings);

    try {
      NormalizedUpload result = normalizer.normalize(new ByteArrayInputStream(xlsx), "single.xlsx");
      Files.deleteIfExists(result.content());
    } finally {
      normalizerLogger.detachAppender(warnings);
    }

    assertThat(warnings.list).isEmpty();
  }

  @Test
  void normalize_xlsxCellsNeedingEscaping_roundTripsThroughCsv() throws Exception {
    byte[] xlsx =
        xlsxBytes(
            List.of("Sheet1"), List.of(List.of("note"), List.of("a,b"), List.of("say \"hi\"")));

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

  @Test
  void normalize_xlsxRowWithGapInMiddle_preservesColumnAlignment() throws Exception {
    byte[] xlsx;
    try (XSSFWorkbook workbook = new XSSFWorkbook();
        ByteArrayOutputStream output = new ByteArrayOutputStream()) {
      var sheet = workbook.createSheet("Sheet1");
      var header = sheet.createRow(0);
      header.createCell(0).setCellValue("a");
      header.createCell(1).setCellValue("b");
      header.createCell(2).setCellValue("c");
      var dataRow = sheet.createRow(1);
      // Column 1 is intentionally left un-created (sparse row) to reproduce a middle gap.
      dataRow.createCell(0).setCellValue("x");
      dataRow.createCell(2).setCellValue("z");
      workbook.write(output);
      xlsx = output.toByteArray();
    }

    NormalizedUpload result = normalizer.normalize(new ByteArrayInputStream(xlsx), "gap.xlsx");

    assertThat(Files.readString(result.content(), StandardCharsets.UTF_8))
        .isEqualTo("a,b,c\r\nx,,z\r\n");
    Files.deleteIfExists(result.content());
  }

  @Test
  void normalize_xlsxBlankInteriorRow_readsBackAsFullWidthEmptyRecord() throws Exception {
    byte[] xlsx;
    try (XSSFWorkbook workbook = new XSSFWorkbook();
        ByteArrayOutputStream output = new ByteArrayOutputStream()) {
      var sheet = workbook.createSheet("Sheet1");
      var header = sheet.createRow(0);
      header.createCell(0).setCellValue("a");
      header.createCell(1).setCellValue("b");
      header.createCell(2).setCellValue("c");
      // Row exists but has zero materialized cells -- getLastCellNum() == -1.
      sheet.createRow(1);
      var dataRow = sheet.createRow(2);
      dataRow.createCell(0).setCellValue("x");
      dataRow.createCell(1).setCellValue("y");
      dataRow.createCell(2).setCellValue("z");
      workbook.write(output);
      xlsx = output.toByteArray();
    }

    NormalizedUpload result = normalizer.normalize(new ByteArrayInputStream(xlsx), "blank.xlsx");

    // Assert by reading back through the same CSVFormat path CsvParsingService uses -- the raw
    // CSV string alone would not catch a row that is narrower than the header.
    try (InputStream stored = Files.newInputStream(result.content())) {
      var parsed = new CsvParsingService(null).readAll(stored);
      assertThat(parsed.rows()).hasSize(2);
      assertThat(parsed.rows().get(0)).hasSize(3).containsExactly("", "", "");
      assertThat(parsed.rows().get(1)).hasSize(3).containsExactly("x", "y", "z");
    }
    Files.deleteIfExists(result.content());
  }

  @Test
  void normalize_xlsxRowWithEmptyTrailingColumns_readsBackAsFullWidthRecord() throws Exception {
    byte[] xlsx;
    try (XSSFWorkbook workbook = new XSSFWorkbook();
        ByteArrayOutputStream output = new ByteArrayOutputStream()) {
      var sheet = workbook.createSheet("Sheet1");
      var header = sheet.createRow(0);
      header.createCell(0).setCellValue("a");
      header.createCell(1).setCellValue("b");
      header.createCell(2).setCellValue("c");
      var dataRow = sheet.createRow(1);
      // Columns 1 and 2 are intentionally never created -- trailing gap, not a middle gap.
      dataRow.createCell(0).setCellValue("x");
      workbook.write(output);
      xlsx = output.toByteArray();
    }

    NormalizedUpload result = normalizer.normalize(new ByteArrayInputStream(xlsx), "trailing.xlsx");

    assertThat(Files.readString(result.content(), StandardCharsets.UTF_8))
        .isEqualTo("a,b,c\r\nx,,\r\n");
    try (InputStream stored = Files.newInputStream(result.content())) {
      var parsed = new CsvParsingService(null).readAll(stored);
      assertThat(parsed.rows()).hasSize(1);
      assertThat(parsed.rows().get(0)).hasSize(3).containsExactly("x", "", "");
    }
    Files.deleteIfExists(result.content());
  }

  /**
   * A spacer or unlabelled index column produces a blank header cell. Written verbatim it becomes
   * an empty CSV field, which commons-csv rejects outright ("A header name is missing"), so the
   * whole upload batch would fail with HTTP 400. Reading back through {@link CsvParsingService}
   * proves the file actually parses, not merely that some bytes were written.
   */
  @Test
  void normalize_xlsxBlankHeaderCellInMiddle_writesGeneratedPositionalName() throws Exception {
    byte[] xlsx = xlsxWithHeader(List.of("a", "", "c"), List.of("1", "2", "3"));

    NormalizedUpload result = normalizer.normalize(new ByteArrayInputStream(xlsx), "spacer.xlsx");

    try (InputStream stored = Files.newInputStream(result.content())) {
      var parsed = new CsvParsingService(null).readAll(stored);
      assertThat(parsed.columns()).containsExactly("a", "column_2", "c");
      assertThat(parsed.rows()).containsExactly(List.of("1", "2", "3"));
    }
    Files.deleteIfExists(result.content());
  }

  @Test
  void normalize_xlsxBlankHeaderCellAtEnd_writesGeneratedPositionalName() throws Exception {
    byte[] xlsx = xlsxWithHeader(List.of("a", "b", ""), List.of("1", "2", "3"));

    NormalizedUpload result = normalizer.normalize(new ByteArrayInputStream(xlsx), "trailing.xlsx");

    try (InputStream stored = Files.newInputStream(result.content())) {
      var parsed = new CsvParsingService(null).readAll(stored);
      assertThat(parsed.columns()).containsExactly("a", "b", "column_3");
      assertThat(parsed.rows()).containsExactly(List.of("1", "2", "3"));
    }
    Files.deleteIfExists(result.content());
  }

  /**
   * A real header already carrying the generated name would collide with it, and a duplicate header
   * name breaks {@code setHeader()} exactly as badly as a missing one — so the generated name has
   * to step aside to the next free variant.
   */
  @Test
  void normalize_xlsxBlankHeaderCollidesWithRealName_picksNextFreeVariant() throws Exception {
    byte[] xlsx = xlsxWithHeader(List.of("column_2", "", "c"), List.of("1", "2", "3"));

    NormalizedUpload result = normalizer.normalize(new ByteArrayInputStream(xlsx), "collide.xlsx");

    try (InputStream stored = Files.newInputStream(result.content())) {
      var parsed = new CsvParsingService(null).readAll(stored);
      assertThat(parsed.columns()).containsExactly("column_2", "column_2_2", "c");
      assertThat(parsed.rows()).containsExactly(List.of("1", "2", "3"));
    }
    Files.deleteIfExists(result.content());
  }

  /** Blank cells outside the header row stay empty — only header names have to be non-blank. */
  @Test
  void normalize_xlsxBlankDataCell_staysEmptyInsteadOfBeingNamed() throws Exception {
    byte[] xlsx = xlsxWithHeader(List.of("a", "b", "c"), List.of("1", "", "3"));

    NormalizedUpload result = normalizer.normalize(new ByteArrayInputStream(xlsx), "data.xlsx");

    try (InputStream stored = Files.newInputStream(result.content())) {
      var parsed = new CsvParsingService(null).readAll(stored);
      assertThat(parsed.columns()).containsExactly("a", "b", "c");
      assertThat(parsed.rows()).containsExactly(List.of("1", "", "3"));
    }
    Files.deleteIfExists(result.content());
  }

  @Test
  void normalize_xlsxCellWithEmbeddedNewline_roundTripsThroughCsv() throws Exception {
    byte[] xlsx = xlsxBytes(List.of("Sheet1"), List.of(List.of("note"), List.of("line1\nline2")));

    NormalizedUpload result = normalizer.normalize(new ByteArrayInputStream(xlsx), "newline.xlsx");

    try (InputStream stored = Files.newInputStream(result.content())) {
      var parsed = new CsvParsingService(null).readAll(stored);
      assertThat(parsed.columns()).containsExactly("note");
      assertThat(parsed.rows()).containsExactly(List.of("line1\nline2"));
    }
    Files.deleteIfExists(result.content());
  }

  /**
   * Upload validation caps the xlsx at its pre-conversion size, but xlsx->CSV expands many times
   * over for text-heavy sheets, so the write itself has to be capped too. A tiny injected limit
   * keeps the test fast; the limit has to appear in the message and the content never does.
   */
  @Test
  void normalize_convertedCsvExceedsLimit_abortsAndDeletesPartialTempFile() throws Exception {
    List<List<String>> rows = new ArrayList<>();
    rows.add(List.of("note"));
    for (int rowIndex = 0; rowIndex < 200; rowIndex++) {
      rows.add(List.of("x".repeat(500)));
    }
    byte[] xlsx = xlsxBytes(List.of("Sheet1"), rows);
    UploadNormalizer cappedNormalizer =
        new UploadNormalizer(new UploadProperties(5, 5_000_000_000L, 1_024L, 200_000_000L, 20));
    long temporaryFilesBefore = countNormalizerTempFiles();

    assertThatThrownBy(
            () -> cappedNormalizer.normalize(new ByteArrayInputStream(xlsx), "huge.xlsx"))
        .isInstanceOf(UploadLimitException.class)
        .hasMessageContaining("1024")
        .hasMessageNotContaining("xxxxx");

    assertThat(countNormalizerTempFiles()).isEqualTo(temporaryFilesBefore);
  }

  private static long countNormalizerTempFiles() throws Exception {
    Path temporaryDirectory = Paths.get(System.getProperty("java.io.tmpdir"));
    try (var entries = Files.list(temporaryDirectory)) {
      return entries
          .filter(path -> path.getFileName().toString().startsWith("erd-upload-"))
          .count();
    }
  }

  /** Builds a one-data-row workbook; an empty string means the cell is created but left blank. */
  private static byte[] xlsxWithHeader(List<String> headerCells, List<String> dataCells)
      throws Exception {
    try (XSSFWorkbook workbook = new XSSFWorkbook();
        ByteArrayOutputStream output = new ByteArrayOutputStream()) {
      var sheet = workbook.createSheet("Sheet1");
      var header = sheet.createRow(0);
      for (int columnIndex = 0; columnIndex < headerCells.size(); columnIndex++) {
        header.createCell(columnIndex).setCellValue(headerCells.get(columnIndex));
      }
      var dataRow = sheet.createRow(1);
      for (int columnIndex = 0; columnIndex < dataCells.size(); columnIndex++) {
        dataRow.createCell(columnIndex).setCellValue(dataCells.get(columnIndex));
      }
      workbook.write(output);
      return output.toByteArray();
    }
  }
}
