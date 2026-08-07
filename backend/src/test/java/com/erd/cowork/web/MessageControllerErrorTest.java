package com.erd.cowork.web;

import static org.assertj.core.api.Assertions.assertThat;

import com.erd.cowork.agent.event.AgentEvent;
import com.erd.cowork.agent.event.ErrorEvent;
import com.erd.cowork.agent.model.AgentOutcome;
import com.erd.cowork.agent.model.AgentRequest;
import com.erd.cowork.agent.provider.DashboardAgentProvider;
import com.erd.cowork.agent.provider.ProviderResult;
import com.erd.cowork.domain.ChatMessage;
import com.erd.cowork.domain.Sender;
import com.erd.cowork.repo.ArtifactRepository;
import com.erd.cowork.repo.ChatMessageRepository;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.boot.test.web.client.TestRestTemplate;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Primary;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.test.context.TestPropertySource;
import reactor.core.publisher.Flux;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
// 固定停用 tsso,讓 CurrentUserFilter 一定註冊(internal 的 tsso.enabled=true 會停掉它、走 SSO)
@TestPropertySource(properties = "tsso.enabled=false")
class MessageControllerErrorTest {

  @Autowired TestRestTemplate rest;
  @Autowired ArtifactRepository artifactRepository;
  @Autowired ChatMessageRepository chatMessageRepository;

  // ── Error FakeProvider ───────────────────────────────────────────────────

  @TestConfiguration
  static class ErrorProviderConfig {

    @Bean
    @Primary
    DashboardAgentProvider errorProvider() {
      return new DashboardAgentProvider() {
        @Override
        public ProviderResult generate(AgentRequest request) {
          Flux<AgentEvent> eventFlux =
              Flux.just((AgentEvent) new ErrorEvent("PROVIDER_ERROR", "boom"));
          return new ProviderResult(eventFlux, () -> new AgentOutcome("", null, null));
        }
      };
    }
  }

  // ── helpers ───────────────────────────────────────────────────────────────

  private String createSession(String userId) {
    return UUID.randomUUID().toString();
  }

  private String postSse(String sessionId, String userId, String question) {
    HttpHeaders headers = new HttpHeaders();
    headers.set("X-User-Id", userId);
    headers.setContentType(MediaType.APPLICATION_JSON);
    headers.set(HttpHeaders.ACCEPT, MediaType.TEXT_EVENT_STREAM_VALUE);
    return rest.exchange(
            "/api/sessions/" + sessionId + "/messages",
            HttpMethod.POST,
            new HttpEntity<>(Map.of("question", question), headers),
            String.class)
        .getBody();
  }

  // ── tests ─────────────────────────────────────────────────────────────────

  @Test
  void sse_providerError_noArtifact_stepsMarkError() {
    long artifactCountBefore = artifactRepository.count();

    String sid = createSession("u1");
    String body = postSse(sid, "u1", "trigger error");

    // SSE body must contain PROVIDER_ERROR exactly once (finalize must not re-emit it)
    assertThat(body).contains("PROVIDER_ERROR");
    int occurrences = body.split("PROVIDER_ERROR", -1).length - 1;
    assertThat(occurrences).isEqualTo(1);

    // DB: USER + AI messages
    List<ChatMessage> msgs = chatMessageRepository.findBySessionIdOrderByCreatedAtAsc(sid);
    assertThat(msgs).hasSize(2);
    ChatMessage aiMsg = msgs.get(1);
    assertThat(aiMsg.getSender()).isEqualTo(Sender.AI);

    // AI message: no artifact
    assertThat(aiMsg.getArtifactId()).isNull();

    // stepsJson stores only d* dynamic steps; no d* were emitted before the error → empty array.
    // Error semantics are carried by the ErrorEvent already present in the SSE body.
    assertThat(aiMsg.getStepsJson()).isEqualTo("[]");

    // No new artifact row created
    assertThat(artifactRepository.count()).isEqualTo(artifactCountBefore);
  }
}
