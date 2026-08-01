package com.erd.cowork.parsing;

/** Static utility for locale-safe case-insensitive text search. Never instantiate. */
public final class TextSearchUtils {

  private TextSearchUtils() {
    throw new UnsupportedOperationException("Utility class");
  }

  /**
   * Case-insensitive {@code indexOf} via {@link String#regionMatches} on the original string, so
   * the returned index is always a valid offset into {@code text}. Searching a lowercased copy is
   * unsafe for that purpose: some characters (e.g. {@code İ} U+0130) change length when lowercased,
   * shifting every index after them.
   *
   * @return index of the first match at or after {@code fromIndex}, or {@code -1} if none
   */
  public static int indexOfIgnoreCase(String text, String target, int fromIndex) {
    if (text == null || target == null || target.isEmpty()) {
      return -1;
    }
    int lastCandidate = text.length() - target.length();
    for (int index = Math.max(fromIndex, 0); index <= lastCandidate; index++) {
      if (text.regionMatches(true, index, target, 0, target.length())) {
        return index;
      }
    }
    return -1;
  }

  /** Same as {@link #indexOfIgnoreCase(String, String, int)} searching from the start. */
  public static int indexOfIgnoreCase(String text, String target) {
    return indexOfIgnoreCase(text, target, 0);
  }
}
