package com.erd.cowork.config;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.data.jpa.repository.config.EnableJpaAuditing;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

@Configuration
@EnableJpaAuditing
public class PersistenceConfig {

  /**
   * Explicit {@link TransactionTemplate} so services can scope a short programmatic transaction
   * around a batch of writes while keeping expensive IO (storage + parsing) outside any
   * transaction.
   */
  @Bean
  public TransactionTemplate transactionTemplate(PlatformTransactionManager transactionManager) {
    return new TransactionTemplate(transactionManager);
  }
}
