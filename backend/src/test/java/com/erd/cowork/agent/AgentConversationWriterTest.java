package com.erd.cowork.agent;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.endsWith;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.atLeastOnce;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.erd.cowork.artifact.ArtifactAssembler;
import com.erd.cowork.config.ArtifactRewriteProperties;
import com.erd.cowork.config.ArtifactRewriteProperties.RewriteRule;
import com.erd.cowork.domain.Artifact;
import com.erd.cowork.repo.ArtifactRepository;
import com.erd.cowork.repo.ChatMessageRepository;
import com.erd.cowork.storage.FileStorage;
import com.erd.cowork.storage.StorageCategory;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.lang.reflect.Field;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.transaction.support.TransactionCallback;
import org.springframework.transaction.support.TransactionTemplate;

@ExtendWith(MockitoExtension.class)
class AgentConversationWriterTest {

  @Mock ChatMessageRepository messages;
  @Mock ArtifactRepository artifacts;
  @Mock ArtifactAssembler artifactAssembler;
  @Mock FileStorage fileStorage;
  @Mock TransactionTemplate transactionTemplate;

  AgentConversationWriter writer;

  @BeforeEach
  void setUp() {
    ArtifactRewriteProperties rewriteProperties =
        new ArtifactRewriteProperties(
            "tw3-ec5",
            Map.of(
                "tw3-ec5",
                List.of(
                    new RewriteRule(
                        "https://cdn\\.tailwindcss\\.com[^\"']*", "/vendor/tailwind-play-v3.js"))));

    writer =
        new AgentConversationWriter(
            messages,
            artifacts,
            artifactAssembler,
            fileStorage,
            transactionTemplate,
            rewriteProperties);
    // Execute the TransactionCallback inline so tests exercise the real business logic.
    when(transactionTemplate.execute(any()))
        .thenAnswer(
            invocation -> {
              TransactionCallback<?> callback = invocation.getArgument(0);
              return callback.doInTransaction(null);
            });
  }

  // ── persistHtmlResult ─────────────────────────────────────────────────────

  @Test
  void persistHtmlResult_newArtifact_storageCalledDbHtmlNullKeyStored() throws IOException {
    String sessionId = "sess-1";
    String rawHtml = "<html>raw</html>";
    String assembledHtml = "<html>assembled</html>";
    String storageKey = "sess-1/uuid_art-id-1.html";

    when(artifactAssembler.assemble(sessionId, rawHtml)).thenReturn(assembledHtml);
    // First save assigns an ID (simulating @UuidGenerator); second save returns same object.
    when(artifacts.save(any(Artifact.class)))
        .thenAnswer(
            invocation -> {
              Artifact artifact = invocation.getArgument(0);
              assignId(artifact, "art-id-1");
              return artifact;
            });
    when(fileStorage.store(
            eq(StorageCategory.ARTIFACT),
            eq(sessionId),
            eq("art-id-1.html"),
            any(ByteArrayInputStream.class)))
        .thenReturn(storageKey);

    String artifactId =
        writer.persistHtmlResult(sessionId, rawHtml, "[]", null, "answer text", "Dashboard Title");

    // Returned id must match what the repository assigned.
    assertThat(artifactId).isEqualTo("art-id-1");

    // artifacts.save called twice: once for ID, once to persist the storageKey.
    ArgumentCaptor<Artifact> captor = ArgumentCaptor.forClass(Artifact.class);
    verify(artifacts, times(2)).save(captor.capture());
    List<Artifact> allSaves = captor.getAllValues();

    // After the second save the artifact carries the storageKey.
    assertThat(allSaves.get(1).getHtmlStorageKey()).isEqualTo(storageKey);

    // AI message must be persisted.
    verify(messages).save(any());
  }

  @Test
  void persistHtmlResult_newArtifact_assetProfileSetToCurrentProfile() throws IOException {
    String sessionId = "sess-profile";
    String rawHtml = "<html>raw</html>";

    when(artifactAssembler.assemble(sessionId, rawHtml)).thenReturn("<html>assembled</html>");
    when(artifacts.save(any(Artifact.class)))
        .thenAnswer(
            invocation -> {
              assignId(invocation.getArgument(0), "art-profile-id");
              return invocation.getArgument(0);
            });
    when(fileStorage.store(any(), any(), any(), any())).thenReturn("some-key");

    writer.persistHtmlResult(sessionId, rawHtml, "[]", null, "answer", "Title");

    ArgumentCaptor<Artifact> captor = ArgumentCaptor.forClass(Artifact.class);
    verify(artifacts, times(2)).save(captor.capture());
    // assetProfile must be stamped with the currentProfile from ArtifactRewriteProperties.
    assertThat(captor.getAllValues().get(0).getAssetProfile()).isEqualTo("tw3-ec5");
  }

