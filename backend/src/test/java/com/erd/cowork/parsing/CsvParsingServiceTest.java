package com.erd.cowork.parsing;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.assertj.core.api.Assertions.within;

import com.erd.cowork.exception.ParseException;
import com.erd.cowork.parsing.model.ColumnProfile;
import com.erd.cowork.parsing.model.FileProfile;
import com.erd.cowork.parsing.model.ParsedRows;
import java.io.ByteArrayInputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import org.junit.jupiter.api.Test;

class CsvParsingServiceTest {

  private final CsvParsingService service = new CsvParsingService(new com.erd.cowork.config.UploadProperties(5, 5_368_709_120L, 2_147_483_648L, 209_715_200L, 20));

  private InputStream csv(String content) {
    return new ByteArrayInputStream(content.getBytes(StandardCharsets.UTF_8));
  }

  // ── profile ───────────────────────────────────────────────────────────────

  @Test
  void profile_numericAndStringColumns_infersTypesAndStats() {
    FileProfile profile =
        service.profile(csv("lot,vt,grade\n95,0.419,A\n96,0.423,B\n97,0.421,A\n"));
    assertThat(profile.rowCount()).isEqualTo(3);
    assertThat(profile.colCount()).isEqualTo(3);
    assertThat(profile.headers()).containsExactly("lot", "vt", "grade");

    ColumnProfile vtColumn = profile.columns().get(1);
    assertThat(vtColumn.colType()).isEqualTo("number");
    assertThat(vtColumn.min()).isEqualTo(0.419);
    assertThat(vtColumn.max()).isEqualTo(0.423);
    assertThat(vtColumn.mean()).isCloseTo(0.421, within(1e-9));

    ColumnProfile grade = profile.columns().get(2);
    assertThat(grade.colType()).isEqualTo("string");
    assertThat(grade.topValues()).containsExactly("A", "B");
    assertThat(grade.min()).isNull();
  }

  @Test
  void profile_isoDateColumn_infersDateType() {
    FileProfile profile = service.profile(csv("d\n2026-01-01\n2026-01-02\n"));
    assertThat(profile.columns().get(0).colType()).isEqualTo("date");
  }

  @Test
  void profile_blankCells_countedAsNulls_typeUnaffected() {
    FileProfile profile = service.profile(csv("v\n1\n\n3\n"));
    ColumnProfile vColumn = profile.columns().get(0);
    assertThat(vColumn.colType()).isEqualTo("number");
    assertThat(vColumn.nullCount()).isEqualTo(1);
    assertThat(profile.rowCount()).isEqualTo(3);
  }

  @Test
  void profile_sampleRows_cappedAt20() {
    StringBuilder csvBuilder = new StringBuilder("n\n");
    for (int index = 0; index < 50; index++) csvBuilder.append(index).append('\n');
    FileProfile profile = service.profile(csv(csvBuilder.toString()));
    assertThat(profile.rowCount()).isEqualTo(50);
    assertThat(profile.sampleRows()).hasSize(20);
    assertThat(profile.sampleRows().get(0)).containsExactly("0");
  }

  @Test
  void profile_emptyInput_throwsParseException() {
    assertThatThrownBy(() -> service.profile(csv(""))).isInstanceOf(ParseException.class);
  }

  // ── readAll ───────────────────────────────────────────────────────────────

  @Test
  void readAll_threeRows_returnsAllColumnsAndRows() {
    ParsedRows result = service.readAll(csv("a,b\n1,x\n2,y\n3,z\n"));
    assertThat(result.columns()).containsExactly("a", "b");
    assertThat(result.rows()).hasSize(3);
    assertThat(result.rows().get(0)).containsExactly("1", "x");
    assertThat(result.rows().get(2)).containsExactly("3", "z");
  }

  @Test
  void readAll_headerOnlyFile_returnsEmptyRows() {
    ParsedRows result = service.readAll(csv("col1,col2\n"));
    assertThat(result.columns()).containsExactly("col1", "col2");
    assertThat(result.rows()).isEmpty();
  }
}
