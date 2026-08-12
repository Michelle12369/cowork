package com.erd.cowork.service;

import com.erd.cowork.agent.model.AgentFileContext;
import com.erd.cowork.agent.model.AgentRequest;
import com.erd.cowork.agent.repair.ArtifactRepairer;
import com.erd.cowork.agent.repair.BrowserJsError;
import com.erd.cowork.agent.repair.BrowserRepairOutcome;
import com.erd.cowork.artifact.ArtifactAssembler;
import com.erd.cowork.config.StorageProperties;
import com.erd.cowork.domain.Artifact;
import com.erd.cowork.domain.ChatMessage;
import com.erd.cowork.domain.ChatSession;
import com.erd.cowork.domain.Sender;
import com.erd.cowork.domain.UploadedFile;
import com.erd.cowork.exception.BrowserRepairUnsupportedException;
import com.erd.cowork.exception.ConflictException;
import com.erd.cowork.exception.FilesExpiredException;
import com.erd.cowork.exception.NotFoundException;
import com.erd.cowork.logging.LogAnnotation;
import com.erd.cowork.parsing.model.FileProfile;
import com.erd.cowork.repo.ArtifactRepository;
import com.erd.cowork.repo.ChatMessageRepository;
import com.erd.cowork.repo.UploadedFileRepository;
import com.erd.cowork.storage.FileStorage;
import com.erd.cowork.storage.StorageCategory;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;

/** Coordinates browser-error-driven artifact repair: ownership check, LLM call, persistence. */
@Slf4j
@Service
@RequiredArgsConstructor
@LogAnnotation
public class ArtifactRepairService {

  private static final String REPAIR_RECORD_SUCCESS_PREFIX = "已修復儀表板執行錯誤";
  private static final String REPAIR_RECORD_FAILURE_PREFIX = "儀表板執行錯誤自動修復未成功";

  /** Format string for the error-count segment in a repair-record message; {@code %d} = count. */
  private static final String REPAIR_RECORD_COUNT_FORMAT = "（%d 個）：";

  private static final int ERROR_SUMMARY_MAX_LENGTH = 120;

  private final ArtifactRepository artifacts;
  private final SessionGuard sessionGuard;
  private final ArtifactRepairer artifactRepairer;
  private final ArtifactAssembler artifactAssembler;
  private final UploadedFileRepository uploadedFiles;
  private final ObjectMapper objectMapper;
  private final ChatMessageRepository chatMessages;
  private final FileStorage fileStorage;
  private final StorageProperties storageProperties;
  private final ArtifactService artifactService;
  private final TransactionTemplate transactionTemplate;

