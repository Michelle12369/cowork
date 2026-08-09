package com.erd.cowork.storage;

import static org.assertj.core.api.Assertions.assertThat;

import java.io.ByteArrayInputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import org.junit.jupiter.api.Test;

class PassthroughUploadDecryptorTest {

  private final PassthroughUploadDecryptor decryptor = new PassthroughUploadDecryptor();

  @Test
  void decrypt_anyContent_returnsBytesUnchanged() throws Exception {
    byte[] original = "col\n1\n".getBytes(StandardCharsets.UTF_8);

    try (InputStream result = decryptor.decrypt(new ByteArrayInputStream(original), "data.csv")) {
      assertThat(result.readAllBytes()).isEqualTo(original);
    }
  }

  @Test
  void decrypt_emptyContent_returnsEmptyStream() throws Exception {
    try (InputStream result = decryptor.decrypt(new ByteArrayInputStream(new byte[0]), "e.csv")) {
      assertThat(result.readAllBytes()).isEmpty();
    }
  }
}
