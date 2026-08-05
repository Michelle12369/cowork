package com.erd.cowork.agent;

import com.erd.cowork.agent.event.AgentEvent;
import com.erd.cowork.agent.event.ArtifactEvent;
import com.erd.cowork.agent.event.ErrorEvent;
import com.erd.cowork.agent.event.QuestionEvent;
import com.erd.cowork.agent.event.StepEvent;
import com.erd.cowork.agent.event.TableEvent;
import com.erd.cowork.agent.model.AgentFileContext;
import com.erd.cowork.agent.model.AgentOutcome;
import com.erd.cowork.agent.model.AgentRequest;
import com.erd.cowork.agent.model.ClarifyingQuestion;
import com.erd.cowork.agent.model.HistoryMessage;
import com.erd.cowork.agent.provider.AgentProvider;
import com.erd.cowork.agent.provider.DashboardAgentProvider;
import com.erd.cowork.agent.provider.HardenedOutput;
import com.erd.cowork.agent.provider.ProviderResult;
import com.erd.cowork.agent.provider.RepairResult;
import com.erd.cowork.config.StorageProperties;
import com.erd.cowork.domain.ChatMessage;
import com.erd.cowork.domain.ChatSession;
import com.erd.cowork.domain.Sender;
import com.erd.cowork.exception.ErrorCode;
import com.erd.cowork.exception.FilesExpiredException;
import com.erd.cowork.exception.NotFoundException;
import com.erd.cowork.parsing.model.FileProfile;
import com.erd.cowork.repo.ArtifactRepository;
import com.erd.cowork.repo.ChatMessageRepository;
import com.erd.cowork.repo.ChatSessionRepository;
import com.erd.cowork.repo.UploadedFileRepository;
import com.erd.cowork.service.ArtifactService;
import com.erd.cowork.service.SessionGuard;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;
import java.util.stream.Collectors;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;
import org.springframework.util.CollectionUtils;
import org.springframework.util.StringUtils;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;
import reactor.core.scheduler.Schedulers;

@Component
@RequiredArgsConstructor
@Slf4j
public class AgentOrchestrator {

  /**
   * Fallback AI message text stored when an artifact is produced but the provider returned no
   * explanation text (e.g. gpt-oss HTML-only responses). A constant avoids an extra LLM call.
   */
  private static final String DEFAULT_DASHBOARD_ANSWER = "✅ 儀表板已產生並顯示於右側面板。";

  /**
   * AI message persisted when the SSE client disconnects (cancel signal) before the stream
   * completes — e.g. the user navigated away or the browser tab was closed. Displayed in the chat
   * bubble so the user knows the response was cut short and can resend.
   */
  private static final String INTERRUPTED_ANSWER = "回應已中斷，請重新送出以繼續";

  /** Prefix appended to history AI message text to summarise the options previously offered. */
  private static final String OPTIONS_SUMMARY_PREFIX = "\n[你提供過的選項: ";

  /** Suffix closing the options summary appended to history AI message text. */
  private static final String OPTIONS_SUMMARY_SUFFIX = "]";

  /** Maximum character length of history message text (before the options summary is appended). */
  private static final int HISTORY_TEXT_MAX_LENGTH = 500;

  /** Maximum character length of the session title set on the first USER message. */
  private static final int SESSION_TITLE_MAX_LENGTH = 30;

  /** Prefix for the auto-generated sequential artifact title (e.g. {@code "Version 1"}). */
  private static final String ARTIFACT_TITLE_PREFIX = "Version ";

  private final SessionGuard sessionGuard;
  private final ChatMessageRepository messages;
  private final UploadedFileRepository uploadedFiles;
  private final ArtifactRepository artifacts;
  private final AgentProvider provider;
  private final ObjectMapper objectMapper;
  private final ChatSessionRepository sessionRepository;
  private final AgentConversationWriter conversationWriter;
  private final StorageProperties storageProperties;
  private final ArtifactService artifactService;

