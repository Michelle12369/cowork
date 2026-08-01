package com.erd.cowork.service;

import java.nio.charset.StandardCharsets;
import java.util.Locale;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Pure static utility that converts an uploaded filename into a concise, human-readable alias
 * suitable for use as a {@code window.__ERD_DATA__} key in generated dashboards, and also derives
 * the display name that appears in the UI.
 *
 * <p>Slug rules: strip the extension, lowercase, keep Unicode letters/digits (CJK intact), map
 * everything else to underscores (collapsed, trimmed), cap at {@value #MAX_ALIAS_UTF8_BYTES} UTF-8
 * bytes. Empty results fall back to {@code file{n}}. Collisions get a {@code _N} suffix, truncating
 * the body first if needed to stay within the byte cap.
 *
 * <p>Oracle VARCHAR2 uses BYTE semantics (not char count), so this class counts UTF-8 bytes rather
 * than Java {@code char} units at every truncation point — H2 (unit tests) uses character
 * semantics, so a naive char-based truncation would pass tests but overflow the Oracle column.
 *
 * <p>non-bean: static utility class; instantiation is neither needed nor allowed.
 */
public final class FileAliasUtils {

  /**
   * Maximum UTF-8 byte length for a generated alias. The {@code alias} column is {@code
   * VARCHAR2(100 BYTE)}; 60 bytes leaves headroom for the ASCII {@code _N} dedup suffix.
   */
  static final int MAX_ALIAS_UTF8_BYTES = 60;

  /**
   * Maximum UTF-8 byte length for a display name stored in the {@code name} column ({@code
   * VARCHAR2(500 BYTE)}); 400 bytes leaves room for the extension.
   */
  static final int MAX_NAME_UTF8_BYTES = 400;

  /**
   * Result of alias generation, pairing the resolved alias with the deduplication suffix number.
   *
   * @param alias the unique alias for use as a {@code window.__ERD_DATA__} key; never empty and
   *     within {@value #MAX_ALIAS_UTF8_BYTES} UTF-8 bytes
   * @param suffixNumber {@code 0} when no suffix was appended (first occurrence of this slug),
   *     {@code ≥ 2} for deduplicated aliases (the number appended as {@code _N})
   */
  public record AliasResolution(String alias, int suffixNumber) {}

  private static final Pattern FALLBACK_ALIAS_PATTERN = Pattern.compile("^file(\\d+)$");

  private FileAliasUtils() {
    throw new UnsupportedOperationException("Utility class");
  }

  /**
   * Derives a unique alias and the associated deduplication suffix number for {@code filename} that
   * does not appear in {@code occupiedAliases}.
   *
   * <p>Callers MUST add {@link AliasResolution#alias()} to their occupied-alias tracking set before
   * invoking this method for the next file in the same batch (intra-batch collision avoidance).
   *
   * @param filename original filename, with or without extension
   * @param occupiedAliases all aliases already in use for the session, including expired files
   * @return an {@link AliasResolution} whose alias is non-empty, unique, and at most {@value
   *     #MAX_ALIAS_UTF8_BYTES} UTF-8 bytes; {@link AliasResolution#suffixNumber()} is {@code 0}
   *     when no suffix was needed, {@code ≥ 2} otherwise
   */
  public static AliasResolution generateAlias(String filename, Set<String> occupiedAliases) {
    String slug = toSlug(stripExtension(filename));
    if (slug.isEmpty()) {
      return new AliasResolution(nextFallbackAlias(occupiedAliases), 0);
    }
    return resolveCollision(slug, occupiedAliases);
  }

  /**
   * Builds the display name stored in the {@code name} column: lowercases the full filename (using
   * {@link Locale#ROOT}) and inserts the deduplication suffix before the extension when {@code
   * suffixNumber ≥ 1}. If the result exceeds {@value #MAX_NAME_UTF8_BYTES} UTF-8 bytes, the stem is
   * truncated (without splitting multi-byte code points) to restore compliance.
   *
   * @param filename original filename as supplied by the multipart upload
   * @param suffixNumber deduplication suffix from {@link AliasResolution#suffixNumber()}; {@code 0}
   *     means no suffix is inserted
   * @return lowercased, optionally suffixed, byte-safe display name
   */
  static String buildDisplayName(String filename, int suffixNumber) {
    String lowercased = filename.toLowerCase(Locale.ROOT);
    int dotIndex = lowercased.lastIndexOf('.');
    String stem;
    String extWithDot;
    if (dotIndex > 0) {
      stem = lowercased.substring(0, dotIndex);
      extWithDot = lowercased.substring(dotIndex);
    } else {
      stem = lowercased;
      extWithDot = "";
    }
    String suffixPart = suffixNumber > 0 ? "_" + suffixNumber : "";
    String fullName = stem + suffixPart + extWithDot;
    if (fullName.getBytes(StandardCharsets.UTF_8).length <= MAX_NAME_UTF8_BYTES) {
      return fullName;
    }
    int overheadBytes = (suffixPart + extWithDot).getBytes(StandardCharsets.UTF_8).length;
    int allowedStemBytes = Math.max(0, MAX_NAME_UTF8_BYTES - overheadBytes);
    return truncateToUtf8Bytes(stem, allowedStemBytes) + suffixPart + extWithDot;
  }

  // --- package-private for unit testing ---

  static String stripExtension(String filename) {
    int dot = filename.lastIndexOf('.');
    // dot == -1 → no extension; dot == 0 → dot-file like ".gitignore" — keep as-is
    return (dot > 0) ? filename.substring(0, dot) : filename;
  }

  static String toSlug(String stem) {
    String lowercased = stem.toLowerCase(Locale.ROOT);
    String nonAlphaReplaced = lowercased.replaceAll("[^\\p{L}\\p{N}]", "_");
    String underscoresCollapsed = nonAlphaReplaced.replaceAll("_+", "_");
    String underscoresTrimmed = underscoresCollapsed.replaceAll("^_+|_+$", "");
    return truncateToUtf8Bytes(underscoresTrimmed, MAX_ALIAS_UTF8_BYTES);
  }

  static AliasResolution resolveCollision(String slug, Set<String> occupiedAliases) {
    if (!occupiedAliases.contains(slug)) {
      return new AliasResolution(slug, 0);
    }
    for (int suffix = 2; ; suffix++) {
      String suffixPart = "_" + suffix;
      // suffixPart is always ASCII so its byte length equals its char length
      int maxBodyBytes = MAX_ALIAS_UTF8_BYTES - suffixPart.length();
      String body = truncateToUtf8Bytes(slug, maxBodyBytes);
      String candidate = body + suffixPart;
      if (!occupiedAliases.contains(candidate)) {
        return new AliasResolution(candidate, suffix);
      }
    }
  }

  /**
   * Truncates {@code text} so that its UTF-8 byte representation does not exceed {@code maxBytes},
   * without splitting multi-byte code points.
   *
   * <p>Returns {@code text} unchanged when it already fits. Returns an empty string when {@code
   * maxBytes ≤ 0}.
   */
  static String truncateToUtf8Bytes(String text, int maxBytes) {
    if (maxBytes <= 0) {
      return "";
    }
    if (text.getBytes(StandardCharsets.UTF_8).length <= maxBytes) {
      return text;
    }
    int byteCount = 0;
    int charIndex = 0;
    while (charIndex < text.length()) {
      int codePoint = text.codePointAt(charIndex);
      int cpBytes = utf8ByteCount(codePoint);
      if (byteCount + cpBytes > maxBytes) {
        break;
      }
      byteCount += cpBytes;
      charIndex += Character.charCount(codePoint);
    }
    return text.substring(0, charIndex);
  }

  private static int utf8ByteCount(int codePoint) {
    if (codePoint < 0x80) return 1;
    if (codePoint < 0x800) return 2;
    if (codePoint < 0x10000) return 3;
    return 4;
  }

  private static String nextFallbackAlias(Set<String> occupiedAliases) {
    int maximumNumber = 0;
    for (String existingAlias : occupiedAliases) {
      Matcher matcher = FALLBACK_ALIAS_PATTERN.matcher(existingAlias);
      if (matcher.matches()) {
        maximumNumber = Math.max(maximumNumber, Integer.parseInt(matcher.group(1)));
      }
    }
    return "file" + (maximumNumber + 1);
  }
}
