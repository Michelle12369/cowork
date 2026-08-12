package com.erd.cowork.agent;

import com.erd.cowork.artifact.ArtifactAssembler;
import com.erd.cowork.config.ArtifactRewriteProperties;
import com.erd.cowork.domain.Artifact;
import com.erd.cowork.domain.ChatMessage;
import com.erd.cowork.domain.Sender;
import com.erd.cowork.logging.LogAnnotation;
import com.erd.cowork.repo.ArtifactRepository;
import com.erd.cowork.repo.ChatMessageRepository;
import com.erd.cowork.storage.FileStorage;
import com.erd.cowork.storage.StorageCategory;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionTemplate;

/** Writes conversation turns (user message, AI message, artifact) to the database. */
@Component
@RequiredArgsConstructor
@Slf4j
@LogAnnotation
public class AgentConversationWriter {

  private final ChatMessageRepository messages;
  private final ArtifactRepository artifacts;
  private final ArtifactAssembler artifactAssembler;
  private final FileStorage fileStorage;
  private final ArtifactRewriteProperties artifactRewriteProperties;
  private final TransactionTemplate transactionTemplate;

  /**
   * Persists an artifact and its paired AI message. Returns the saved artifact's ID.
   *
   * <p>The assembled HTML is stored in {@link FileStorage} (not as a DB field) to avoid heap
   * materialisation of large payloads. The artifact is first saved to obtain its generated id, then
   * the HTML is stored under that id, and the storage key is written back in a second save. The
   * artifact + AI message writes share a single {@link TransactionTemplate}-managed transaction; a
   * storage {@link IOException} rolls back the whole write instead of leaving a partially-written
   * artifact.
   *
   * <p>The {@code assetProfile} is stamped with the current profile from {@link
   * ArtifactRewriteProperties#currentProfile()} so that future serve calls can apply exactly the
   * same CDN-rewrite rules that were active at generation time.
   *
   * @param sessionId session identifier
   * @param html raw HTML (LLM output, not yet data-injected); stored as a separate raw file only
   *     when it references the {@code __ERD_DATA__} marker (data injection) — the deepagent line
   *     has no marker, so readers fall back to the assembled file
   * @param stepsJson serialized d* steps JSON string
   * @param questionsJson serialized questions JSON string, or {@code null}
   * @param answerText the plain-text explanation
   * @param artifactTitle resolved artifact title
   * @return the saved artifact ID
   */
  public String persistHtmlResult(
      String sessionId,
      String html,
      String stepsJson,
      String questionsJson,
      String answerText,
      String artifactTitle) {
    String injectedHtml = artifactAssembler.assemble(sessionId, html);

    return transactionTemplate.execute(
        status -> {
          // Save first (without html body) to obtain the generated id.
          Artifact artifact = new Artifact();
          artifact.setSessionId(sessionId);
          artifact.setTitle(artifactTitle);
          artifact.setAssetProfile(artifactRewriteProperties.currentProfile());
          Artifact saved = artifacts.save(artifact);
          String artifactId = saved.getId();

          // Store assembled HTML in FileStorage keyed by artifactId.
          byte[] htmlBytes = injectedHtml.getBytes(StandardCharsets.UTF_8);
          try (ByteArrayInputStream htmlStream = new ByteArrayInputStream(htmlBytes)) {
            String storageKey =
                fileStorage.store(
                    StorageCategory.ARTIFACT, sessionId, artifactId + ".html", htmlStream);
            saved.setHtmlStorageKey(storageKey);
          } catch (IOException ioException) {
            throw new RuntimeException(
                "Failed to store artifact HTML for session " + sessionId, ioException);
          }

          // Always store the raw (pre-assemble) HTML: loadRawHtml prefers it, so version-edits
          // load this clean base instead of the assembled copy (whose head-inject theme would
          // otherwise leak into the edit base and trip the guard).
          byte[] rawBytes = html.getBytes(StandardCharsets.UTF_8);
          try (ByteArrayInputStream rawStream = new ByteArrayInputStream(rawBytes)) {
            String rawStorageKey =
                fileStorage.store(
                    StorageCategory.ARTIFACT, sessionId, artifactId + ".raw.html", rawStream);
            saved.setRawHtmlStorageKey(rawStorageKey);
          } catch (IOException ioException) {
            throw new RuntimeException(
                "Failed to store raw artifact HTML for session " + sessionId, ioException);
          }

          saved = artifacts.save(saved);

          log.info(
              "artifact generated session={} artifactId={} htmlChars={} profile={}",
              sessionId,
              artifactId,
              html.length(),
              saved.getAssetProfile());

          ChatMessage aiMsg = new ChatMessage();
          aiMsg.setSessionId(sessionId);
          aiMsg.setSender(Sender.AI);
          aiMsg.setText(answerText);
          aiMsg.setStepsJson(stepsJson);
          aiMsg.setArtifactId(artifactId);
          aiMsg.setQuestionsJson(questionsJson);
          messages.save(aiMsg);
          return artifactId;
        });
  }

  /**
   * Persists an AI-only message (no artifact).
   *
   * @param sessionId session identifier
   * @param answerText plain-text answer
   * @param stepsJson serialized d* steps JSON string
   * @param questionsJson serialized questions JSON string, or {@code null}
   */
  public void persistAiMessage(
      String sessionId, String answerText, String stepsJson, String questionsJson) {
    transactionTemplate.executeWithoutResult(
        status -> {
          ChatMessage aiMsg = new ChatMessage();
          aiMsg.setSessionId(sessionId);
          aiMsg.setSender(Sender.AI);
          aiMsg.setText(answerText);
          aiMsg.setStepsJson(stepsJson);
          aiMsg.setQuestionsJson(questionsJson);
          messages.save(aiMsg);
        });
  }

  /**
   * Persists an AI error/interrupted message. Failures are logged and swallowed — never re-thrown.
   * Used by the AGENT_ERROR path and the client-disconnect (cancel) path.
   *
   * @param sessionId session identifier
   * @param text message text (error description or "（回應中斷）")
   */
  public void tryPersistAiMessage(String sessionId, String text) {
    try {
      transactionTemplate.executeWithoutResult(
          status -> {
            ChatMessage msg = new ChatMessage();
            msg.setSessionId(sessionId);
            msg.setSender(Sender.AI);
            msg.setText(text);
            msg.setStepsJson("[]");
            messages.save(msg);
          });
    } catch (Exception exception) {
      log.error("failed to persist AI message for session {}", sessionId, exception);
    }
  }
}
