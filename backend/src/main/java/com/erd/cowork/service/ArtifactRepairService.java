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
import org.springframework.transaction.annotation.Transactional;

/** Coordinates browser-error-driven artifact repair: ownership check, LLM call, persistence. */
@Slf4j
@Service
@RequiredArgsConstructor
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

  /**
   * Repairs an artifact in response to runtime JavaScript errors reported by the browser.
   *
   * <p>Ownership is enforced via {@link SessionGuard}: a missing or foreign artifact surfaces as
   * 404. A null {@code rawHtml} (artifact created before the raw-HTML column was introduced)
   * surfaces as 409.
   *
   * <p>On success, the repaired assembled HTML is stored in {@link FileStorage}; the previous
   * storage key (if any) is deleted best-effort (failure only warns, never blocks the repair).
   *
   * <p>On completion (success or LLM failure), a system {@link ChatMessage} is persisted so the
   * repair outcome remains visible in the conversation history after a page refresh.
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
  @Transactional
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

    String rawHtml = artifact.getRawHtml();
    if (rawHtml == null) {
      throw new ConflictException("Artifact has no raw HTML to repair: " + artifactId);
    }
    List<AgentFileContext> fileContexts = buildFileContexts(sessionId);

    AgentRequest baseRequest =
        new AgentRequest(ownedSession.getUserId(), sessionId, "", List.of(), fileContexts, rawHtml);

    BrowserRepairOutcome outcome =
        artifactRepairer.repairWithBrowserErrors(sessionId, rawHtml, errors, baseRequest).block();

    if (outcome == null || !outcome.passed()) {
      persistRepairRecord(sessionId, errors, false);
      return false;
    }

    String assembledHtml = artifactAssembler.assemble(sessionId, outcome.html());
    String oldStorageKey = artifact.getHtmlStorageKey();

    byte[] htmlBytes = assembledHtml.getBytes(StandardCharsets.UTF_8);
    try (ByteArrayInputStream htmlStream = new ByteArrayInputStream(htmlBytes)) {
      String newStorageKey =
          fileStorage.store(StorageCategory.ARTIFACT, sessionId, artifactId + ".html", htmlStream);
      artifact.setHtmlStorageKey(newStorageKey);
    } catch (IOException ioException) {
      throw new RuntimeException(
          "Failed to store repaired artifact HTML for artifact " + artifactId, ioException);
    }

    artifact.setRawHtml(outcome.html());

    // Best-effort deletion of the superseded storage file; failure only warns.
    if (oldStorageKey != null) {
      try {
        fileStorage.delete(oldStorageKey);
      } catch (IOException ioException) {
        log.warn(
            "Failed to delete old artifact HTML key={} for artifact={}",
            oldStorageKey,
            artifactId,
            ioException);
      }
    }

    artifacts.save(artifact);
    persistRepairRecord(sessionId, errors, true);
    return true;
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
