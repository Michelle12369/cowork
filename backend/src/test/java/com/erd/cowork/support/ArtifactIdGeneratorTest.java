package com.erd.cowork.support;

import static org.assertj.core.api.Assertions.assertThat;

import com.erd.cowork.config.PersistenceConfig;
import com.erd.cowork.domain.Artifact;
import com.erd.cowork.domain.ChatMessage;
import com.erd.cowork.domain.Sender;
import com.erd.cowork.domain.UploadedFile;
import com.erd.cowork.repo.ArtifactRepository;
import com.erd.cowork.repo.ChatMessageRepository;
import com.erd.cowork.repo.UploadedFileRepository;
import java.util.regex.Pattern;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.data.mongo.DataMongoTest;
import org.springframework.context.annotation.Import;

/**
 * Covers the {@code BeforeConvertCallback}s added to {@code PersistenceConfig} for {@code
 * Artifact}/{@code ChatMessage}/{@code UploadedFile}: without them, Spring Data Mongo's default
 * behaviour for a null {@code String @Id} assigns a 24-character ObjectId hex string, not the
 * 36-character UUID the spec requires (the format previously produced by JPA's
 * {@code @UuidGenerator}). All three entities are covered independently -- an ObjectId hex string
 * is a legal {@code String} id too, so a test against only one entity would not catch a format
 * regression reintroduced on another.
 *
 * <p>{@code @DataMongoTest} does not component-scan arbitrary {@code @Configuration} classes, so
 * {@code PersistenceConfig} (and its entity callback beans) must be imported explicitly.
 */
@DataMongoTest
@Import(PersistenceConfig.class)
class ArtifactIdGeneratorTest {

  private static final Pattern UUID_PATTERN =
      Pattern.compile("^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$");

  @Autowired ArtifactRepository artifacts;
  @Autowired ChatMessageRepository messages;
  @Autowired UploadedFileRepository files;

  @Test
  void save_newArtifactWithNullId_generatesUuidFormatId() {
    Artifact artifact = new Artifact();
    artifact.setSessionId("44444444-4444-4444-4444-444444444444");
    artifact.setTitle("t");

    Artifact saved = artifacts.save(artifact);

    assertUuidFormat(saved.getId());
  }

  @Test
  void save_newChatMessageWithNullId_generatesUuidFormatId() {
    ChatMessage message = new ChatMessage();
    message.setSessionId("44444444-4444-4444-4444-444444444444");
    message.setSender(Sender.USER);
    message.setText("hello");

    ChatMessage saved = messages.save(message);

    assertUuidFormat(saved.getId());
  }

  @Test
  void save_newUploadedFileWithNullId_generatesUuidFormatId() {
    UploadedFile file = new UploadedFile();
    file.setSessionId("44444444-4444-4444-4444-444444444444");
    file.setName("data.csv");
    file.setAlias("data");
    file.setStorageKey("uploads/data.csv");
    file.setType("csv");

    UploadedFile saved = files.save(file);

    assertUuidFormat(saved.getId());
  }

  private void assertUuidFormat(String id) {
    assertThat(id).isNotNull();
    assertThat(id).hasSize(36);
    assertThat(UUID_PATTERN.matcher(id).matches()).isTrue();
  }
}
