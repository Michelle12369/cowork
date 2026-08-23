package com.erd.cowork.agent.provider.analysis;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.erd.cowork.config.AnalysisAgentProperties;
import com.erd.cowork.exception.AnalysisReplayFailedException;
import com.erd.cowork.exception.AnalysisReplayRejectedException;
import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.web.reactive.function.client.WebClient;

class AnalysisReplayClientTest {

  private static final int DEFAULT_TEST_TIMEOUT_SECONDS = 30;
  private static final int DEFAULT_TEST_MAX_IN_MEMORY_SIZE_MB = 64;

  private MockWebServer mockWebServer;
  private AnalysisReplayClient client;

  @BeforeEach
  void setUp() throws Exception {
    mockWebServer = new MockWebServer();
    mockWebServer.start();
    AnalysisAgentProperties analysisProperties =
        new AnalysisAgentProperties(
            "http://localhost:" + mockWebServer.getPort(),
            "/data/uploads",
            DEFAULT_TEST_TIMEOUT_SECONDS,
            DEFAULT_TEST_MAX_IN_MEMORY_SIZE_MB);
    client = new AnalysisReplayClient(analysisProperties, new ObjectMapper(), WebClient.builder());
  }

  @AfterEach
  void tearDown() throws Exception {
    mockWebServer.shutdown();
  }

  // ── malformed stored recipe (Finding 2: aligned with deepagent's INVALID_RECIPE status) ────

  @Test
  void replay_malformedRecipeJson_errorsWithAnalysisReplayRejectedExceptionCodeInvalidRecipe() {
    assertThatThrownBy(() -> client.replay("art-1", "{not valid json", "<html>base</html>").block())
        .isInstanceOf(AnalysisReplayRejectedException.class)
        .satisfies(
            exception ->
                assertThat(((AnalysisReplayRejectedException) exception).getErrorCode())
                    .isEqualTo("INVALID_RECIPE"));
    // A malformed recipe never reaches deepagent-service — no request should have been sent.
    assertThat(mockWebServer.getRequestCount()).isZero();
  }

  // ── 200 with html: success ──────────────────────────────────────────────────

  @Test
  void replay_200ResponseWithHtml_returnsSuccessOutcome() {
    mockWebServer.enqueue(
        new MockResponse()
            .setResponseCode(200)
            .addHeader("Content-Type", "application/json")
            .setBody("{\"html\":\"<html>fresh</html>\"}"));

    AnalysisReplayOutcome outcome =
        client.replay("art-1", "{\"schemaVersion\":1}", "<html>base</html>").block();

    assertThat(outcome).isEqualTo(AnalysisReplayOutcome.success("<html>fresh</html>"));
  }

  // ── 200 with error body: request-shaped failure, not an exception ──────────

  @Test
  void replay_200ResponseWithErrorBody_returnsFailureOutcomeNotException() {
    mockWebServer.enqueue(
        new MockResponse()
            .setResponseCode(200)
            .addHeader("Content-Type", "application/json")
            .setBody("{\"error\":{\"code\":\"SOURCE_GONE\",\"message\":\"資料源已停用\"}}"));

    AnalysisReplayOutcome outcome =
        client.replay("art-1", "{\"schemaVersion\":1}", "<html>base</html>").block();

    assertThat(outcome).isEqualTo(AnalysisReplayOutcome.failure("SOURCE_GONE", "資料源已停用"));
  }

  // ── non-2xx: transport/outage failure ───────────────────────────────────────

  @Test
  void replay_502Response_errorsWithAnalysisReplayFailedException() {
    mockWebServer.enqueue(new MockResponse().setResponseCode(502));

    assertThatThrownBy(
            () -> client.replay("art-1", "{\"schemaVersion\":1}", "<html>base</html>").block())
        .isInstanceOf(AnalysisReplayFailedException.class);
  }
}
