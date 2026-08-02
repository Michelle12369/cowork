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
}
