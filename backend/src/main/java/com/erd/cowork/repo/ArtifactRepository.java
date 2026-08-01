package com.erd.cowork.repo;

import com.erd.cowork.domain.Artifact;
import java.time.Instant;
import java.util.List;
import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;

public interface ArtifactRepository extends JpaRepository<Artifact, String> {

  Optional<Artifact> findFirstBySessionIdOrderByCreatedAtDesc(String sessionId);

  long countBySessionId(String sessionId);

  List<Artifact> findByCreatedAtBeforeAndHtmlStorageKeyIsNotNull(Instant cutoff);
}