  private record PrepareResult(
      ChatSession session,
      List<AgentFileContext> files,
      List<HistoryMessage> history,
      String previousArtifactHtml) {}

  /** Streams agent events for the given session and question. */
  public Flux<AgentEvent> stream(
      String userId, String sessionId, String question, String baseArtifactId) {
    // One flag per request: tracks whether an AI ChatMessage has been persisted for this turn.
    // Guards the doOnCancel handler (inside buildEventFlow) against double-writes with
    // finalize() and the AGENT_ERROR path.
    AtomicBoolean aiPersisted = new AtomicBoolean(false);
    return Mono.fromCallable(() -> prepare(userId, sessionId, question, baseArtifactId))
        .subscribeOn(Schedulers.boundedElastic())
        .flatMapMany(
            prepareResult ->
                buildEventFlow(userId, sessionId, question, prepareResult, aiPersisted))
        .onErrorResume(
            NotFoundException.class,
            exception ->
                // This path fires during prepare() before any USER message is saved,
                // so there is no paired USER row — no AI message needs to be persisted here.
                Flux.just(
                    new ErrorEvent(
                        ErrorCode.NOT_FOUND.name(),
                        Objects.requireNonNullElse(
                            exception.getMessage(), exception.getClass().getSimpleName()))))
        .onErrorResume(
            FilesExpiredException.class,
            exception ->
                // This path fires during prepare() before any USER message is saved,
                // so there is no paired USER row — no AI message needs to be persisted here.
                Flux.just(
                    new ErrorEvent(
                        ErrorCode.FILES_EXPIRED.name(),
                        Objects.requireNonNullElse(
                            exception.getMessage(), exception.getClass().getSimpleName()))))
        .onErrorResume(
            exception -> {
              log.error("Agent error for session {}", sessionId, exception);
              String errorMsg =
                  Objects.requireNonNullElse(
                      exception.getMessage(), exception.getClass().getSimpleName());
              // Persist AI error message so the USER message has a paired reply.
              // Mark aiPersisted before the save so a concurrent doOnCancel cannot double-write.
              // Runs on boundedElastic; persistence failure is only logged — never re-thrown.
              return Mono.<Void>fromRunnable(
                      () -> {
                        aiPersisted.set(true);
                        conversationWriter.tryPersistAiMessage(sessionId, errorMsg);
                      })
                  .subscribeOn(Schedulers.boundedElastic())
                  .thenReturn((AgentEvent) new ErrorEvent(ErrorCode.AGENT_ERROR.name(), errorMsg))
                  .flux();
            });
  }

  // ── Phase 1: prepare ─────────────────────────────────────────────────────

  /** Package-private seam for tests; production callers go through the streaming entry point. */
  PrepareResult prepareForTest(
      String userId, String sessionId, String question, String baseArtifactId) {
    return prepare(userId, sessionId, question, baseArtifactId);
  }

