package com.erd.cowork.agent.provider.analysis;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.erd.cowork.agent.repair.BrowserJsError;
import com.erd.cowork.agent.repair.BrowserRepairOutcome;
import com.erd.cowork.config.AnalysisAgentProperties;
import com.erd.cowork.exception.AnalysisBrowserRepairFailedException;
import java.util.List;
import java.util.concurrent.TimeUnit;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okhttp3.mockwebserver.RecordedRequest;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;

class AnalysisBrowserRepairClientTest {

  private static final int DEFAULT_TEST_TIMEOUT_SECONDS = 30;
  private static final int DEFAULT_TEST_MAX_IN_MEMORY_SIZE_MB = 64;

  private MockWebServer mockWebServer;
  private AnalysisBrowserRepairClient client;

  @BeforeEach
  void setUp() throws Exception {
    mockWebServer = new MockWebServer();
    mockWebServer.start();
    client = newClient(mockWebServer, DEFAULT_TEST_TIMEOUT_SECONDS);
  }

  @AfterEach
  void tearDown() throws Exception {
    mockWebServer.shutdown();
  }

  private static AnalysisBrowserRepairClient newClient(
      MockWebServer mockWebServer, int requestTimeoutSeconds) {
    AnalysisAgentProperties analysisProperties =
        new AnalysisAgentProperties(
            "http://localhost:" + mockWebServer.getPort(),
            "/data/uploads",
            requestTimeoutSeconds,
            DEFAULT_TEST_MAX_IN_MEMORY_SIZE_MB);
    return new AnalysisBrowserRepairClient(analysisProperties, WebClient.builder());
  }

  // ── 200: success ───────────────────────────────────────────────────────────

  @Test
  void repair_200Response_returnsPassedOutcomeWithRepairedHtml() {
    mockWebServer.enqueue(
        new MockResponse()
            .setResponseCode(200)
            .addHeader("Content-Type", "application/json")
            .setBody("{\"html\":\"<html>fixed</html>\"}"));

    BrowserRepairOutcome outcome =
        client
            .repair(
                "session-1",
                "user-1",
                "<html>broken</html>",
                List.of(new BrowserJsError("TypeError: x is undefined", 42, 0)))
            .block();

    assertThat(outcome).isEqualTo(new BrowserRepairOutcome("<html>fixed</html>", true));
  }

  @Test
  void repair_200Response_requestBodyCarriesSessionUserHtmlAndErrorsWithLineCol() throws Exception {
    mockWebServer.enqueue(
        new MockResponse()
            .setResponseCode(200)
            .addHeader("Content-Type", "application/json")
            .setBody("{\"html\":\"<html>fixed</html>\"}"));

    client
        .repair(
            "session-1",
            "user-1",
            "<html>broken</html>",
            List.of(new BrowserJsError("TypeError: x is undefined", 42, 7)))
        .block();

    RecordedRequest request = mockWebServer.takeRequest();
    assertThat(request.getPath()).isEqualTo("/repair");
    String body = request.getBody().readUtf8();
    assertThat(body).contains("\"sessionId\":\"session-1\"");
    assertThat(body).contains("\"userId\":\"user-1\"");
    assertThat(body).contains("\"html\":\"<html>broken</html>\"");
    assertThat(body).contains("\"message\":\"TypeError: x is undefined\"");
    // line/col are forwarded too — deepagent-service quotes the offending source line into the
    // repair prompt using them.
    assertThat(body).contains("\"line\":42");
    assertThat(body).contains("\"col\":7");
  }

  // ── 422: guard rejected ────────────────────────────────────────────────────

  @Test
  void repair_422Response_returnsNotPassedOutcomeWithOriginalHtml() {
    mockWebServer.enqueue(
        new MockResponse()
            .setResponseCode(422)
            .addHeader("Content-Type", "application/json")
            .setBody("{\"errors\":[\"dashboard.html references missing query id: q9\"]}"));

    BrowserRepairOutcome outcome =
        client
            .repair(
                "session-1",
                "user-1",
                "<html>broken</html>",
                List.of(new BrowserJsError("err", 1, 0)))
            .block();

    assertThat(outcome).isEqualTo(new BrowserRepairOutcome("<html>broken</html>", false));
  }

  // ── 502: hard failure ──────────────────────────────────────────────────────

  @Test
  void repair_502Response_errorsWithAnalysisBrowserRepairFailedException() {
    mockWebServer.enqueue(
        new MockResponse()
            .setResponseCode(502)
            .addHeader("Content-Type", "application/json")
            .setBody("{\"error\":\"model call failed\"}"));

    assertThatThrownBy(
            () ->
                client
                    .repair(
                        "session-1",
                        "user-1",
                        "<html>broken</html>",
                        List.of(new BrowserJsError("err", 1, 0)))
                    .block())
        .isInstanceOf(AnalysisBrowserRepairFailedException.class);
  }

  @Test
  void repair_streamStalls_timesOut() {
    AnalysisBrowserRepairClient stallingClient = newClient(mockWebServer, 1);
    mockWebServer.enqueue(
        new MockResponse()
            .setResponseCode(200)
            .addHeader("Content-Type", "application/json")
            .setBodyDelay(5, TimeUnit.SECONDS)
            .setBody("{\"html\":\"too late\"}"));

    assertThatThrownBy(
            () ->
                stallingClient
                    .repair(
                        "session-1",
                        "user-1",
                        "<html>broken</html>",
                        List.of(new BrowserJsError("err", 1, 0)))
                    .block())
        .hasRootCauseInstanceOf(java.util.concurrent.TimeoutException.class);
  }

  @Test
  void repair_returnsColdMono_notSubscribedUntilBlocked() {
    // No response enqueued at all — if repair() eagerly fired the request, this test would hang
    // or throw when the Mono is built rather than only when subscribed.
    Mono<BrowserRepairOutcome> outcome =
        client.repair(
            "session-1", "user-1", "<html>broken</html>", List.of(new BrowserJsError("e", 1, 0)));
    assertThat(mockWebServer.getRequestCount()).isZero();
    assertThat(outcome).isNotNull();
  }
}
