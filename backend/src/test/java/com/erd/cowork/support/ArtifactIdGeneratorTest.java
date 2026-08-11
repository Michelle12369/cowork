package com.erd.cowork.support;

import static org.assertj.core.api.Assertions.assertThat;

import com.erd.cowork.config.PersistenceConfig;
import com.erd.cowork.domain.Artifact;
import com.erd.cowork.repo.ArtifactRepository;
import java.util.regex.Pattern;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.data.mongo.DataMongoTest;
import org.springframework.context.annotation.Import;

/**
 * Covers the {@code BeforeConvertCallback<Artifact>} added to {@code PersistenceConfig}: without
 * it, Spring Data Mongo's default behaviour for a null {@code String @Id} assigns a 24-character
 * ObjectId hex string, not the 36-character UUID the spec requires (the format previously produced
 * by JPA's {@code @UuidGenerator}).
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

  @Test
  void save_newArtifactWithNullId_generatesUuidFormatId() {
    Artifact artifact = new Artifact();
    artifact.setSessionId("44444444-4444-4444-4444-444444444444");
    artifact.setTitle("t");

    Artifact saved = artifacts.save(artifact);

    assertThat(saved.getId()).isNotNull();
    assertThat(saved.getId()).hasSize(36);
    assertThat(UUID_PATTERN.matcher(saved.getId()).matches()).isTrue();
  }
}
