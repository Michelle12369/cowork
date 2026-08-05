package com.erd.cowork.repo;

import com.erd.cowork.domain.Artifact;
import java.time.Instant;
import java.util.List;
import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.transaction.annotation.Transactional;

public interface ArtifactRepository extends JpaRepository<Artifact, String> {

  Optional<Artifact> findFirstBySessionIdOrderByCreatedAtDesc(String sessionId);

  long countBySessionId(String sessionId);

  /**
   * Projects only id/keys for retention cleanup — never the full entity, so stale artifacts are not
   * materialized in heap at once.
   */
  @Query(
      "select a.id as id, a.htmlStorageKey as htmlStorageKey, "
          + "a.rawHtmlStorageKey as rawHtmlStorageKey from Artifact a "
          + "where a.createdAt < :cutoff "
          + "and (a.htmlStorageKey is not null or a.rawHtmlStorageKey is not null)")
  List<ArtifactStorageKeyView> findStaleArtifactStorageKeys(@Param("cutoff") Instant cutoff);

  /** Targeted column update; the row itself is kept for message references. */
  @Modifying
  @Transactional
  @Query("update Artifact a set a.htmlStorageKey = null where a.id = :id")
  void clearHtmlStorageKey(@Param("id") String id);

  /** Targeted column update; the row itself is kept for message references. */
  @Modifying
  @Transactional
  @Query("update Artifact a set a.rawHtmlStorageKey = null where a.id = :id")
  void clearRawHtmlStorageKey(@Param("id") String id);

  /** Narrow read projection backing {@link #findStaleArtifactStorageKeys(Instant)}. */
  interface ArtifactStorageKeyView {
    String getId();

    String getHtmlStorageKey();

    String getRawHtmlStorageKey();
  }
}
