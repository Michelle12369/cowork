package com.erd.cowork.support;

import static org.assertj.core.api.Assertions.assertThat;

import com.erd.cowork.config.PersistenceConfig;
import com.erd.cowork.domain.ChatMessage;
import com.erd.cowork.domain.Sender;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.data.mongo.DataMongoTest;
import org.springframework.context.annotation.Import;
import org.springframework.data.mongodb.MongoTransactionManager;
import org.springframework.data.mongodb.core.MongoTemplate;
import org.springframework.transaction.support.TransactionTemplate;

/**
 * Branch 3 去風險 gate：證明多文件交易在嵌入單成員 replica set 上成立（standalone 無交易語意）。
 *
 * <p>測試連線走 main {@code application.properties} 的 {@code SPRING_DATA_MONGODB_URI}
 * （預設 localhost:27017，MUST 是單成員以上 replica set）。{@code @Import(PersistenceConfig.class)}
 * 仍需保留——{@code @DataMongoTest} slice 預設不掃使用者 {@code @Configuration}，需要它才能拿到 {@link
 * MongoTransactionManager} bean。
 */
@DataMongoTest
@Import(PersistenceConfig.class)
class TransactionSmokeTest {

  @Autowired MongoTemplate mongoTemplate;

  @Test
  void multiDocumentTransaction_secondWriteThrows_firstWriteRolledBack() {
    mongoTemplate.getCollection("chat_message").drop();
    TransactionTemplate transactionTemplate =
        new TransactionTemplate(
            new MongoTransactionManager(mongoTemplate.getMongoDatabaseFactory()));
    try {
      transactionTemplate.executeWithoutResult(
          status -> {
            ChatMessage first = new ChatMessage();
            first.setSessionId("s1");
            first.setSender(Sender.AI);
            mongoTemplate.save(first);
            throw new RuntimeException("boom after first write");
          });
    } catch (RuntimeException expected) {
      // rolled back
    }
    assertThat(mongoTemplate.getCollection("chat_message").countDocuments()).isZero();
  }
}
