package com.erd.cowork.storage;

import java.io.IOException;
import java.io.InputStream;

public interface FileStorage {

  /**
   * Streams content to storage and returns the storage key.
   *
   * <p>Implementations MUST fully consume {@code in} to EOF: the caller measures the stored size by
   * counting bytes read from the same stream (wrapped in a {@code CountingInputStream}), so a short
   * read would desync the recorded size from what was actually written.
   */
  String store(StorageCategory category, String sessionId, String originalFilename, InputStream in)
      throws IOException;

  InputStream read(String storageKey) throws IOException;

  void delete(String storageKey) throws IOException;
}
