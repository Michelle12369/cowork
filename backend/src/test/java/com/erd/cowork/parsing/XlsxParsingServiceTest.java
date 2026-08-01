package com.erd.cowork.parsing;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.assertj.core.api.Assertions.within;

import com.erd.cowork.exception.ParseException;
import com.erd.cowork.parsing.model.ColumnProfile;
import com.erd.cowork.parsing.model.FileProfile;
import com.erd.cowork.parsing.model.ParsedRows;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import org.apache.poi.xssf.usermodel.XSSFWorkbook;
import org.junit.jupiter.api.Test;

class XlsxParsingServiceTest {

  private final XlsxParsingService service = new XlsxParsingService(new com.erd.cowork.config.UploadProperties(5, 5_368_709_120L, 2_147_483_648L, 209_715_200L, 20));

  private ByteArrayInputStream buildXlsx(boolean withData) throws Exception {
    try (XSSFWorkbook wb = new XSSFWorkbook()) {
      var sheet = wb.createSheet("Sheet1");
      // header row
      var header = sheet.createRow(0);
      header.createCell(0).setCellValue("lot");
      header.createCell(1).setCellValue("vt");
      if (withData) {
        // data row 1
        var r1 = sheet.createRow(1);
        r1.createCell(0).setCellValue(95);
        r1.createCell(1).setCellValue(0.419);
        // data row 2
        var r2 = sheet.createRow(2);
        r2.createCell(0).setCellValue(96);
        r2.createCell(1).setCellValue(0.423);
      }
      ByteArrayOutputStream out = new ByteArrayOutputStream();
      wb.write(out);
      return new ByteArrayInputStream(out.toByteArray());
    }
  }

  // ── profile ───────────────────────────────────────────────────────────────

  @Test
  void profile_headerAndTwoRows_infersTypesAndStats() throws Exception {
    FileProfile profile = service.profile(buildXlsx(true));
    assertThat(profile.rowCount()).isEqualTo(2);
    assertThat(profile.colCount()).isEqualTo(2);
    assertThat(profile.headers()).containsExactly("lot", "vt");

    ColumnProfile vtColumn = profile.columns().get(1);
    assertThat(vtColumn.colType()).isEqualTo("number");
    assertThat(vtColumn.mean()).isCloseTo(0.421, within(1e-9));
  }

  @Test
  void profile_sheetWithNoRows_throwsParseException() throws Exception {
    try (XSSFWorkbook wb = new XSSFWorkbook()) {
      wb.createSheet("Sheet1"); // empty sheet, no rows at all
      ByteArrayOutputStream out = new ByteArrayOutputStream();
      wb.write(out);
      ByteArrayInputStream in = new ByteArrayInputStream(out.toByteArray());
      assertThatThrownBy(() -> service.profile(in)).isInstanceOf(ParseException.class);
    }
  }

  @Test
  void profile_headerOnlySheet_returnsZeroRowCount() throws Exception {
    // header-only is a legal input: rowCount=0, headers present, no exception
    FileProfile profile = service.profile(buildXlsx(false));
    assertThat(profile.rowCount()).isEqualTo(0);
    assertThat(profile.headers()).containsExactly("lot", "vt");
  }

  // ── readAll ───────────────────────────────────────────────────────────────

  @Test
  void readAll_headerAndTwoRows_returnsAllRows() throws Exception {
    ParsedRows result = service.readAll(buildXlsx(true));
    assertThat(result.columns()).containsExactly("lot", "vt");
    assertThat(result.rows()).hasSize(2);
    assertThat(result.rows().get(0).get(0)).isEqualTo("95");
  }

  @Test
  void readAll_headerOnlySheet_returnsEmptyRows() throws Exception {
    ParsedRows result = service.readAll(buildXlsx(false));
    assertThat(result.columns()).containsExactly("lot", "vt");
    assertThat(result.rows()).isEmpty();
  }
}
