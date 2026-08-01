package com.erd.cowork.agent;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.Set;
import org.junit.jupiter.api.Test;

class TableMarkerUtilsTest {

  @Test
  void extractReferencedTableIds_noMarker_returnsEmptySet() {
    assertThat(TableMarkerUtils.extractReferencedTableIds("plain answer")).isEmpty();
  }

  @Test
  void extractReferencedTableIds_blankText_returnsEmptySet() {
    assertThat(TableMarkerUtils.extractReferencedTableIds(null)).isEmpty();
    assertThat(TableMarkerUtils.extractReferencedTableIds("")).isEmpty();
    assertThat(TableMarkerUtils.extractReferencedTableIds("   ")).isEmpty();
  }

  @Test
  void extractReferencedTableIds_singleMarker_returnsThatId() {
    assertThat(TableMarkerUtils.extractReferencedTableIds("before [[table:tbl_1]] after"))
        .containsExactly("tbl_1");
  }

  @Test
  void extractReferencedTableIds_multipleMarkers_returnsAllIdsInAppearanceOrder() {
    Set<String> ids =
        TableMarkerUtils.extractReferencedTableIds("[[table:tbl_2]] text [[table:tbl_1]]");
    assertThat(ids).containsExactly("tbl_2", "tbl_1");
  }

  @Test
  void extractReferencedTableIds_duplicateMarkerForSameId_returnsOneEntry() {
    Set<String> ids =
        TableMarkerUtils.extractReferencedTableIds("[[table:tbl_1]] ... [[table:tbl_1]]");
    assertThat(ids).containsExactly("tbl_1");
  }
}
