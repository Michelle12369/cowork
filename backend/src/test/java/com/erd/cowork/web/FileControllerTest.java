package com.erd.cowork.web;

import static org.assertj.core.api.Assertions.assertThat;

import com.erd.cowork.domain.UploadedFile;
import com.erd.cowork.repo.UploadedFileRepository;
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
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.test.context.TestPropertySource;
import org.springframework.util.LinkedMultiValueMap;
import org.springframework.util.MultiValueMap;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@TestPropertySource(
    properties = {
      "erd.storage.local-dir=${java.io.tmpdir}/erd-cowork-test-files",
      // 固定停用 tsso,讓 CurrentUserFilter 一定註冊(internal 的 tsso.enabled=true 會停掉它、走 SSO)
      "tsso.enabled=false"
    })
class FileControllerTest {

  @Autowired TestRestTemplate rest;
  @Autowired UploadedFileRepository files;

  private static final Path TEST_STORAGE_DIR =
      Paths.get(System.getProperty("java.io.tmpdir"), "erd-cowork-test-files");

  @AfterAll
  static void cleanupStorage() throws IOException {
    deleteTestStorage(TEST_STORAGE_DIR);
  }

  static void deleteTestStorage(Path dir) throws IOException {
    if (!Files.exists(dir)) {
      return;
    }
    String abs = dir.toAbsolutePath().toString();
    if (!abs.contains("erd-cowork")) {
      throw new IllegalStateException("refusing to delete non-test path: " + abs);
    }
    try (Stream<Path> walk = Files.walk(dir)) {
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

  private static HttpHeaders userHeaders(String userId) {
    HttpHeaders headers = new HttpHeaders();
    headers.set("X-User-Id", userId);
    return headers;
  }

  /**
   * Returns a fresh session UUID. The session is auto-created by the upsert mechanism on the
   * caller's first file upload or message — no explicit create call is needed.
   */
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
  void uploadCsv_valid_returns201WithAliasAndRowCount() {
    String sessionId = createSession("u1");
    ResponseEntity<String> response =
        rest.postForEntity(
            "/api/sessions/" + sessionId + "/files",
            multipart("u1", "lots.csv", "lot,vt\n95,0.419\n96,0.423\n"),
            String.class);
    assertThat(response.getStatusCode()).isEqualTo(HttpStatus.CREATED);
    assertThat(response.getBody())
        .contains("\"alias\":\"lots\"")
        .contains("\"rowCount\":2")
        .contains("\"type\":\"csv\"");
  }

  @Test
  void uploadSecondFile_getsDistinctSlugAlias() {
    String sessionId = createSession("u1");
    rest.postForEntity(
        "/api/sessions/" + sessionId + "/files", multipart("u1", "a.csv", "x\n1\n"), String.class);
    ResponseEntity<String> response2 =
        rest.postForEntity(
            "/api/sessions/" + sessionId + "/files",
            multipart("u1", "b.csv", "y\n2\n"),
            String.class);
    assertThat(response2.getBody()).contains("\"alias\":\"b\"");
  }

  @Test
  void uploadSixthFile_returns400UploadLimit() {
    String sessionId = createSession("u1");
    for (int index = 0; index < 5; index++) {
      rest.postForEntity(
          "/api/sessions/" + sessionId + "/files",
          multipart("u1", "f" + index + ".csv", "c\n1\n"),
          String.class);
    }
    ResponseEntity<String> response =
        rest.postForEntity(
            "/api/sessions/" + sessionId + "/files",
            multipart("u1", "f6.csv", "c\n1\n"),
            String.class);
    assertThat(response.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
    assertThat(response.getBody()).contains("UPLOAD_LIMIT");
  }

  @Test
  void uploadUnsupportedExtension_returns400UnsupportedType() {
    String sessionId = createSession("u1");
    ResponseEntity<String> response =
        rest.postForEntity(
            "/api/sessions/" + sessionId + "/files",
            multipart("u1", "x.pdf", "junk"),
            String.class);
    assertThat(response.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
    assertThat(response.getBody()).contains("UNSUPPORTED_TYPE");
  }

  @Test
  void uploadTsvFile_returns400UnsupportedType() {
    String sessionId = createSession("u1");
    ResponseEntity<String> response =
        rest.postForEntity(
            "/api/sessions/" + sessionId + "/files",
            multipart("u1", "data.tsv", "col1\tcol2\nval1\tval2\n"),
            String.class);
    assertThat(response.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
    assertThat(response.getBody()).contains("UNSUPPORTED_TYPE");
  }

  @Test
  void uploadTxtFile_returns400UnsupportedType() {
    String sessionId = createSession("u1");
    ResponseEntity<String> response =
        rest.postForEntity(
            "/api/sessions/" + sessionId + "/files",
            multipart("u1", "notes.txt", "some text content\n"),
            String.class);
    assertThat(response.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
    assertThat(response.getBody()).contains("UNSUPPORTED_TYPE");
  }

  @Test
  void uploadToOthersSession_returns404() {
    // Establish the session under u1 first, then attempt to upload as u2.
    String sessionId = UUID.randomUUID().toString();
    rest.postForEntity(
        "/api/sessions/" + sessionId + "/files",
        multipart("u1", "seed.csv", "x\n1\n"),
        String.class);

    ResponseEntity<String> response =
        rest.postForEntity(
            "/api/sessions/" + sessionId + "/files",
            multipart("u2", "a.csv", "c\n1\n"),
            String.class);
    assertThat(response.getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
  }

  @Test
  void deleteFile_removesIt_andSessionDetailReflects() {
    String sessionId = createSession("u1");
    ResponseEntity<String> up =
        rest.postForEntity(
            "/api/sessions/" + sessionId + "/files",
            multipart("u1", "a.csv", "c\n1\n"),
            String.class);
    String fileId = up.getBody().replaceAll(".*\"id\":\"([^\"]+)\".*", "$1");
    ResponseEntity<Void> del =
        rest.exchange(
            "/api/sessions/" + sessionId + "/files/" + fileId,
            HttpMethod.DELETE,
            new HttpEntity<Void>(userHeaders("u1")),
            Void.class);
    assertThat(del.getStatusCode()).isEqualTo(HttpStatus.NO_CONTENT);
    ResponseEntity<String> detail =
        rest.exchange(
            "/api/sessions/" + sessionId,
            HttpMethod.GET,
            new HttpEntity<Void>(userHeaders("u1")),
            String.class);
    assertThat(detail.getBody()).contains("\"files\":[]");
  }

  @Test
  void uploadBatchWithOneBadFile_rollsBackWholeBatch_noOrphans() throws IOException {
    String sessionId = createSession("batch-user");

    // good CSV
    ByteArrayResource goodCsv =
        new ByteArrayResource("col\n1\n2\n".getBytes(StandardCharsets.UTF_8)) {
          @Override
          public String getFilename() {
            return "good.csv";
          }
        };
    // .xlsx with garbage bytes — passes validate() (xlsx is allowed), gets stored,
    // then parse() throws ParseException → triggers orphan cleanup
    ByteArrayResource badXlsx =
        new ByteArrayResource("THIS_IS_NOT_XLSX_GARBAGE".getBytes(StandardCharsets.UTF_8)) {
          @Override
          public String getFilename() {
            return "bad.xlsx";
          }
        };

    MultiValueMap<String, Object> body = new LinkedMultiValueMap<>();
    body.add("files", goodCsv);
    body.add("files", badXlsx);
    HttpHeaders headers = userHeaders("batch-user");
    headers.setContentType(MediaType.MULTIPART_FORM_DATA);
    HttpEntity<MultiValueMap<String, Object>> request = new HttpEntity<>(body, headers);

    ResponseEntity<String> response =
        rest.postForEntity("/api/sessions/" + sessionId + "/files", request, String.class);

    // whole batch rejected
    assertThat(response.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
    assertThat(response.getBody()).contains("PARSE_ERROR");

    // DB rolled back — session has no files
    ResponseEntity<String> detail =
        rest.exchange(
            "/api/sessions/" + sessionId,
            HttpMethod.GET,
            new HttpEntity<Void>(userHeaders("batch-user")),
            String.class);
    assertThat(detail.getBody()).contains("\"files\":[]");

    // storage cleaned up — session dir absent or empty
    Path sessionDir =
        Paths.get(System.getProperty("java.io.tmpdir"), "erd-cowork-test-files", sessionId);
    if (Files.exists(sessionDir)) {
      try (Stream<Path> entries = Files.list(sessionDir)) {
        assertThat(entries.toList()).as("orphaned files found under session storage dir").isEmpty();
      }
    }
  }

  @Test
  void uploadCsv_expiredFileOverQuota_stillReturns201() {
    String sessionId = createSession("u1");

    // Establish the session via upsert before inserting the expired file directly into the DB.
    rest.postForEntity(
        "/api/sessions/" + sessionId + "/files",
        multipart("u1", "seed.csv", "x\n0\n"),
        String.class);

    // Seed an expired file whose size alone blows past the 5GB session cap. Because it is expired,
    // its bytes must NOT count towards the quota, so a small csv upload should still succeed.
    UploadedFile expired = new UploadedFile();
    expired.setSessionId(sessionId);
    expired.setName("old-huge.csv");
    expired.setAlias("file1");
    expired.setStorageKey("expired/old-huge.csv");
    expired.setSizeBytes(6L * 1024 * 1024 * 1024); // 6GB > 5GB session limit
    expired.setType("csv");
    expired.setExpired(true);
    files.save(expired);

    ResponseEntity<String> response =
        rest.postForEntity(
            "/api/sessions/" + sessionId + "/files",
            multipart("u1", "small.csv", "lot,vt\n1,0.5\n"),
            String.class);

    assertThat(response.getStatusCode()).isEqualTo(HttpStatus.CREATED);
    // slug-based aliasing: "small.csv" → slug "small"; no collision with the expired file's
    // legacy alias "file1", so the alias is simply "small"
    assertThat(response.getBody()).contains("\"alias\":\"small\"");
  }

  @Test
  void uploadFile_sameSlugAsExpiredFile_getsDeduplicatedAlias() {
    String sessionId = createSession("uexpired");

    // Establish the session via upsert before inserting the expired file directly into the DB.
    rest.postForEntity(
        "/api/sessions/" + sessionId + "/files",
        multipart("uexpired", "seed.csv", "x\n0\n"),
        String.class);

    // Seed an expired file whose alias is the same slug that the next upload would produce.
    UploadedFile expired = new UploadedFile();
    expired.setSessionId(sessionId);
    expired.setName("sales.csv");
    expired.setAlias("sales");
    expired.setStorageKey("expired/" + sessionId + "/sales.csv");
    expired.setSizeBytes(100L);
    expired.setType("csv");
    expired.setExpired(true);
    files.save(expired);

    // "Sales.csv" lowercases to the same slug "sales" — already occupied by the expired file.
    ResponseEntity<String> response =
        rest.postForEntity(
            "/api/sessions/" + sessionId + "/files",
            multipart("uexpired", "Sales.csv", "col\n1\n"),
            String.class);

    assertThat(response.getStatusCode()).isEqualTo(HttpStatus.CREATED);
    // expired file's alias "sales" is respected → collision → deduplication suffix applied
    assertThat(response.getBody()).contains("\"alias\":\"sales_2\"");
  }

  @Test
  void uploadToUnknownSession_validUuid_createsSessionAndStoresFile() {
    String freshSessionId = UUID.randomUUID().toString();
    String userId = "upsert-owner";

    // First upload to a session that does not yet exist → upsert creates it (201)
    ResponseEntity<String> uploadResponse =
        rest.postForEntity(
            "/api/sessions/" + freshSessionId + "/files",
            multipart(userId, "data.csv", "col\n42\n"),
            String.class);
    assertThat(uploadResponse.getStatusCode()).isEqualTo(HttpStatus.CREATED);
    assertThat(uploadResponse.getBody()).contains("\"alias\":\"data\"");

    // Same user can GET the newly created session and sees the uploaded file
    ResponseEntity<String> detail =
        rest.exchange(
            "/api/sessions/" + freshSessionId,
            HttpMethod.GET,
            new HttpEntity<Void>(userHeaders(userId)),
            String.class);
    assertThat(detail.getStatusCode()).isEqualTo(HttpStatus.OK);
    assertThat(detail.getBody()).contains("\"files\":[{");

    // A different user gets 404 — session ownership is enforced
    ResponseEntity<String> forbiddenDetail =
        rest.exchange(
            "/api/sessions/" + freshSessionId,
            HttpMethod.GET,
            new HttpEntity<Void>(userHeaders("other-user")),
            String.class);
    assertThat(forbiddenDetail.getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
  }
}
