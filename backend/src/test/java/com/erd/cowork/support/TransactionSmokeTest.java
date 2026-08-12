package com.erd.cowork.support;

import static org.assertj.core.api.Assertions.assertThat;

import com.erd.cowork.config.PersistenceConfig;
import com.erd.cowork.domain.ChatMessage;
import com.erd.cowork.domain.Sender;
import de.flapdoodle.embed.mongo.spring.autoconfigure.EmbeddedMongoAutoConfiguration;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.data.mongo.DataMongoTest;
import org.springframework.context.annotation.Import;
import org.springframework.data.mongodb.MongoTransactionManager;
import org.springframework.data.mongodb.core.MongoTemplate;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.transaction.support.TransactionTemplate;

/**
 * Branch 3 去風險 gate：證明多文件交易在嵌入單成員 replica set 上成立（standalone 無交易語意）。
 *
 * <p>排除 spring3x 的 {@link EmbeddedMongoAutoConfiguration}——它會依 {@code
 * de.flapdoodle.mongodb.embedded.version} 屬性另起一個 standalone mongod，與 {@link
 * EmbeddedReplicaSetMongo} 提供的 replica-set uri 衝突。其餘 {@code @DataMongoTest} 測試不受影響，仍走 standalone
 * autoconfig。
 */
@DataMongoTest(excludeAutoConfiguration = EmbeddedMongoAutoConfiguration.class)
@Import(PersistenceConfig.class)
class TransactionSmokeTest {

  @DynamicPropertySource
  static void mongoUri(DynamicPropertyRegistry registry) {
    EmbeddedReplicaSetMongo.registerUri(registry);
  }

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
