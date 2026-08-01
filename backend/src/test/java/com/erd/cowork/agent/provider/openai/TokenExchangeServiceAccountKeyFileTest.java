package com.erd.cowork.agent.provider.openai;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.erd.cowork.config.AgentProperties;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okhttp3.mockwebserver.RecordedRequest;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.web.reactive.function.client.WebClient;

/**
 * Focuses on {@link TokenExchangeClient} resolution/precedence between the inline {@code
 * serviceAccountKey} and the {@code serviceAccountKeyFile} (Kubernetes secret-mount) source,
 * validation, and per-exchange rotation behavior. Exercises {@link TokenExchangeClient} directly
 * against a {@link MockWebServer} exchange endpoint rather than the full SSE provider flow.
 */
class TokenExchangeServiceAccountKeyFileTest {

  private static final String EXCHANGE_PATH = "/exchange";
  private static final String HEADER_NAME = "X-Test-Token-Header";
  private static final int TTL_SECONDS = 300;

  @TempDir private Path tempDir;

  private MockWebServer mockWebServer;

  @BeforeEach
  void setUp() throws IOException {
    mockWebServer = new MockWebServer();
    mockWebServer.start();
  }

  @AfterEach
  void tearDown() throws IOException {
    mockWebServer.shutdown();
  }

  private TokenExchangeClient buildClient(String serviceAccountKey, String serviceAccountKeyFile) {
    String baseUrl = "http://localhost:" + mockWebServer.getPort();
    AgentProperties.OpenAiCompatible.TokenExchange tokenExchangeConfig =
        new AgentProperties.OpenAiCompatible.TokenExchange(
            baseUrl + EXCHANGE_PATH,
            serviceAccountKey,
            serviceAccountKeyFile,
            HEADER_NAME,
            TTL_SECONDS);
    AgentProperties.OpenAiCompatible openAiCompatible =
        new AgentProperties.OpenAiCompatible(
            baseUrl,
            null,
            "test-model",
            131072,
            "/v1/chat/completions",
            "token-exchange",
            tokenExchangeConfig);
    AgentProperties props =
        new AgentProperties(
            "openai-compatible", openAiCompatible, new AgentProperties.Repair(false));
    return new TokenExchangeClient(props, WebClient.builder(), new ObjectMapper());
  }

  private static MockResponse jsonExchangeResponse(String token) {
    return new MockResponse()
        .setResponseCode(200)
        .addHeader("Content-Type", "application/json")
        .setBody("{\"token\":\"" + token + "\"}");
  }

  private String writeKeyFile(String content) throws IOException {
    Path keyFile = tempDir.resolve("service-account-key-" + System.nanoTime() + ".txt");
    Files.writeString(keyFile, content, StandardCharsets.UTF_8);
    return keyFile.toString();
  }

  @Test
  void resolve_fileConfigured_readsKeyFromFileStripped() throws Exception {
    String keyFilePath = writeKeyFile("j1-secret\n");
    mockWebServer.enqueue(jsonExchangeResponse("j2-token"));

    TokenExchangeClient client = buildClient(null, keyFilePath);
    String token = client.getToken().block();

    assertThat(token).isEqualTo("j2-token");
    RecordedRequest exchangeRequest = mockWebServer.takeRequest();
    assertThat(exchangeRequest.getBody().readUtf8()).contains("\"key\":\"j1-secret\"");
  }

  @Test
  void resolve_fileAndInlineBothSet_fileWins() throws Exception {
    String keyFilePath = writeKeyFile("file-value");
    mockWebServer.enqueue(jsonExchangeResponse("j2-token"));

    TokenExchangeClient client = buildClient("inline-value", keyFilePath);
    client.getToken().block();

    RecordedRequest exchangeRequest = mockWebServer.takeRequest();
    String exchangeBody = exchangeRequest.getBody().readUtf8();
    assertThat(exchangeBody).contains("file-value");
    assertThat(exchangeBody).doesNotContain("inline-value");
  }

  @Test
  void resolve_onlyInlineSet_usesInline() throws Exception {
    mockWebServer.enqueue(jsonExchangeResponse("j2-token"));

    TokenExchangeClient client = buildClient("inline-only-value", null);
    client.getToken().block();

    RecordedRequest exchangeRequest = mockWebServer.takeRequest();
    assertThat(exchangeRequest.getBody().readUtf8()).contains("inline-only-value");
  }

  @Test
  void resolve_fileMissing_throwsClearErrorWithoutContents() {
    String missingPath = tempDir.resolve("does-not-exist.txt").toString();

    TokenExchangeClient client = buildClient("fallback-inline-value", missingPath);

    assertThatThrownBy(() -> client.getToken().block())
        .isInstanceOf(IllegalStateException.class)
        .hasMessageContaining(missingPath)
        .hasMessageNotContaining("fallback-inline-value");
  }

  @Test
  void resolve_neitherSet_failsFast() {
    assertThatThrownBy(() -> buildClient(null, null)).isInstanceOf(IllegalArgumentException.class);
  }

  @Test
  void resolve_fileRotated_picksUpNewValueOnNextExchange() throws Exception {
    String keyFilePath = writeKeyFile("original-value");
    mockWebServer.enqueue(jsonExchangeResponse("j2-first"));
    mockWebServer.enqueue(jsonExchangeResponse("j2-second"));

    TokenExchangeClient client = buildClient(null, keyFilePath);
    client.getToken().block(); // first exchange, token cached
    client.invalidate(); // simulate TTL expiry / 401 invalidation

    Files.writeString(Path.of(keyFilePath), "rotated-value", StandardCharsets.UTF_8);
    client.getToken().block(); // second exchange must re-read the file

    RecordedRequest firstExchangeRequest = mockWebServer.takeRequest();
    RecordedRequest secondExchangeRequest = mockWebServer.takeRequest();
    assertThat(firstExchangeRequest.getBody().readUtf8()).contains("original-value");
    assertThat(secondExchangeRequest.getBody().readUtf8()).contains("rotated-value");
  }
}
