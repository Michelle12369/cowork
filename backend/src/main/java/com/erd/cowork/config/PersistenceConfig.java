package com.erd.cowork.config;

import com.erd.cowork.domain.Artifact;
import com.erd.cowork.domain.ChatMessage;
import com.erd.cowork.domain.ChatSession;
import com.erd.cowork.domain.UploadedFile;
import java.util.UUID;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.data.mongodb.MongoDatabaseFactory;
import org.springframework.data.mongodb.MongoTransactionManager;
import org.springframework.data.mongodb.config.EnableMongoAuditing;
import org.springframework.data.mongodb.core.mapping.event.AfterConvertCallback;
import org.springframework.data.mongodb.core.mapping.event.AfterSaveCallback;
import org.springframework.data.mongodb.core.mapping.event.BeforeConvertCallback;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

/** Branch 3：replica set，多文件交易由 MongoTransactionManager 提供。 */
@Configuration
@EnableMongoAuditing
public class PersistenceConfig {

  /**
   * {@code MongoTransactionManager}（driver-level {@code ClientSession} 交易）——不像 driver 的 {@code
   * withTransaction} helper，不會自動重試 transient transaction error（例如 write conflict）。目前三條交易路徑 （{@code
   * AgentConversationWriter.persistHtmlResult}、{@code ArtifactRepairService}、{@code
   * FileService.upload} 批次）寫入的都是 fresh-UUID 產生的新文件，同文件並發寫入機率低，可接受無重試；未來若出現高並發
   * 的同文件寫入路徑（例如多個請求同時更新同一筆既有文件），MUST 另外評估是否需要重試包裝。
   */
  @Bean
  MongoTransactionManager transactionManager(MongoDatabaseFactory databaseFactory) {
    return new MongoTransactionManager(databaseFactory);
  }

  @Bean
  TransactionTemplate transactionTemplate(PlatformTransactionManager transactionManager) {
    return new TransactionTemplate(transactionManager);
  }

  /** 載入既有 session 後標 not-new，讓後續 save 走 replace 而非 insert（取代 JPA @PostLoad）。 */
  @Bean
  AfterConvertCallback<ChatSession> chatSessionAfterConvert() {
    return (session, document, collection) -> {
      session.markNotNew();
      return session;
    };
  }

  /**
   * 任何 save（含首次 insert）完成後一律標 not-new，取代 JPA {@code @PostPersist}。沒有這個掛鉤， 呼叫端第一次 {@code save()} 建新
   * session 後，同一個記憶體物件的 {@code isNew} 仍是 true—— 若同一物件再被 save 一次（例如 {@code
   * AgentOrchestrator.prepare} / {@code FileService.upload} 在建立後緊接著更新 {@code updatedAt}）就會誤判成
   * insert，對已存在的 {@code _id} 撞 {@code DuplicateKeyException}。
   */
  @Bean
  AfterSaveCallback<ChatSession> chatSessionAfterSave() {
    return (session, document, collection) -> {
      session.markNotNew();
      return session;
    };
  }

  /**
   * 補上 JPA {@code @UuidGenerator} 在 persist 時生 UUID 的行為：Spring Data Mongo 對值為 null 的 {@code
   * String @Id} 預設會賦 ObjectId hex（24 字元），不符 spec 的 String UUID（36 字元）契約。 {@code ChatSession}
   * 不受影響——它是 client 指定 id 的 {@code Persistable}，id 一律由呼叫端在 save 前明確設定。
   */
  @Bean
  BeforeConvertCallback<Artifact> artifactIdGenerator() {
    return (artifact, collection) -> {
      if (artifact.getId() == null) {
        artifact.setId(UUID.randomUUID().toString());
      }
      return artifact;
    };
  }

  /** 同 {@link #artifactIdGenerator()}，補 {@code ChatMessage} 的 UUID id。 */
  @Bean
  BeforeConvertCallback<ChatMessage> chatMessageIdGenerator() {
    return (message, collection) -> {
      if (message.getId() == null) {
        message.setId(UUID.randomUUID().toString());
      }
      return message;
    };
  }

  /** 同 {@link #artifactIdGenerator()}，補 {@code UploadedFile} 的 UUID id。 */
  @Bean
  BeforeConvertCallback<UploadedFile> uploadedFileIdGenerator() {
    return (uploadedFile, collection) -> {
      if (uploadedFile.getId() == null) {
        uploadedFile.setId(UUID.randomUUID().toString());
      }
      return uploadedFile;
    };
  }
}
