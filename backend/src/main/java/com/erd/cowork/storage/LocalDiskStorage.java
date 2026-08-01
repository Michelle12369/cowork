package com.erd.cowork.storage;

import com.erd.cowork.config.StorageProperties;
import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardCopyOption;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;

@Slf4j
@Component
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
    Files.copy(in, target, StandardCopyOption.REPLACE_EXISTING);
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
