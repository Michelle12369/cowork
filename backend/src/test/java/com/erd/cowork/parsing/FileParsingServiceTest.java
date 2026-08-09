package com.erd.cowork.parsing;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.erd.cowork.exception.ParseException;
import com.erd.cowork.parsing.model.FileProfile;
import com.erd.cowork.parsing.model.ParsedRows;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.Collections;
import java.util.List;
import org.apache.poi.xssf.usermodel.XSSFWorkbook;
import org.junit.jupiter.api.Test;

class FileParsingServiceTest {

  private final FileParsingService service =
      new FileParsingService(
          new CsvParsingService(
              new com.erd.cowork.config.UploadProperties(
                  5, 5_368_709_120L, 2_147_483_648L, 209_715_200L, 20)),
          new XlsxParsingService(
              new com.erd.cowork.config.UploadProperties(
                  5, 5_368_709_120L, 2_147_483_648L, 209_715_200L, 20)),
          new ObjectMapper());

  private InputStream csv(String content) {
    return new ByteArrayInputStream(content.getBytes(StandardCharsets.UTF_8));
  }

  private ByteArrayInputStream buildXlsx() throws Exception {
    try (XSSFWorkbook wb = new XSSFWorkbook()) {
      var sheet = wb.createSheet("Sheet1");
      var header = sheet.createRow(0);
      header.createCell(0).setCellValue("lot");
      header.createCell(1).setCellValue("vt");
      var r1 = sheet.createRow(1);
      r1.createCell(0).setCellValue(95);
      r1.createCell(1).setCellValue(0.419);
      var r2 = sheet.createRow(2);
      r2.createCell(0).setCellValue(96);
      r2.createCell(1).setCellValue(0.423);
      ByteArrayOutputStream out = new ByteArrayOutputStream();
      wb.write(out);
      return new ByteArrayInputStream(out.toByteArray());
    }
  }

  // ── profile ───────────────────────────────────────────────────────────────

  @Test
  void profile_csvFileType_delegatesToCsvParser() {
    FileProfile profile = service.profile("csv", csv("x,y\n1,2\n3,4\n"));
    assertThat(profile.rowCount()).isEqualTo(2);
    assertThat(profile.headers()).containsExactly("x", "y");
  }

  @Test
  void profile_xlsxFileType_delegatesToXlsxParser() throws Exception {
    FileProfile profile = service.profile("xlsx", buildXlsx());
    assertThat(profile.rowCount()).isEqualTo(2);
    assertThat(profile.headers()).containsExactly("lot", "vt");
  }

  @Test
  void profile_unsupportedFileType_throwsParseExceptionWithUnsupportedMessage() {
    assertThatThrownBy(() -> service.profile("pdf", csv("dummy")))
        .isInstanceOf(ParseException.class)
        .hasMessageContaining("unsupported");
  }

  @Test
  void profile_tsvFileType_throwsParseExceptionWithUnsupportedMessage() {
    assertThatThrownBy(() -> service.profile("tsv", csv("x\ty\n1\t2\n")))
        .isInstanceOf(ParseException.class)
        .hasMessageContaining("unsupported");
  }

  @Test
  void profile_txtFileType_throwsParseExceptionWithUnsupportedMessage() {
    assertThatThrownBy(() -> service.profile("txt", csv("x,y\n1,2\n")))
        .isInstanceOf(ParseException.class)
        .hasMessageContaining("unsupported");
  }

  // ── readAll ───────────────────────────────────────────────────────────────

  @Test
  void readAll_csvFileType_returnsAllRows() {
    ParsedRows result = service.readAll("csv", csv("x,y\n1,2\n3,4\n"));
    assertThat(result.columns()).containsExactly("x", "y");
    assertThat(result.rows()).hasSize(2);
  }

  @Test
  void readAll_xlsxFileType_returnsAllRows() throws Exception {
    ParsedRows result = service.readAll("xlsx", buildXlsx());
    assertThat(result.columns()).containsExactly("lot", "vt");
    assertThat(result.rows()).hasSize(2);
  }

  @Test
  void readAll_unsupportedFileType_throwsParseException() {
    assertThatThrownBy(() -> service.readAll("pdf", csv("dummy")))
        .isInstanceOf(ParseException.class)
        .hasMessageContaining("unsupported");
  }

  // ── toJson ────────────────────────────────────────────────────────────────

  @Test
  void toJson_returnsValidJsonWithRowCount() throws Exception {
    FileProfile profile = service.profile("xlsx", buildXlsx());
    String json = service.toJson(profile);
    assertThat(json).contains("\"rowCount\"");
    // verify it is valid JSON
    new ObjectMapper().readTree(json);
  }

  // ── extension (static) ────────────────────────────────────────────────────

  @Test
  void extension_staticMethod_lowercasesAndExtractsExtension() {
    assertThat(FileParsingService.extension("data.CSV")).isEqualTo("csv");
    assertThat(FileParsingService.extension("report.XLSX")).isEqualTo("xlsx");
    assertThat(FileParsingService.extension("noext")).isEqualTo("");
  }

  // ── toJsonWithinByteLimit ────────────────────────────────────────────────

  private final FileParsingService parsingService =
      new FileParsingService(null, null, new ObjectMapper());

  private static FileProfile profileWithSampleRows(int rowCount, int cellChars) {
    List<List<String>> sampleRows =
        Collections.nCopies(rowCount, List.of("x".repeat(cellChars), "y".repeat(cellChars)));
    return new FileProfile(rowCount, 2, List.of("col_a", "col_b"), List.of(), sampleRows);
  }

  @Test
  void toJsonWithinByteLimit_smallProfile_returnsFullJson() {
    FileProfile profile = profileWithSampleRows(3, 10);
    String json = parsingService.toJsonWithinByteLimit(profile);
    assertThat(json).contains("sampleRows").contains("xxxxxxxxxx");
  }

  @Test
  void toJsonWithinByteLimit_oversizedSampleRows_shrinksSamplesToFit() {
    // 20 列 × 兩欄 × 5000 字 ≈ 200KB，遠超 65_000 bytes——縮樣本後必須合上限且仍是合法 JSON
    FileProfile profile = profileWithSampleRows(20, 5000);
    String json = parsingService.toJsonWithinByteLimit(profile);
    assertThat(json.getBytes(StandardCharsets.UTF_8).length)
        .isLessThanOrEqualTo(TextColumnUtils.TEXT_COLUMN_MAX_BYTES);
    assertThat(json).startsWith("{").endsWith("}");
  }
}
