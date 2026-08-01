package com.erd.cowork.agent.extraction;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;

class BareHtmlUtilsTest {

  @Test
  void extract_doctypeDocumentWithSurroundingText_returnsDocumentOnly() {
    String text = "intro\n<!DOCTYPE html><html><body>x</body></html>\ntrailing";
    assertThat(BareHtmlUtils.extract(text)).isEqualTo("<!DOCTYPE html><html><body>x</body></html>");
  }

  @Test
  void extract_htmlTagWithoutDoctype_returnsDocument() {
    String text = "note <html><body>x</body></html>";
    assertThat(BareHtmlUtils.extract(text)).isEqualTo("<html><body>x</body></html>");
  }

  @Test
  void extract_noClosingHtmlTag_returnsNull() {
    assertThat(BareHtmlUtils.extract("<html><body>x</body>")).isNull();
  }

  @Test
  void extract_noHtmlAtAll_returnsNull() {
    assertThat(BareHtmlUtils.extract("just prose")).isNull();
  }

  @Test
  void extract_blankText_returnsNull() {
    assertThat(BareHtmlUtils.extract("  ")).isNull();
    assertThat(BareHtmlUtils.extract(null)).isNull();
  }

  @Test
  void extract_lengthChangingUnicodeBeforeDocument_boundariesStayAligned() {
    // "İ" (U+0130) becomes two chars when lowercased. With indexes computed on a
    // lowercased copy, both boundaries would shift and the extraction would be corrupted.
    String text = "İİ 前言 <!DOCTYPE html><html><body>x</body></html> 後記";
    assertThat(BareHtmlUtils.extract(text)).isEqualTo("<!DOCTYPE html><html><body>x</body></html>");
  }
}
