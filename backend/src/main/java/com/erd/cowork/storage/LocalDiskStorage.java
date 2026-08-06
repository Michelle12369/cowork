package com.erd.cowork.storage;

import com.erd.cowork.config.StorageProperties;
import java.io.IOException;
import java.io.InputStream;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardCopyOption;
import java.util.UUID;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;

@Slf4j
@Component
@ConditionalOnProperty(
    prefix = "erd.storage",
    name = "type",
    havingValue = "local",
    matchIfMissing = true)
public class LocalDiskStorage implements FileStorage {

  private final Path root;

  public LocalDiskStorage(StorageProperties properties) {
    this.root = Paths.get(properties.localDir()).toAbsolutePath().normalize();
  }

  @Override
  public String store(
      StorageCategory category, String sessionId, String originalFilename, InputStream in)
      throws IOException {
    String key = StorageKeyUtils.buildKey(category, sessionId, originalFilename);
    Path target = resolve(key);
    Files.createDirectories(target.getParent());
    // Copy to a sibling temp file first, then move into place: if the stream throws partway
    // through, a straight Files.copy(in, target, ...) would leave partial plaintext at the final
    // path with no DB row to ever clean it up. The temp file lives in the same directory so the
    // move stays on one filesystem and can be atomic.
    Path tempFile = target.resolveSibling(target.getFileName() + ".tmp-" + UUID.randomUUID());
    try {
      Files.copy(in, tempFile, StandardCopyOption.REPLACE_EXISTING);
      try {
        Files.move(
            tempFile, target, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
      } catch (AtomicMoveNotSupportedException atomicMoveNotSupportedException) {
        Files.move(tempFile, target, StandardCopyOption.REPLACE_EXISTING);
      }
    } catch (IOException exception) {
      Files.deleteIfExists(tempFile);
      throw exception;
    }
    log.info("stored file key={} bytes={}", key, Files.size(target));
    return key;
  }

  @Override
  public InputStream read(String storageKey) throws IOException {
    return Files.newInputStream(resolve(storageKey));
  }

  @Override
  public void delete(String storageKey) throws IOException {
    Files.deleteIfExists(resolve(storageKey));
  }

  private Path resolve(String key) throws IOException {
    Path resolved = root.resolve(key).normalize();
    if (!resolved.startsWith(root)) {
      throw new IOException("invalid storage key: " + key);
    }
    return resolved;
  }
}
