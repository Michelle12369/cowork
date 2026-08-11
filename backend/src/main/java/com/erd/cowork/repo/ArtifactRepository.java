package com.erd.cowork.repo;

import com.erd.cowork.domain.Artifact;
import java.time.Instant;
import java.util.List;
import java.util.Optional;
import org.springframework.data.mongodb.repository.MongoRepository;
import org.springframework.data.mongodb.repository.Query;
import org.springframework.data.mongodb.repository.Update;
import org.springframework.data.repository.query.Param;

public interface ArtifactRepository extends MongoRepository<Artifact, String> {

  Optional<Artifact> findFirstBySessionIdOrderByCreatedAtDesc(String sessionId);

  long countBySessionId(String sessionId);

  @Query(
      "{ 'createdAt': { $lt: ?0 }, $or: [ {'htmlStorageKey': {$ne: null}}, {'rawHtmlStorageKey':"
          + " {$ne: null}} ] }")
  List<Artifact> findStaleArtifactStorageKeys(@Param("cutoff") Instant cutoff);

  @Query("{ '_id': ?0 }")
  @Update("{ $set: { 'htmlStorageKey': null } }")
  void clearHtmlStorageKey(@Param("id") String id);

  @Query("{ '_id': ?0 }")
  @Update("{ $set: { 'rawHtmlStorageKey': null } }")
  void clearRawHtmlStorageKey(@Param("id") String id);
}