  /**
   * Repairs an artifact in response to runtime JavaScript errors reported by the browser.
   *
   * <p>Ownership is enforced via {@link SessionGuard}: a missing or foreign artifact surfaces as
   * 404. An artifact with no stored HTML at all (both storage keys null) surfaces as 409.
   *
   * <p>On success, the repaired assembled HTML is stored in {@link FileStorage}; the previous
   * storage keys (assembled and raw, if any) are deleted best-effort (failure only warns, never
   * blocks the repair).
   *
   * <p>On completion (success or LLM failure), a system {@link ChatMessage} is persisted so the
   * repair outcome remains visible in the conversation history after a page refresh.
   *
   * <p>The LLM repair call runs outside any database transaction — it can easily exceed MongoDB's
   * 60s transaction lifetime limit, and a Mongo transaction (unlike the JPA one this replaced) is
   * server-side aborted once that limit is hit. Only the DB+storage tail (new HTML store, old-key
   * cleanup, {@code artifacts.save}, repair-record message) is wrapped in a single {@link
   * TransactionTemplate}-managed transaction, so the artifact update and its paired chat message
   * still commit atomically.
   *
   * @param artifactId artifact UUID to repair
   * @param errors runtime JavaScript errors from the browser (at most 10 are forwarded to the LLM)
   * @return {@code true} if the repair passed syntax validation and the artifact was updated;
   *     {@code false} if the LLM produced no improvement
   * @throws NotFoundException if the artifact does not exist or belongs to another user
   * @throws ConflictException if the artifact has no raw HTML to repair from
   * @throws BrowserRepairUnsupportedException if the active provider mode has no {@code
   *     DashboardAgentProvider} (browser repair only applies to LLM-written HTML) — mapped to 409
   *     by {@link com.erd.cowork.exception.GlobalExceptionHandler}
   */
  public boolean repairFromBrowserErrors(String artifactId, List<BrowserJsError> errors) {
    log.info("repairFromBrowserErrors artifactId={} errorCount={}", artifactId, errors.size());

    if (!artifactRepairer.isBrowserRepairSupported()) {
      throw new BrowserRepairUnsupportedException(
          "Browser-error repair is not supported by the active provider mode.");
    }

    Artifact artifact =
        artifacts
            .findById(artifactId)
            .orElseThrow(() -> new NotFoundException("Artifact not found: " + artifactId));

    String sessionId = artifact.getSessionId();
    ChatSession ownedSession = sessionGuard.loadOwned(sessionId);
    boolean hasExpiredFile =
        uploadedFiles.findBySessionId(sessionId).stream().anyMatch(UploadedFile::isExpired);
    if (hasExpiredFile) {
      throw new FilesExpiredException(storageProperties.retention().uploads().toDays());
    }

    String rawHtml =
        artifactService
            .loadRawHtml(artifact)
            .orElseThrow(
                () -> new ConflictException("Artifact has no raw HTML to repair: " + artifactId));
    List<AgentFileContext> fileContexts = buildFileContexts(sessionId);

    AgentRequest baseRequest =
        new AgentRequest(ownedSession.getUserId(), sessionId, "", List.of(), fileContexts, rawHtml);

    // LLM repair call — deliberately outside any transaction. It can run well past Mongo's 60s
    // transaction lifetime limit, and a Mongo transaction (unlike the JPA one this replaced) is
    // aborted server-side once that limit is hit, taking the DB writes below down with it.
    BrowserRepairOutcome outcome =
        artifactRepairer.repairWithBrowserErrors(sessionId, rawHtml, errors, baseRequest).block();

    if (outcome == null || !outcome.passed()) {
      persistRepairRecord(sessionId, errors, false);
      return false;
    }

    String assembledHtml = artifactAssembler.assemble(sessionId, outcome.html());
    String oldStorageKey = artifact.getHtmlStorageKey();
    String oldRawStorageKey = artifact.getRawHtmlStorageKey();

    // DB + storage tail only — the artifact update and its paired repair-record message must
    // commit atomically, but nothing here is slow enough to risk the transaction time limit.
    return transactionTemplate.execute(
        status -> {
          byte[] htmlBytes = assembledHtml.getBytes(StandardCharsets.UTF_8);
          try (ByteArrayInputStream htmlStream = new ByteArrayInputStream(htmlBytes)) {
            String newStorageKey =
                fileStorage.store(
                    StorageCategory.ARTIFACT, sessionId, artifactId + ".html", htmlStream);
            artifact.setHtmlStorageKey(newStorageKey);
          } catch (IOException ioException) {
            throw new RuntimeException(
                "Failed to store repaired artifact HTML for artifact " + artifactId, ioException);
          }

          // Same rule as generation: always store a dedicated raw file so version-edits load the
          // clean pre-assemble base (loadRawHtml prefers the raw key over the theme-injected
          // assembled copy).
          byte[] rawBytes = outcome.html().getBytes(StandardCharsets.UTF_8);
          try (ByteArrayInputStream rawStream = new ByteArrayInputStream(rawBytes)) {
            String newRawStorageKey =
                fileStorage.store(
                    StorageCategory.ARTIFACT, sessionId, artifactId + ".raw.html", rawStream);
            artifact.setRawHtmlStorageKey(newRawStorageKey);
          } catch (IOException ioException) {
            throw new RuntimeException(
                "Failed to store repaired raw HTML for artifact " + artifactId, ioException);
          }

          deleteBestEffort(oldStorageKey, artifactId);
          deleteBestEffort(oldRawStorageKey, artifactId);

          artifacts.save(artifact);
          persistRepairRecord(sessionId, errors, true);
          return true;
        });
  }

  /** Best-effort deletion of a superseded storage file; failure only warns, never blocks. */
  private void deleteBestEffort(String storageKey, String artifactId) {
    if (storageKey == null) {
      return;
    }
    try {
      fileStorage.delete(storageKey);
    } catch (IOException ioException) {
      log.warn(
          "Failed to delete old artifact HTML key={} for artifact={}",
          storageKey,
          artifactId,
          ioException);
    }
  }

  private void persistRepairRecord(String sessionId, List<BrowserJsError> errors, boolean success) {
    String prefix = success ? REPAIR_RECORD_SUCCESS_PREFIX : REPAIR_RECORD_FAILURE_PREFIX;
    String firstMessage = errors.get(0).message();
    String summary =
        firstMessage.length() > ERROR_SUMMARY_MAX_LENGTH
            ? firstMessage.substring(0, ERROR_SUMMARY_MAX_LENGTH)
            : firstMessage;
    String text = prefix + String.format(REPAIR_RECORD_COUNT_FORMAT, errors.size()) + summary;

    ChatMessage record = new ChatMessage();
    record.setSessionId(sessionId);
    record.setSender(Sender.AI);
    record.setText(text);
    chatMessages.save(record);
  }

  private List<AgentFileContext> buildFileContexts(String sessionId) {
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
    return fileContexts;
  }
}