  private PrepareResult prepare(
      String userId, String sessionId, String question, String baseArtifactId) {
    var session = sessionGuard.loadOrCreateOwnedAs(userId, sessionId);

    List<ChatMessage> existingMessages = messages.findBySessionIdOrderByCreatedAtAsc(sessionId);

    // Title rule: set on the very first USER message
    boolean hasUserMessage =
        existingMessages.stream().anyMatch(chatMessage -> chatMessage.getSender() == Sender.USER);
    if (!hasUserMessage) {
      session.setTitle(truncate(question, SESSION_TITLE_MAX_LENGTH));
    }
    // Touch every turn so updatedAt means "last activity", not "created". Setting the field is
    // what makes the entity dirty -- save() alone on an unchanged entity issues no UPDATE, so
    // @LastModifiedDate would never fire (auditing overwrites this value with its own now()).
    session.setUpdatedAt(Instant.now());
    sessionRepository.save(session);

    // Guard: if any file in the session has been removed by the retention policy, refuse before
    // persisting the USER message so no orphaned USER row is left without a paired AI reply.
    boolean hasExpiredFiles =
        uploadedFiles.findBySessionId(sessionId).stream().anyMatch(file -> file.isExpired());
    if (hasExpiredFiles) {
      throw new FilesExpiredException(storageProperties.retention().uploads().toDays());
    }

    // Persist USER message
    ChatMessage userMsg = new ChatMessage();
    userMsg.setSessionId(sessionId);
    userMsg.setSender(Sender.USER);
    userMsg.setText(question);
    messages.save(userMsg);

    // Build file contexts (skip files with null or unparseable metadataJson)
    List<AgentFileContext> fileContexts = new ArrayList<>();
    for (var uploadedFile : uploadedFiles.findBySessionIdAndExpiredFalse(sessionId)) {
      if (uploadedFile.getMetadataJson() == null) {
        log.warn("null metadataJson for file {}", uploadedFile.getId());
        continue;
      }
      try {
        FileProfile profile =
            objectMapper.readValue(uploadedFile.getMetadataJson(), FileProfile.class);
        fileContexts.add(
            new AgentFileContext(
                uploadedFile.getAlias(),
                uploadedFile.getName(),
                uploadedFile.getType(),
                uploadedFile.getStorageKey(),
                profile));
      } catch (Exception exception) {
        log.warn(
            "failed to parse metadataJson for file {}: {}",
            uploadedFile.getId(),
            exception.getMessage());
      }
    }

    // History: all messages before the new USER message.
    // AI messages with questionsJson have their options appended so the model remembers
    // what choices it offered and won't repeat the same question next turn.
    List<HistoryMessage> history =
        existingMessages.stream().map(this::buildHistoryMessage).toList();

    // Resolve the raw HTML for iterative refinement.
    // When baseArtifactId is specified: load that artifact and verify it belongs to this session;
    // if the check fails, fall back to the most-recent artifact.
    String previousArtifactHtml = resolveArtifactHtml(sessionId, baseArtifactId);

    return new PrepareResult(session, fileContexts, history, previousArtifactHtml);
  }

  /**
   * Builds a {@link HistoryMessage} from a persisted {@link ChatMessage}. For AI messages that
   * carried clarifying questions, the options summary is appended to the text so the model knows
   * what choices it already offered and avoids repeating the same question.
   *
   * <p>Truncation is applied here (before the append) so the options summary is never cut off.
   * {@link com.erd.cowork.agent.provider.openai.PromptAssembler} does NOT re-truncate history text.
   */
  private HistoryMessage buildHistoryMessage(ChatMessage chatMessage) {
    String text = chatMessage.getText() != null ? chatMessage.getText() : "";
    // Truncate the base text first so the options summary (appended below) is never cut off.
    if (text.length() > HISTORY_TEXT_MAX_LENGTH) {
      text = text.substring(0, HISTORY_TEXT_MAX_LENGTH);
    }
    if (chatMessage.getSender() == Sender.AI
        && StringUtils.hasText(chatMessage.getQuestionsJson())) {
      try {
        List<ClarifyingQuestion> questions =
            objectMapper.readValue(
                chatMessage.getQuestionsJson(), new TypeReference<List<ClarifyingQuestion>>() {});
        if (!questions.isEmpty()) {
          String optionsSummary =
              questions.stream()
                  .map(clarifyingQuestion -> String.join(" / ", clarifyingQuestion.options()))
                  .collect(Collectors.joining("; "));
          text = text + OPTIONS_SUMMARY_PREFIX + optionsSummary + OPTIONS_SUMMARY_SUFFIX;
        }
      } catch (Exception exception) {
        log.debug(
            "failed to parse questionsJson for history message {}", chatMessage.getId(), exception);
      }
    }
    return new HistoryMessage(chatMessage.getSender().name(), text);
  }

