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
   * Projects only {@code id}/{@code htmlStorageKey} for retention cleanup -- {@link Artifact} has
   * an eager, unbounded {@code rawHtml} CLOB, so selecting full entities here would materialize
   * every stale artifact's HTML in heap at once.
   */
  @Query(
      "select a.id as id, a.htmlStorageKey as htmlStorageKey from Artifact a "
          + "where a.createdAt < :cutoff and a.htmlStorageKey is not null")
  List<ArtifactStorageKeyView> findStaleArtifactStorageKeys(@Param("cutoff") Instant cutoff);

  /** Targeted column update so clearing the key never re-writes the {@code rawHtml} CLOB. */
  @Modifying
  @Transactional
  @Query("update Artifact a set a.htmlStorageKey = null where a.id = :id")
  void clearHtmlStorageKey(@Param("id") String id);

  /** Narrow read projection backing {@link #findStaleArtifactStorageKeys(Instant)}. */
  interface ArtifactStorageKeyView {
    String getId();

    String getHtmlStorageKey();
  }
}
