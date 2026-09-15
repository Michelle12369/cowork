package com.erd.cowork.agent.provider.analysis;

import static org.assertj.core.api.Assertions.assertThat;

import com.erd.cowork.agent.mcp.McpCallErrorWriter;
import com.erd.cowork.agent.model.ConnectorSpec;
import com.erd.cowork.config.AnalysisAgentProperties;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Iterator;
import java.util.Map;
import java.util.concurrent.TimeUnit;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okhttp3.mockwebserver.RecordedRequest;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.web.reactive.function.client.WebClient;

class AnalysisToolCallClientTest {

  private static final ConnectorSpec SALES =
      new ConnectorSpec("sales", "Sales", "http://mcp.local/sales", null);
  private static final String SSO_TOKEN = "secret-sso-token";
  private static final String SSO_URL = "http://sso.local";
  private static final Path FIXTURE =
      Path.of("..", "deepagent-service", "tests", "fixtures", "mcp_result_examples.json");

  private final ObjectMapper objectMapper = new ObjectMapper();
  private MockWebServer mockWebServer;
  private AnalysisToolCallClient client;

  @BeforeEach
  void setUp() throws Exception {
    mockWebServer = new MockWebServer();
    mockWebServer.start();
    client = newClient("http://localhost:" + mockWebServer.getPort(), 30);
  }

  @AfterEach
  void tearDown() throws Exception {
    mockWebServer.shutdown();
  }

  private AnalysisToolCallClient newClient(String baseUrl, int toolCallTimeoutSeconds) {
    AnalysisAgentProperties properties =
        new AnalysisAgentProperties(
            baseUrl,
            "/data/uploads",
            180,
            64,
            "agent-bearer",
            "X-SSO-Token",
            "X-SSO-Url",
            toolCallTimeoutSeconds);
    return new AnalysisToolCallClient(
        properties, new McpCallErrorWriter(objectMapper), WebClient.builder());
  }

  private String call() {
    return client.call(SALES, "list_orders", Map.of("days", 30), SSO_TOKEN, SSO_URL).block();
  }

  private static MockResponse json(int status, String body) {
    return new MockResponse()
        .setResponseCode(status)
        .addHeader("Content-Type", "application/json")
        .setBody(body);
  }

  @Test
  void call_200_returnsBodyByteForByteIncludingEnvelopeAndFloatLiterals() {
    String body = "{\"data\":{\"result\":[{\"qty\":1.10,\"id\":\"A-1\"}],\"total\":1.0}}";
    mockWebServer.enqueue(json(200, body));

    assertThat(call()).isEqualTo(body);
  }

  @Test
  void call_200_fixtureExamplesPassThroughUnchanged() throws Exception {
    JsonNode examples = objectMapper.readTree(Files.readString(FIXTURE, StandardCharsets.UTF_8));
    Iterator<Map.Entry<String, JsonNode>> fields = examples.fields();
    while (fields.hasNext()) {
      String exampleBody = objectMapper.writeValueAsString(fields.next().getValue());
      mockWebServer.enqueue(json(200, exampleBody));

      assertThat(call()).isEqualTo(exampleBody);
    }
  }

  @Test
  void call_requestBody_carriesConnectorToolArgsAndSsoOnlyInHeaders() throws Exception {
    mockWebServer.enqueue(json(200, "{\"data\":[]}"));

    call();

    RecordedRequest request = mockWebServer.takeRequest();
    assertThat(request.getPath()).isEqualTo("/tool-call");
    assertThat(request.getHeader("Authorization")).isEqualTo("Bearer agent-bearer");
    assertThat(request.getHeader("X-SSO-Token")).isEqualTo(SSO_TOKEN);
    assertThat(request.getHeader("X-SSO-Url")).isEqualTo(SSO_URL);
    String body = request.getBody().readUtf8();
    assertThat(body).contains("\"connector\":{");
    assertThat(body).contains("\"id\":\"sales\"");
    assertThat(body).contains("\"url\":\"http://mcp.local/sales\"");
    assertThat(body).contains("\"tool\":\"list_orders\"");
    assertThat(body).contains("\"args\":{\"days\":30}");
    assertThat(body).doesNotContain(SSO_TOKEN);
    assertThat(body).doesNotContain(SSO_URL);
  }

  @Test
  void call_422_returnsInvalidCall() throws Exception {
    mockWebServer.enqueue(json(422, "{\"detail\":[{\"loc\":[\"body\",\"tool\"]}]}"));

    JsonNode result = objectMapper.readTree(call());

    assertThat(result.path("error").path("code").asText()).isEqualTo("INVALID_CALL");
    assertThat(result.path("error").path("message").asText()).contains("422");
    assertThat(result.has("data")).isFalse();
  }

  @Test
  void call_503_returnsRetryableWithStatusInMessage() throws Exception {
    mockWebServer.enqueue(json(503, "upstream down"));

    JsonNode result = objectMapper.readTree(call());

    assertThat(result.path("error").path("code").asText()).isEqualTo("RETRYABLE");
    assertThat(result.path("error").path("message").asText()).contains("503");
  }

  @Test
  void call_401_returnsConnectorUnavailable() throws Exception {
    mockWebServer.enqueue(json(401, "{\"detail\":\"bad bearer\"}"));

    JsonNode result = objectMapper.readTree(call());

    assertThat(result.path("error").path("code").asText()).isEqualTo("CONNECTOR_UNAVAILABLE");
    assertThat(result.path("error").path("message").asText()).contains("401");
  }

  @Test
  void call_timeout_returnsRetryable() throws Exception {
    client = newClient("http://localhost:" + mockWebServer.getPort(), 1);
    mockWebServer.enqueue(json(200, "{\"data\":[]}").setBodyDelay(3, TimeUnit.SECONDS));

    JsonNode result = objectMapper.readTree(call());

    assertThat(result.path("error").path("code").asText()).isEqualTo("RETRYABLE");
    assertThat(result.path("error").path("message").asText()).contains("timed out");
  }

  @Test
  void call_connectionRefused_returnsRetryable() throws Exception {
    int closedPort = mockWebServer.getPort();
    mockWebServer.shutdown();
    client = newClient("http://localhost:" + closedPort, 5);

    JsonNode result = objectMapper.readTree(call());

    assertThat(result.path("error").path("code").asText()).isEqualTo("RETRYABLE");
  }

  @Test
  void call_anyFailure_messageNeverContainsSsoValues() throws Exception {
    mockWebServer.enqueue(json(500, "boom " + SSO_TOKEN));

    String result = call();

    assertThat(result).doesNotContain(SSO_TOKEN);
    assertThat(result).doesNotContain(SSO_URL);
  }

  @Test
  void call_200EmptyBody_returnsRetryable() throws Exception {
    mockWebServer.enqueue(new MockResponse().setResponseCode(200));

    JsonNode result = objectMapper.readTree(call());

    assertThat(result.path("error").path("code").asText()).isEqualTo("RETRYABLE");
  }
}