  private String resolveArtifactHtml(String sessionId, String baseArtifactId) {
    if (StringUtils.hasText(baseArtifactId)) {
      var specified =
          artifacts
              .findById(baseArtifactId)
              .filter(artifact -> sessionId.equals(artifact.getSessionId()))
              .flatMap(artifactService::loadRawHtml)
              .orElse(null);
      if (specified != null) {
        return specified;
      }
      log.debug(
          "baseArtifactId {} not found or not owned by session {}; falling back to most-recent",
          baseArtifactId,
          sessionId);
    }
    return artifacts
        .findFirstBySessionIdOrderByCreatedAtDesc(sessionId)
        .flatMap(artifactService::loadRawHtml)
        .orElse(null);
  }

  // ── Phase 2: event flow ───────────────────────────────────────────────────

  private Flux<AgentEvent> buildEventFlow(
      String userId,
      String sessionId,
      String question,
      PrepareResult prepareResult,
      AtomicBoolean aiPersisted) {

    AtomicReference<ErrorEvent> errorRef = new AtomicReference<>();

    // Accumulates step events emitted by the provider stream.
    // LinkedHashMap preserves insertion order; the last state per key wins (RUNNING → SUCCESS).
    Map<String, StepEvent> stepAccum = new LinkedHashMap<>();

    // Accumulates every TABLE event emitted this turn, keyed by tableId, so finalize() can look up
    // the tables the final answer text references via a [[table:id]] marker and persist just those
    // (referencedTablesJson) — see TableMarkerUtils.
    Map<String, TableEvent> tableAccum = new LinkedHashMap<>();

    AgentRequest request =
        new AgentRequest(
            userId,
            sessionId,
            question,
            prepareResult.history(),
            prepareResult.files(),
            prepareResult.previousArtifactHtml());

    // provider.generate called exactly once here
    ProviderResult providerResult = provider.generate(request);

    Flux<AgentEvent> providerEvents =
        providerResult
            .events()
            .doOnNext(
                event -> {
                  if (event instanceof ErrorEvent errorEvent) {
                    errorRef.set(errorEvent);
                  }
                  // Collect step events — last state per key wins.
                  if (event instanceof StepEvent stepEvent && stepEvent.stepKey() != null) {
                    stepAccum.put(stepEvent.stepKey(), stepEvent);
                  }
                  // Collect TABLE events keyed by tableId (see tableAccum javadoc above).
                  if (event instanceof TableEvent tableEvent) {
                    tableAccum.put(tableEvent.tableId(), tableEvent);
                  }
                });

    // Event flow: provider events (token/step/error) → finalize (persist + emit artifact/question).
    // Fixed s1–s4 steps are removed; progress is conveyed solely by provider-emitted step markers.
    // doOnCancel fires when the SSE client disconnects mid-stream (Reactor cancel signal).
    // At that point finalize() has not run, so we persist a paired AI "interrupted" row.
    // This handler is only reached after prepare() succeeded (USER message already saved).
    return Flux.concat(
            providerEvents,
            Flux.defer(
                    () ->
                        finalize(
                            sessionId,
                            request,
                            providerResult,
                            errorRef,
                            stepAccum,
                            tableAccum,
                            aiPersisted))
                .subscribeOn(Schedulers.boundedElastic()))
        .doOnCancel(
            () -> {
              // compareAndSet guards against a (theoretical) race with finalize completing
              // just before the cancel signal is processed.
              if (aiPersisted.compareAndSet(false, true)) {
                Mono.fromRunnable(
                        () -> conversationWriter.tryPersistAiMessage(sessionId, INTERRUPTED_ANSWER))
                    .subscribeOn(Schedulers.boundedElastic())
                    .subscribe(
                        null,
                        exception ->
                            log.error(
                                "failed to persist interrupted AI message for session {}",
                                sessionId,
                                exception));
              }
            });
  }

  // ── Phase 3: finalize ─────────────────────────────────────────────────────

