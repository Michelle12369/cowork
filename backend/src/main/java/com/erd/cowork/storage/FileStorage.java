package com.erd.cowork.storage;

import java.io.IOException;
import java.io.InputStream;

public interface FileStorage {

  /** Streams content to storage and returns the storage key. */
  String store(StorageCategory category, String sessionId, String originalFilename, InputStream in)
      throws IOException;

  InputStream read(String storageKey) throws IOException;

  void delete(String storageKey) throws IOException;
}
