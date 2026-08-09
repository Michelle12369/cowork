package com.erd.cowork.parsing;

import java.nio.ByteBuffer;
import java.nio.CharBuffer;
import java.nio.charset.CharsetEncoder;
import java.nio.charset.StandardCharsets;

/** DB TEXT 欄位（64KB bytes 上限）的 UTF-8 截斷工具。 */
public final class TextColumnUtils {

  /** MariaDB TEXT 實際上限 65_535 bytes；留 headroom 取 65_000。 */
  public static final int TEXT_COLUMN_MAX_BYTES = 65_000;

  private TextColumnUtils() {
    throw new UnsupportedOperationException();
  }

  /** 依 UTF-8 byte 長度截斷；不足上限時原樣返回（同一實例），不切斷 code point。 */
  public static String truncateToUtf8Bytes(String value, int maxBytes) {
    if (value == null || value.getBytes(StandardCharsets.UTF_8).length <= maxBytes) {
      return value;
    }
    CharsetEncoder encoder = StandardCharsets.UTF_8.newEncoder();
    ByteBuffer encoded = ByteBuffer.allocate(maxBytes);
    encoder.encode(CharBuffer.wrap(value), encoded, true);
    return new String(encoded.array(), 0, encoded.position(), StandardCharsets.UTF_8);
  }
}