  private Flux<AgentEvent> finalize(
      String sessionId,
      AgentRequest request,
      ProviderResult providerResult,
      AtomicReference<ErrorEvent> errorRef,
      Map<String, StepEvent> stepAccum,
      Map<String, TableEvent> tableAccum,
      AtomicBoolean aiPersisted) {
    try {
      AgentOutcome outcome = providerResult.outcome().get();
      ErrorEvent err = errorRef.get();

      // stepsJson for the error path uses whatever steps were accumulated so far.
      if (err != null) {
        String stepsJson = objectMapper.writeValueAsString(new ArrayList<>(stepAccum.values()));
        // Provider emitted an ErrorEvent — persist AI message and return empty (error already in
        // stream). Mark aiPersisted before the save so a concurrent doOnCancel cannot double-write.
        aiPersisted.set(true);
        conversationWriter.persistAiMessage(sessionId, err.message(), stepsJson, null, null);
        return Flux.empty();
      }

      // Serialize questions from the outcome — unchanged by harden.
      String questionsJson = null;
      var questions = outcome.questions();
      if (!CollectionUtils.isEmpty(questions)) {
        questionsJson = objectMapper.writeValueAsString(questions);
        log.info(
            "clarification requested session={} questionCount={}", sessionId, questions.size());
      }
      final String capturedQuestionsJson = questionsJson;
      final var capturedQuestions = questions;

      // Provider-specific quality hardening: bare-HTML promotion, validation, repair/retry. Only
      // dashboard modes (LLM writes HTML directly) need generation-time repair, hence this
      // instanceof rather than a strategy map, overkill for two implementations.
      RepairResult hardenResult =
          provider instanceof DashboardAgentProvider dashboardProvider
              ? dashboardProvider.harden(sessionId, request, outcome)
              : RepairResult.passthrough(outcome);

      // Forward harden events into the SSE stream while collecting all step events
      // (including r1) into stepAccum. Last state per key wins — same pattern
      // as the provider-event accumulation in buildEventFlow.
      Flux<AgentEvent> hardenEvents =
          hardenResult
              .events()
              .doOnNext(
                  event -> {
                    if (event instanceof StepEvent stepEvent && stepEvent.stepKey() != null) {
                      stepAccum.put(stepEvent.stepKey(), stepEvent);
                    }
                    // Defensive: no harden path emits TableEvent today, but mirror the
                    // provider-event accumulation in case a future repair pass re-runs a tool.
                    if (event instanceof TableEvent tableEvent) {
                      tableAccum.put(tableEvent.tableId(), tableEvent);
                    }
                  });

      return Flux.concat(
          hardenEvents,
          Flux.defer(
              () -> {
                HardenedOutput hardenedOutput = hardenResult.output().join();
                String resultHtml = hardenedOutput.html();
                String resultAnswerText = hardenedOutput.answerText();

                // Blank-answer fallback: some providers emit HTML with no explanation.
                // Pin a constant sentence so the DB text field is never empty when an
                // artifact is present. Applied after harden so the guard can also supply
                // answer text (e.g. bare-HTML replacement).
                if (StringUtils.hasText(resultHtml) && !StringUtils.hasText(resultAnswerText)) {
                  resultAnswerText = DEFAULT_DASHBOARD_ANSWER;
                }

                // stepsJson is serialized HERE — after r1 has been accumulated by the
                // doOnNext above — so it includes the terminal repair step state.
                String stepsJson;
                try {
                  stepsJson = objectMapper.writeValueAsString(new ArrayList<>(stepAccum.values()));
                } catch (JsonProcessingException jsonException) {
                  throw new RuntimeException("Failed to serialize steps JSON", jsonException);
                }

                // Only the tables the final answer actually references via a [[table:id]] marker
                // are persisted — see TableMarkerUtils/tableAccum javadocs.
                String referencedTablesJson =
                    buildReferencedTablesJson(resultAnswerText, tableAccum);

                if (StringUtils.hasText(resultHtml)) {
                  String artifactTitle = resolveArtifactTitle(sessionId);
                  // CAS-gate against doOnCancel — when the SSE client disconnects while
                  // harden's blocking work is running, the cancel handler wins the CAS first;
                  // this path then loses the CAS and skips persist, preventing a double-write.
                  if (!aiPersisted.compareAndSet(false, true)) {
                    log.info(
                        "result discarded session={} — turn already finalized by cancel",
                        sessionId);
                    return Flux.empty();
                  }
                  String artifactId =
                      conversationWriter.persistHtmlResult(
                          sessionId,
                          resultHtml,
                          stepsJson,
                          capturedQuestionsJson,
                          resultAnswerText,
                          artifactTitle,
                          referencedTablesJson);
                  Flux<AgentEvent> artifactFlux =
                      Flux.just(new ArtifactEvent(artifactId, artifactTitle));
                  Flux<AgentEvent> questionFlux =
                      capturedQuestionsJson != null
                          ? Flux.just(new QuestionEvent(capturedQuestions))
                          : Flux.empty();
                  return Flux.concat(artifactFlux, questionFlux);
                }

                // No HTML produced.
                log.info(
                    "no dashboard produced session={} answerChars={}",
                    sessionId,
                    resultAnswerText != null ? resultAnswerText.length() : 0);
                // CAS-gate against doOnCancel — same rationale as the HTML path above.
                if (!aiPersisted.compareAndSet(false, true)) {
                  log.info(
                      "no-html result discarded session={} — turn already finalized by cancel",
                      sessionId);
                  return Flux.empty();
                }
                conversationWriter.persistAiMessage(
                    sessionId,
                    resultAnswerText,
                    stepsJson,
                    capturedQuestionsJson,
                    referencedTablesJson);
                return capturedQuestionsJson != null
                    ? Flux.just(new QuestionEvent(capturedQuestions))
                    : Flux.empty();
              }));
    } catch (JsonProcessingException exception) {
      throw new RuntimeException("Failed to serialize steps JSON", exception);
    }
  }

