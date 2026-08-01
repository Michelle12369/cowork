package com.erd.cowork.parsing;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;

class TextSearchUtilsTest {

  @Test
  void indexOfIgnoreCase_differentCase_found() {
    assertThat(TextSearchUtils.indexOfIgnoreCase("xx<HEAD>yy", "<head")).isEqualTo(2);
  }

  @Test
  void indexOfIgnoreCase_targetAbsent_returnsMinusOne() {
    assertThat(TextSearchUtils.indexOfIgnoreCase("<body></body>", "<head")).isEqualTo(-1);
  }

  @Test
  void indexOfIgnoreCase_fromIndexSkipsEarlierMatch() {
    assertThat(TextSearchUtils.indexOfIgnoreCase("<head><head>", "<head", 1)).isEqualTo(6);
  }

  @Test
  void indexOfIgnoreCase_lengthChangingUnicodeBeforeTarget_indexValidInOriginal() {
    // "İ" (U+0130) becomes two chars when lowercased — an index computed on a lowercased
    // copy would be shifted; the returned index must be valid in the original string.
    String text = "İİ<head>";
    int index = TextSearchUtils.indexOfIgnoreCase(text, "<head");
    assertThat(index).isEqualTo(2);
    assertThat(text.substring(index)).startsWith("<head>");
  }

  @Test
  void indexOfIgnoreCase_nullOrEmptyArgs_returnsMinusOne() {
    assertThat(TextSearchUtils.indexOfIgnoreCase(null, "x")).isEqualTo(-1);
    assertThat(TextSearchUtils.indexOfIgnoreCase("x", null)).isEqualTo(-1);
    assertThat(TextSearchUtils.indexOfIgnoreCase("x", "")).isEqualTo(-1);
  }
}
