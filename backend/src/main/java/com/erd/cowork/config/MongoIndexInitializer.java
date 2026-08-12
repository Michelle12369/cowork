package com.erd.cowork.config;

import com.erd.cowork.domain.Artifact;
import com.erd.cowork.domain.ChatMessage;
import com.erd.cowork.domain.ChatSession;
import com.erd.cowork.domain.UploadedFile;
import lombok.RequiredArgsConstructor;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.data.domain.Sort;
import org.springframework.data.mongodb.core.MongoTemplate;
import org.springframework.data.mongodb.core.index.Index;
import org.springframework.stereotype.Component;

/** 啟動時建立索引（取代 Flyway）。 */
@Component
@RequiredArgsConstructor
public class MongoIndexInitializer {

  private final MongoTemplate mongoTemplate;

  @EventListener(ApplicationReadyEvent.class)
  public void createIndexes() {
    mongoTemplate
        .indexOps(ChatSession.class)
        .ensureIndex(
            new Index().on("userId", Sort.Direction.ASC).on("updatedAt", Sort.Direction.DESC));
    mongoTemplate
        .indexOps(ChatSession.class)
        .ensureIndex(new Index().on("updatedAt", Sort.Direction.ASC));
    mongoTemplate
        .indexOps(ChatMessage.class)
        .ensureIndex(
            new Index().on("sessionId", Sort.Direction.ASC).on("createdAt", Sort.Direction.ASC));
    mongoTemplate
        .indexOps(UploadedFile.class)
        .ensureIndex(
            new Index().on("sessionId", Sort.Direction.ASC).on("expired", Sort.Direction.ASC));
    mongoTemplate
        .indexOps(UploadedFile.class)
        .ensureIndex(
            new Index()
                .on("sessionId", Sort.Direction.ASC)
                .on("alias", Sort.Direction.ASC)
                .unique());
    mongoTemplate
        .indexOps(Artifact.class)
        .ensureIndex(
            new Index().on("sessionId", Sort.Direction.ASC).on("createdAt", Sort.Direction.DESC));
    mongoTemplate
        .indexOps(Artifact.class)
        .ensureIndex(new Index().on("createdAt", Sort.Direction.ASC));
  }
}