  // ── helpers ───────────────────────────────────────────────────────────────

  /**
   * Builds the persisted {@code referencedTablesJson} for a finalized AI message — the subset of
   * {@code tableAccum} whose id appears in a {@code [[table:id]]} marker inside {@code answerText},
   * in first-marker-appearance order. Returns {@code null} when the answer referenced no table (the
   * common case), so {@code referencedTablesJson} stays {@code null} rather than an empty-array
   * string.
   */
  private String buildReferencedTablesJson(String answerText, Map<String, TableEvent> tableAccum) {
    Set<String> referencedIds = TableMarkerUtils.extractReferencedTableIds(answerText);
    if (referencedIds.isEmpty()) {
      return null;
    }
    List<TableEvent> referencedTables =
        referencedIds.stream().map(tableAccum::get).filter(Objects::nonNull).toList();
    if (referencedTables.isEmpty()) {
      return null;
    }
    try {
      return objectMapper.writeValueAsString(referencedTables);
    } catch (JsonProcessingException jsonException) {
      throw new RuntimeException("Failed to serialize referenced tables JSON", jsonException);
    }
  }

  /**
   * Resolves the artifact title for a newly generated dashboard. Titles are sequential within a
   * session: {@code "Version 1"} for the first artifact, {@code "Version 2"} for the second, etc.
   * The count is read before the new artifact is persisted, so the returned number is always one
   * greater than the current artifact count.
   */
  private String resolveArtifactTitle(String sessionId) {
    return ARTIFACT_TITLE_PREFIX + (artifacts.countBySessionId(sessionId) + 1);
  }

  static String truncate(String text, int maxLength) {
    return text.length() <= maxLength ? text : text.substring(0, maxLength) + "…";
  }
}
