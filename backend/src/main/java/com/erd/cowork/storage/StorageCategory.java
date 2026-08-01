package com.erd.cowork.storage;

/**
 * Top-level storage key namespace. Keeps uploads and generated artifacts in separate directory
 * trees so disk usage can be attributed per data class and the two can later live on separate
 * volumes. Legacy keys written before this split have no prefix and resolve unchanged.
 */
public enum StorageCategory {
  UPLOAD("uploads"),
  ARTIFACT("artifacts");

  private final String prefix;

  StorageCategory(String prefix) {
    this.prefix = prefix;
  }

  public String prefix() {
    return prefix;
  }
}
