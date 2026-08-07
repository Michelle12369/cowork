package com.erd.cowork.web;

import static org.assertj.core.api.Assertions.assertThat;

import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.Comparator;
import java.util.UUID;
import java.util.stream.Stream;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.client.TestRestTemplate;
import org.springframework.core.io.ByteArrayResource;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.test.context.TestPropertySource;
import org.springframework.util.LinkedMultiValueMap;
import org.springframework.util.MultiValueMap;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@TestPropertySource(
    properties = {
      "erd.upload.max-csv-bytes=10",
      "erd.storage.local-dir=${java.io.tmpdir}/erd-cowork-test-files",
      "tsso.enabled=false"
    })
class FileControllerLimitTest {

  @Autowired TestRestTemplate rest;

  private static final Path TEST_STORAGE_DIR =
      Paths.get(System.getProperty("java.io.tmpdir"), "erd-cowork-test-files");

  @AfterAll
  static void cleanupStorage() throws IOException {
    if (!Files.exists(TEST_STORAGE_DIR)) {
      return;
    }
    String abs = TEST_STORAGE_DIR.toAbsolutePath().toString();
    if (!abs.contains("erd-cowork")) {
      throw new IllegalStateException("refusing to delete non-test path: " + abs);
    }
    try (Stream<Path> walk = Files.walk(TEST_STORAGE_DIR)) {
      walk.sorted(Comparator.reverseOrder())
          .forEach(
              filePath -> {
                try {
                  Files.deleteIfExists(filePath);
                } catch (IOException exception) {
                  throw new UncheckedIOException(exception);
                }
              });
    }
  }

  record SessionSummary(String id, String title, String updatedAt) {}

  private static HttpHeaders userHeaders(String userId) {
    HttpHeaders headers = new HttpHeaders();
    headers.set("X-User-Id", userId);
    return headers;
  }

  private String createSession(String userId) {
    return UUID.randomUUID().toString();
  }

  private HttpEntity<MultiValueMap<String, Object>> multipart(
      String userId, String filename, String content) {
    ByteArrayResource resource =
        new ByteArrayResource(content.getBytes(StandardCharsets.UTF_8)) {
          @Override
          public String getFilename() {
            return filename;
          }
        };
    MultiValueMap<String, Object> body = new LinkedMultiValueMap<>();
    body.add("files", resource);
    HttpHeaders headers = userHeaders(userId);
    headers.setContentType(MediaType.MULTIPART_FORM_DATA);
    return new HttpEntity<>(body, headers);
  }

  @Test
  void uploadOversizeCsv_returns400UploadLimit() {
    String sessionId = createSession("limit-user");
    // 11 bytes > max-csv-bytes=10
    ResponseEntity<String> response =
        rest.postForEntity(
            "/api/sessions/" + sessionId + "/files",
            multipart("limit-user", "big.csv", "col\n12345678\n"),
            String.class);
    assertThat(response.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
    assertThat(response.getBody()).contains("UPLOAD_LIMIT");
  }
}
