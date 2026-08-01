package com.erd.cowork.web;

import static org.assertj.core.api.Assertions.assertThat;

import java.io.IOException;
import java.io.UncheckedIOException;
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
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.test.context.TestPropertySource;

/**
 * End-to-end check that the built-in sample dataset actually resolves through the real {@link
 * com.erd.cowork.service.FileService} chain — classpath resource lookup, storage, parsing, and
 * alias generation — not just the mocked slice tests in {@link SampleDatasetControllerTest}.
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@TestPropertySource(
    properties = "erd.storage.local-dir=${java.io.tmpdir}/erd-cowork-test-sample-files")
class SampleDatasetControllerIntegrationTest {

  @Autowired TestRestTemplate rest;

  private static final Path TEST_STORAGE_DIR =
      Paths.get(System.getProperty("java.io.tmpdir"), "erd-cowork-test-sample-files");

  @AfterAll
  static void cleanupStorage() throws IOException {
    if (!Files.exists(TEST_STORAGE_DIR)) {
      return;
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

  private HttpEntity<Void> userRequest(String userId) {
    HttpHeaders headers = new HttpHeaders();
    headers.set("X-User-Id", userId);
    return new HttpEntity<>(headers);
  }

  @Test
  void listSamples_returnsProductUsageFeedbackDataset() {
    ResponseEntity<String> response = rest.getForEntity("/api/samples", String.class);

    assertThat(response.getStatusCode()).isEqualTo(HttpStatus.OK);
    assertThat(response.getBody())
        .contains("\"name\":\"product-usage-feedback\"")
        .contains("\"usage_log\"")
        .contains("\"feedback\"");
  }

  @Test
  void loadSample_realClasspathFiles_returns201WithParsedRowCounts() {
    String sessionId = UUID.randomUUID().toString();

    ResponseEntity<String> response =
        rest.exchange(
            "/api/sessions/" + sessionId + "/files/samples/product-usage-feedback",
            HttpMethod.POST,
            userRequest("sample-loader"),
            String.class);

    assertThat(response.getStatusCode()).isEqualTo(HttpStatus.CREATED);
    assertThat(response.getBody())
        .contains("\"alias\":\"usage_log\"")
        .contains("\"alias\":\"feedback\"")
        .contains("\"rowCount\":2000")
        .contains("\"rowCount\":300")
        .contains("\"type\":\"csv\"");
  }

  @Test
  void loadSample_unknownName_returns404() {
    String sessionId = UUID.randomUUID().toString();

    ResponseEntity<String> response =
        rest.exchange(
            "/api/sessions/" + sessionId + "/files/samples/does-not-exist",
            HttpMethod.POST,
            userRequest("sample-loader"),
            String.class);

    assertThat(response.getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
  }

  @Test
  void loadSample_othersSession_returns404() {
    String sessionId = UUID.randomUUID().toString();
    // Establish session ownership under "owner" first.
    rest.exchange(
        "/api/sessions/" + sessionId + "/files/samples/product-usage-feedback",
        HttpMethod.POST,
        userRequest("owner"),
        String.class);

    ResponseEntity<String> response =
        rest.exchange(
            "/api/sessions/" + sessionId + "/files/samples/product-usage-feedback",
            HttpMethod.POST,
            userRequest("someone-else"),
            String.class);

    assertThat(response.getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
  }
}
