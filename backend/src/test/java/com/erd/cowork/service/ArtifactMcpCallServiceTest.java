package com.erd.cowork.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import com.erd.cowork.agent.mcp.McpCallErrorWriter;
import com.erd.cowork.agent.model.ConnectorSpec;
import com.erd.cowork.agent.provider.analysis.AnalysisToolCallClient;
import com.erd.cowork.context.CoworkContext;
import com.erd.cowork.context.CoworkContextHolder;
import com.erd.cowork.domain.Artifact;
import com.erd.cowork.domain.ChatSession;
import com.erd.cowork.exception.NotFoundException;
import com.erd.cowork.repo.ArtifactRepository;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.ObjectProvider;
import reactor.core.publisher.Mono;

@ExtendWith(MockitoExtension.class)
class ArtifactMcpCallServiceTest {

  private static final ConnectorSpec SALES =
      new ConnectorSpec("sales", "Sales", "http://mcp.local/sales", null);
  private static final Map<String, Object> ARGS = Map.of("days", 30, "region", "TW");

  @Mock ArtifactRepository artifacts;
  @Mock SessionGuard sessionGuard;
  @Mock ConnectorCatalogService connectorCatalogService;
  @Mock ObjectProvider<AnalysisToolCallClient> toolCallClientProvider;
  @Mock AnalysisToolCallClient toolCallClient;

  private final ObjectMapper objectMapper = new ObjectMapper();
  private ArtifactMcpCallService service;
  private ListAppender<ILoggingEvent> logAppender;

  @BeforeEach
  void setUp() {
    service =
        new ArtifactMcpCallService(
            artifacts,
            sessionGuard,
            connectorCatalogService,
            toolCallClientProvider,
            new McpCallErrorWriter(objectMapper),
            objectMapper);
    CoworkContextHolder.set(CoworkContext.external("user-1", "http://sso.local", "sso-secret"));
    logAppender = new ListAppender<>();
    logAppender.start();
    ((Logger) LoggerFactory.getLogger(ArtifactMcpCallService.class)).addAppender(logAppender);
  }

  @AfterEach
  void tearDown() {
    CoworkContextHolder.clear();
    ((Logger) LoggerFactory.getLogger(ArtifactMcpCallService.class)).detachAppender(logAppender);
  }

  private void givenOwnedArtifactWithConnectors(List<String> selectedConnectors) {
    Artifact artifact = new Artifact();
    artifact.setId("art-1");
    artifact.setSessionId("session-1");
    when(artifacts.findById("art-1")).thenReturn(Optional.of(artifact));
    ChatSession session = new ChatSession();
    session.setId("session-1");
    session.setSelectedConnectors(selectedConnectors);
    when(sessionGuard.loadOwned("session-1")).thenReturn(session);
  }

  private JsonNode callAsJson() throws Exception {
    return objectMapper.readTree(service.call("art-1", "sales", "list_orders", ARGS));
  }

  @Test
  void call_ownedArtifactWithAllowedConnector_forwardsToClientWithSsoAndReturnsBodyUnchanged()
      throws Exception {
    givenOwnedArtifactWithConnectors(List.of("sales", "crm"));
    when(connectorCatalogService.resolveSpecs(List.of("sales"))).thenReturn(List.of(SALES));
    when(toolCallClientProvider.getIfAvailable()).thenReturn(toolCallClient);
    String body = "{\"data\":{\"result\":[{\"qty\":1.10}]}}";
    when(toolCallClient.call(
            eq(SALES), eq("list_orders"), eq(ARGS), eq("sso-secret"), eq("http://sso.local")))
        .thenReturn(Mono.just(body));

    String result = service.call("art-1", "sales", "list_orders", ARGS);

    assertThat(result).isEqualTo(body);
  }

  @Test
  void call_artifactMissing_throwsNotFound() {
    when(artifacts.findById("missing")).thenReturn(Optional.empty());

    assertThatThrownBy(() -> service.call("missing", "sales", "list_orders", ARGS))
        .isInstanceOf(NotFoundException.class);
    verify(toolCallClient, never()).call(any(), anyString(), any(), any(), any());
  }

  @Test
  void call_foreignSession_throwsNotFound() {
    Artifact artifact = new Artifact();
    artifact.setId("art-1");
    artifact.setSessionId("session-1");
    when(artifacts.findById("art-1")).thenReturn(Optional.of(artifact));
    when(sessionGuard.loadOwned("session-1")).thenThrow(new NotFoundException("session not found"));

    assertThatThrownBy(() -> service.call("art-1", "sales", "list_orders", ARGS))
        .isInstanceOf(NotFoundException.class);
  }

  @Test
  void call_connectorNotInSession_returnsInvalidCallListingAllowedIds() throws Exception {
    givenOwnedArtifactWithConnectors(List.of("crm", "hr"));

    JsonNode result = callAsJson();

    assertThat(result.path("error").path("code").asText()).isEqualTo("INVALID_CALL");
    assertThat(result.path("error").path("message").asText()).contains("sales").contains("crm, hr");
    verify(connectorCatalogService, never()).resolveSpecs(anyList());
  }

  @Test
  void call_fileModeSession_returnsInvalidCall() throws Exception {
    givenOwnedArtifactWithConnectors(null);

    JsonNode result = callAsJson();

    assertThat(result.path("error").path("code").asText()).isEqualTo("INVALID_CALL");
  }

  @Test
  void call_catalogEntryRemoved_returnsConnectorUnavailableWithCatalogMessage() throws Exception {
    givenOwnedArtifactWithConnectors(List.of("sales"));
    when(connectorCatalogService.resolveSpecs(List.of("sales")))
        .thenThrow(new NotFoundException("資料源 sales 已下架"));

    JsonNode result = callAsJson();

    assertThat(result.path("error").path("code").asText()).isEqualTo("CONNECTOR_UNAVAILABLE");
    assertThat(result.path("error").path("message").asText()).contains("已下架");
  }

  @Test
  void call_clientBeanAbsent_returnsConnectorUnavailable() throws Exception {
    givenOwnedArtifactWithConnectors(List.of("sales"));
    when(connectorCatalogService.resolveSpecs(List.of("sales"))).thenReturn(List.of(SALES));
    when(toolCallClientProvider.getIfAvailable()).thenReturn(null);

    JsonNode result = callAsJson();

    assertThat(result.path("error").path("code").asText()).isEqualTo("CONNECTOR_UNAVAILABLE");
  }

  @Test
  void call_anyOutcome_logsArgKeysAndCodeButNeverValuesOrSso() throws Exception {
    givenOwnedArtifactWithConnectors(List.of("sales"));
    when(connectorCatalogService.resolveSpecs(List.of("sales"))).thenReturn(List.of(SALES));
    when(toolCallClientProvider.getIfAvailable()).thenReturn(toolCallClient);
    when(toolCallClient.call(any(), anyString(), any(), any(), any()))
        .thenReturn(Mono.just("{\"error\":{\"code\":\"TOOL_ERROR\",\"message\":\"bad fab\"}}"));

    service.call("art-1", "sales", "list_orders", ARGS);

    String joinedLogs =
        String.join(
            "\n", logAppender.list.stream().map(ILoggingEvent::getFormattedMessage).toList());
    assertThat(joinedLogs).contains("connector=sales").contains("tool=list_orders");
    assertThat(joinedLogs).contains("days").contains("region");
    assertThat(joinedLogs).contains("code=TOOL_ERROR").contains("ok=false");
    assertThat(joinedLogs).doesNotContain("TW").doesNotContain("sso-secret");
  }
}
