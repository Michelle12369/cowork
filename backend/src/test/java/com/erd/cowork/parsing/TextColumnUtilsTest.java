package com.erd.cowork.parsing;

import static org.assertj.core.api.Assertions.assertThat;

import java.nio.charset.StandardCharsets;
import org.junit.jupiter.api.Test;

class TextColumnUtilsTest {

  @Test
  void truncateToUtf8Bytes_underLimit_returnsSameInstance() {
    String value = "hello 中文";
    assertThat(TextColumnUtils.truncateToUtf8Bytes(value, 100)).isSameAs(value);
  }

  @Test
  void truncateToUtf8Bytes_nullValue_returnsNull() {
    assertThat(TextColumnUtils.truncateToUtf8Bytes(null, 100)).isNull();
  }

  @Test
  void truncateToUtf8Bytes_overLimit_truncatesOnCharBoundary() {
    // 每個中文字 3 bytes；上限 10 bytes 只裝得下 3 個字（9 bytes），不得切出半個字
    String value = "測試切斷邊界";
    String truncated = TextColumnUtils.truncateToUtf8Bytes(value, 10);
    assertThat(truncated).isEqualTo("測試切");
    assertThat(truncated.getBytes(StandardCharsets.UTF_8).length).isLessThanOrEqualTo(10);
  }

  @Test
  void truncateToUtf8Bytes_surrogatePairAtBoundary_dropsWholePair() {
    // 😀 是 4-byte surrogate pair；上限 5 bytes 裝不下第二個 emoji，不得留下半個 pair
    String value = "a😀😀";
    String truncated = TextColumnUtils.truncateToUtf8Bytes(value, 5);
    assertThat(truncated).isEqualTo("a😀");
  }
}
