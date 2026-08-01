package com.erd.cowork.storage;

import java.util.UUID;

/** Static utility for building and sanitizing storage keys. Never instantiate. */
public final class StorageKeyUtils {

  private StorageKeyUtils() {}

  /**
   * Builds a storage key in the format {@code {sessionId}/{UUID}_{safeName}}.
   *
   * @param sessionId the session identifier used as the top-level directory component
   * @param originalFilename the original filename (may contain path separators or special chars)
   * @return a safe, unique storage key
   */
  public static String buildKey(String sessionId, String originalFilename) {
    String safeName = sanitize(originalFilename);
    return sessionId + "/" + UUID.randomUUID() + "_" + safeName;
  }

  /**
   * Strips directory components and replaces filesystem-unsafe characters with underscores.
   *
   * <p>Pure string processing — the untrusted filename never reaches a filesystem API ({@code
   * Paths.get} throws {@code InvalidPathException} on NUL and other illegal characters, and its
   * parsing rules are platform-dependent).
   *
   * <ul>
   *   <li>null, empty, or separator-only filename → {@code "unnamed"}
   *   <li>Path traversal ({@code ../../etc/passwd}, {@code ..\..\etc\passwd}) → basename only
   *   <li>Characters {@code \ / : * ? " < > |} and control characters → {@code _}
   * </ul>
   *
   * @param filename the raw filename; may be null or contain path separators
   * @return a sanitized filename safe for use as a storage key component
   */
  public static String sanitize(String filename) {
    if (filename == null) {
      return "unnamed";
    }
    int lastSeparator = Math.max(filename.lastIndexOf('/'), filename.lastIndexOf('\\'));
    String base = filename.substring(lastSeparator + 1);
    if (base.isEmpty()) {
      return "unnamed";
    }
    return base.replaceAll("[\\\\/:*?\"<>|\\p{Cntrl}]", "_");
  }
}
