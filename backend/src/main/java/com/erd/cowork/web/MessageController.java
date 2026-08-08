package com.erd.cowork.web;

import com.erd.cowork.agent.AgentOrchestrator;
import com.erd.cowork.agent.event.AgentEvent;
import com.erd.cowork.context.CoworkContextHolder;
import com.erd.cowork.logging.LogAnnotation;
import com.erd.cowork.web.dto.SendMessageRequest;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.responses.ApiResponse;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import java.time.Duration;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.MediaType;
import org.springframework.http.codec.ServerSentEvent;
import org.springframework.util.StringUtils;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;

@Slf4j
@RestController
@RequestMapping("/api/sessions")
@RequiredArgsConstructor
@Validated
@Tag(name = "Messages", description = "Agent message streaming")
@LogAnnotation
public class MessageController {

  private final AgentOrchestrator orchestrator;

  @PostMapping(
      value = "/{id}/messages",
      consumes = MediaType.APPLICATION_JSON_VALUE,
      produces = MediaType.TEXT_EVENT_STREAM_VALUE)
  @Operation(summary = "Send a message and stream agent events")
  @ApiResponse(responseCode = "200", description = "SSE stream of agent events")
  @ApiResponse(responseCode = "404", description = "Session not found")
  public Flux<ServerSentEvent<AgentEvent>> stream(
      @PathVariable String id, @Valid @RequestBody SendMessageRequest request) {

    // Capture userId synchronously — the ThreadLocal-backed context must not be
    // accessed inside the reactive pipeline (which may run on a different thread).
    String userId = CoworkContextHolder.userId();

    log.info(
        "POST message session={} questionLen={} hasBaseArtifact={}",
        id,
        request.question().length(),
        StringUtils.hasText(request.baseArtifactId()));
    log.debug(
        "question preview: {}",
        request.question().length() <= 80
            ? request.question()
            : request.question().substring(0, 80) + "…");

    // refCount(2) (vs autoConnect(2)) so that cancellation propagates: when the client
    // disconnects, the merged downstream subscription is cancelled → both subscribers
    // (data + done) drop to zero → the upstream source is disconnected → cancellation
    // reaches the provider's sink.onDispose, interrupting in-flight generation.
    Flux<AgentEvent> events =
        orchestrator.stream(userId, id, request.question(), request.baseArtifactId())
            .publish()
            .refCount(2);

    Flux<ServerSentEvent<AgentEvent>> data =
        events.map(agentEvent -> ServerSentEvent.builder(agentEvent).build());

    Mono<Object> done = events.ignoreElements().cast(Object.class);

    Flux<ServerSentEvent<AgentEvent>> heartbeat =
        Flux.interval(Duration.ofSeconds(15))
            .map(intervalTick -> ServerSentEvent.<AgentEvent>builder().comment("ka").build())
            .takeUntilOther(done);

    return Flux.merge(data, heartbeat);
  }
}
