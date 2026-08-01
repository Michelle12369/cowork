package com.erd.cowork.storage;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.erd.cowork.config.StorageProperties;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class LocalDiskStorageTest {

  @TempDir Path tempDir;
  private LocalDiskStorage storage;

  @BeforeEach
  void setUp() {
    storage = new LocalDiskStorage(new StorageProperties(tempDir.toString(), null, null, null));
  }

  @Test
  void store_thenRead_roundTripsContent() throws IOException {
    String key =
        storage.store(
            StorageCategory.UPLOAD,
            "sess-1",
            "data.csv",
            new ByteArrayInputStream("a,b\n1,2".getBytes(StandardCharsets.UTF_8)));
    assertThat(key).startsWith("uploads/sess-1/").endsWith("_data.csv");
    try (InputStream in = storage.read(key)) {
      assertThat(new String(in.readAllBytes(), StandardCharsets.UTF_8)).isEqualTo("a,b\n1,2");
    }
  }

  @Test
  void store_pathTraversalFilename_isSanitized() throws IOException {
    String key =
        storage.store(
            StorageCategory.UPLOAD,
            "sess-1",
            "../../etc/passwd",
            new ByteArrayInputStream("x".getBytes(StandardCharsets.UTF_8)));
    assertThat(key).doesNotContain("..");
    Path stored = tempDir.resolve(key);
    assertThat(stored.normalize()).startsWith(tempDir);
    assertThat(Files.exists(stored)).isTrue();
  }

  @Test
  void read_maliciousKey_throwsIOException() {
    assertThatThrownBy(() -> storage.read("../../etc/passwd")).isInstanceOf(IOException.class);
  }

  @Test
  void delete_removesFile_andReadThrows() throws IOException {
    String key =
        storage.store(
            StorageCategory.UPLOAD,
            "sess-1",
            "d.csv",
            new ByteArrayInputStream("x".getBytes(StandardCharsets.UTF_8)));
    storage.delete(key);
    assertThatThrownBy(() -> storage.read(key)).isInstanceOf(IOException.class);
  }
}
