package com.erd.cowork.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EntityListeners;
import jakarta.persistence.Id;
import jakarta.persistence.Lob;
import jakarta.persistence.Table;
import java.time.Instant;
import lombok.EqualsAndHashCode;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import org.hibernate.annotations.UuidGenerator;
import org.springframework.data.annotation.CreatedDate;
import org.springframework.data.jpa.domain.support.AuditingEntityListener;

@Entity
@Table(name = "artifact")
@EntityListeners(AuditingEntityListener.class)
@Getter
@Setter
@EqualsAndHashCode(of = "id")
@NoArgsConstructor
public class Artifact {

  @Id
  @UuidGenerator
  @Column(length = 36)
  private String id;

  @Column(nullable = false, length = 36)
  private String sessionId;

  @Column(nullable = false, length = 300)
  private String title;

  @Lob private String rawHtml;

  /**
   * Storage key for the raw (pre-assembly) model HTML in {@link
   * com.erd.cowork.storage.FileStorage}. Null when assemble performs no data injection (deepagent
   * line); readers then fall back to {@link #htmlStorageKey}.
   */
  @Column(length = 500)
  private String rawHtmlStorageKey;

  /**
   * Storage key for the assembled (data-injected) HTML, written by {@link
   * com.erd.cowork.storage.FileStorage}.
   */
  @Column(length = 500)
  private String htmlStorageKey;

  /**
   * Asset generation profile used when this artifact was generated (e.g. {@code tw3-ec5}). Used by
   * {@link com.erd.cowork.artifact.ArtifactCdnRewriter} to apply the correct CDN-to-vendor rewrite
   * rules at serve time. Null for artifacts created before per-profile tracking was introduced;
   * these fall back to the legacy default profile.
   */
  @Column(length = 40)
  private String assetProfile;

  @CreatedDate
  @Column(nullable = false, updatable = false)
  private Instant createdAt;
}
