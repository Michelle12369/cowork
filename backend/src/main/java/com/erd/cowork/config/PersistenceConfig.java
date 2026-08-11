package com.erd.cowork.config;

import com.erd.cowork.domain.ChatSession;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.data.mongodb.config.EnableMongoAuditing;
import org.springframework.data.mongodb.core.mapping.event.AfterConvertCallback;

/**
 * Branch 1（純遷移基座）：MongoDB standalone、無多文件交易，且本分支刻意不引入任何交易語意—— 移除 JPA 的
 * TransactionTemplate/transaction manager，服務改裸寫入。多文件原子性策略解耦到 Branch 2（補償）/ Branch 3（交易），本分支不含。
 */
@Configuration
@EnableMongoAuditing
public class PersistenceConfig {

  /** 載入既有 session 後標 not-new，讓後續 save 走 replace 而非 insert（取代 JPA @PostLoad）。 */
  @Bean
  AfterConvertCallback<ChatSession> chatSessionAfterConvert() {
    return (session, document, collection) -> {
      session.markNotNew();
      return session;
    };
  }
}
