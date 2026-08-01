package com.erd.cowork.agent.extraction;

import com.erd.cowork.parsing.TextSearchUtils;
import org.springframework.util.StringUtils;

/**
 * Static utility for extracting a complete bare HTML document (no {@code ```html} fence) from free
 * text.
 *
 * <p>non-bean: static utility class; never Spring-inject.
 */
public final class BareHtmlUtils {

  private BareHtmlUtils() {
    throw new UnsupportedOperationException("Utility class");
  }

  /**
   * Extracts a complete bare HTML document from free text. The document starts at the first
   * case-insensitive {@code <!doctype html} (or, failing that, {@code <html}) and ends at the first
   * subsequent {@code </html>} (inclusive). Returns {@code null} when no complete document is
   * present.
   */
  public static String extract(String text) {
    if (!StringUtils.hasText(text)) {
      return null;
    }
    int start = TextSearchUtils.indexOfIgnoreCase(text, "<!doctype html");
    if (start < 0) {
      start = TextSearchUtils.indexOfIgnoreCase(text, "<html");
    }
    if (start < 0) {
      return null;
    }
    int end = TextSearchUtils.indexOfIgnoreCase(text, "</html>", start);
    if (end < 0) {
      return null;
    }
    return text.substring(start, end + "</html>".length());
  }
}