  @Test
  void persistHtmlResult_storageThrowsIOException_wrapsAsRuntimeExceptionAiMessageNotSaved()
      throws IOException {
    String sessionId = "sess-err";
    String rawHtml = "<html>raw</html>";

    when(artifactAssembler.assemble(sessionId, rawHtml)).thenReturn("<html>assembled</html>");
    when(artifacts.save(any(Artifact.class)))
        .thenAnswer(
            invocation -> {
              assignId(invocation.getArgument(0), "art-id-err");
              return invocation.getArgument(0);
            });
    when(fileStorage.store(any(), any(), any(), any())).thenThrow(new IOException("disk full"));

    assertThatThrownBy(
            () -> writer.persistHtmlResult(sessionId, rawHtml, "[]", null, "text", "Title"))
        .isInstanceOf(RuntimeException.class)
        .hasMessageContaining("Failed to store artifact HTML")
        .hasCauseInstanceOf(IOException.class);

    // The AI message must NOT be saved when storage fails (transaction rolls back).
    verify(messages, never()).save(any());
  }

  @Test
  void persistHtmlResult_storesUsingArtifactIdAsFilename() throws IOException {
    String sessionId = "sess-3";
    String rawHtml = "<html>r</html>";

    when(artifactAssembler.assemble(sessionId, rawHtml)).thenReturn("<html>a</html>");
    when(artifacts.save(any(Artifact.class)))
        .thenAnswer(
            invocation -> {
              assignId(invocation.getArgument(0), "specific-artifact-id");
              return invocation.getArgument(0);
            });
    when(fileStorage.store(any(), any(), any(), any())).thenReturn("some-key");

    writer.persistHtmlResult(sessionId, rawHtml, "[]", null, "answer", "Title");

    // Filename passed to storage must be <artifactId>.html.
    verify(fileStorage)
        .store(eq(StorageCategory.ARTIFACT), eq(sessionId), eq("specific-artifact-id.html"), any());
  }

  @Test
  void persistHtmlResult_assembleInjectsData_storesRawFileAndNoClob() throws IOException {
    String rawHtmlWithMarker = "<html>raw __ERD_DATA__</html>";
    when(artifactAssembler.assemble(eq("session-1"), eq(rawHtmlWithMarker)))
        .thenReturn("<html>assembled</html>");
    when(artifacts.save(any(Artifact.class)))
        .thenAnswer(
            invocation -> {
              Artifact artifact = invocation.getArgument(0);
              if (artifact.getId() == null) {
                assignId(artifact, "art-changed-id");
              }
              return artifact;
            });
    when(fileStorage.store(eq(StorageCategory.ARTIFACT), eq("session-1"), anyString(), any()))
        .thenReturn("key-assembled", "key-raw");

    writer.persistHtmlResult("session-1", rawHtmlWithMarker, "[]", null, "answer", "Version 1");

    ArgumentCaptor<Artifact> savedArtifact = ArgumentCaptor.forClass(Artifact.class);
    verify(artifacts, atLeastOnce()).save(savedArtifact.capture());
    Artifact finalState = savedArtifact.getValue();
    assertThat(finalState.getRawHtmlStorageKey()).isEqualTo("key-raw");
    verify(fileStorage)
        .store(eq(StorageCategory.ARTIFACT), eq("session-1"), endsWith(".raw.html"), any());
  }

  @Test
  void persistHtmlResult_assembleInjectsNoData_stillStoresRawFile() throws IOException {
    // The deepagent line has no __ERD_DATA__ marker; raw is stored regardless so version-edits
    // load the clean pre-assemble base (loadRawHtml prefers the raw key).
    String rawHtmlWithoutMarker = "<html>same</html>";
    when(artifactAssembler.assemble(eq("session-1"), eq(rawHtmlWithoutMarker)))
        .thenReturn("<html>same</html>");
    when(artifacts.save(any(Artifact.class)))
        .thenAnswer(
            invocation -> {
              Artifact artifact = invocation.getArgument(0);
              if (artifact.getId() == null) {
                assignId(artifact, "art-noop-id");
              }
              return artifact;
            });
    when(fileStorage.store(eq(StorageCategory.ARTIFACT), eq("session-1"), anyString(), any()))
        .thenReturn("key-assembled", "key-raw");

    writer.persistHtmlResult("session-1", rawHtmlWithoutMarker, "[]", null, "answer", "Version 1");

    ArgumentCaptor<Artifact> savedArtifact = ArgumentCaptor.forClass(Artifact.class);
    verify(artifacts, atLeastOnce()).save(savedArtifact.capture());
    assertThat(savedArtifact.getValue().getRawHtmlStorageKey()).isEqualTo("key-raw");
    verify(fileStorage)
        .store(eq(StorageCategory.ARTIFACT), eq("session-1"), endsWith(".raw.html"), any());
  }

  // ── helpers ───────────────────────────────────────────────────────────────

  /** Assigns an id to an Artifact via reflection (simulating {@code @UuidGenerator}). */
  private static void assignId(Artifact artifact, String id) {
    try {
      Field idField = Artifact.class.getDeclaredField("id");
      idField.setAccessible(true);
      idField.set(artifact, id);
    } catch (ReflectiveOperationException ex) {
      throw new RuntimeException("Could not assign id to Artifact in test", ex);
    }
  }
}
