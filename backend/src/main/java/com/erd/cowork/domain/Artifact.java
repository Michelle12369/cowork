package com.erd.cowork.domain;

import java.time.Instant;
import lombok.EqualsAndHashCode;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import org.springframework.data.annotation.CreatedDate;
import org.springframework.data.annotation.Id;
import org.springframework.data.mongodb.core.mapping.Document;

@Document(collection = "artifact")
@Getter
@Setter
@EqualsAndHashCode(of = "id")
@NoArgsConstructor
public class Artifact {

  @Id private String id;
  private String sessionId;
  private String title;

  /**
   * Storage key for the raw (pre-assembly) model HTML in {@link
   * com.erd.cowork.storage.FileStorage}. Null when assemble performs no data injection (deepagent
   * line); readers then fall back to {@link #htmlStorageKey}.
   */
  private String rawHtmlStorageKey;

  /**
   * Storage key for the assembled (data-injected) HTML, written by {@link
   * com.erd.cowork.storage.FileStorage}.
   */
  private String htmlStorageKey;

  /**
   * Asset generation profile used when this artifact was generated (e.g. {@code tw3-ec5}). Used by
   * {@link com.erd.cowork.artifact.ArtifactCdnRewriter} to apply the correct CDN-to-vendor rewrite
   * rules at serve time. Null for artifacts created before per-profile tracking was introduced;
   * these fall back to the legacy default profile.
   */
  private String assetProfile;

  /**
   * Recipe (schema §4) as raw JSON, capturing this version's data-source fetches; null for
   * upload-only or pre-replay artifacts.
   */
  private String recipeJson;

  /**
   * Whether this version's dashboard drew on any uploaded (non-API) source — gates static-only
   * sharing.
   */
  private Boolean hasUploadSources;

  @CreatedDate private Instant createdAt;
}
