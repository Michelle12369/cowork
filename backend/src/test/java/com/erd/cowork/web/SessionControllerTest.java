package com.erd.cowork.web;

import static org.assertj.core.api.Assertions.assertThat;

import java.nio.charset.StandardCharsets;
import java.util.UUID;
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
      "tsso.enabled=false"
    })
class SessionControllerTest {

  @Autowired TestRestTemplate rest;

  record SessionSummary(String id, String title, String updatedAt) {}

  private static HttpEntity<Void> asUser(String userId) {
    HttpHeaders headers = new HttpHeaders();
    headers.set("X-User-Id", userId);
    return new HttpEntity<>(headers);
  }

  /** Creates a session for {@code userId} by uploading a minimal seed CSV via file upsert. */
  private String createSessionViaUpload(String userId) {
    String sessionId = UUID.randomUUID().toString();
    ByteArrayResource resource =
        new ByteArrayResource("x\n1\n".getBytes(StandardCharsets.UTF_8)) {
          @Override
          public String getFilename() {
            return "seed.csv";
          }
        };
    MultiValueMap<String, Object> body = new LinkedMultiValueMap<>();
    body.add("files", resource);
    HttpHeaders headers = new HttpHeaders();
    headers.set("X-User-Id", userId);
    headers.setContentType(MediaType.MULTIPART_FORM_DATA);
    rest.postForEntity(
        "/api/sessions/" + sessionId + "/files", new HttpEntity<>(body, headers), Void.class);
    return sessionId;
  }

  @Test
  void listAndGet_afterSessionCreatedViaUpload_returnsConsistentData() {
    String userId = "lifecycle-" + UUID.randomUUID();
    String sessionId = createSessionViaUpload(userId);

    ResponseEntity<SessionSummary[]> list =
        rest.exchange("/api/sessions", HttpMethod.GET, asUser(userId), SessionSummary[].class);
    assertThat(list.getStatusCode()).isEqualTo(HttpStatus.OK);
    assertThat(list.getBody()).extracting(SessionSummary::id).contains(sessionId);

    ResponseEntity<String> detail =
        rest.exchange("/api/sessions/" + sessionId, HttpMethod.GET, asUser(userId), String.class);
    assertThat(detail.getStatusCode()).isEqualTo(HttpStatus.OK);
    assertThat(detail.getBody()).contains("\"messages\":[]").contains("\"files\":[{");
  }

  @Test
  void listSessions_freshUser_returnsEmptyArray() {
    String freshUserId = "empty-list-" + UUID.randomUUID();
    ResponseEntity<SessionSummary[]> list =
        rest.exchange("/api/sessions", HttpMethod.GET, asUser(freshUserId), SessionSummary[].class);
    assertThat(list.getStatusCode()).isEqualTo(HttpStatus.OK);
    assertThat(list.getBody()).isEmpty();
  }

  @Test
  void getSession_unknownId_returns404() {
    ResponseEntity<String> response =
        rest.exchange("/api/sessions/nope", HttpMethod.GET, asUser("u1"), String.class);
    assertThat(response.getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
    assertThat(response.getBody()).contains("NOT_FOUND");
  }

  @Test
  void getSession_otherUsersSession_returns404() {
    String sessionId = createSessionViaUpload("u1");

    ResponseEntity<String> response =
        rest.exchange("/api/sessions/" + sessionId, HttpMethod.GET, asUser("u2"), String.class);
    assertThat(response.getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
    assertThat(response.getBody()).contains("NOT_FOUND");
  }

  // ── selectedGroups exposure (§11.6 session-lock — frontend reload restore) ─────

  @Test
  void getSession_beforeAnyMessage_selectedGroupsIsNull() {
    // A session created via file-upload only (no message sent yet) is 未定案 — the DTO must
    // surface that as a null field so the frontend knows the connector picker is still unlocked.
    String userId = "selectedgroups-null-" + UUID.randomUUID();
    String sessionId = createSessionViaUpload(userId);

    ResponseEntity<String> detail =
        rest.exchange("/api/sessions/" + sessionId, HttpMethod.GET, asUser(userId), String.class);
    assertThat(detail.getStatusCode()).isEqualTo(HttpStatus.OK);
    assertThat(detail.getBody()).contains("\"selectedGroups\":null");
  }
}
