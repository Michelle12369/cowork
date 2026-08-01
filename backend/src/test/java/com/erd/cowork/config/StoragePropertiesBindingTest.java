package com.erd.cowork.config;

import static org.assertj.core.api.Assertions.assertThat;

import java.time.Duration;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.TestPropertySource;

@SpringBootTest
@TestPropertySource(
    properties = {
      "erd.storage.cleanup.cron=0 30 4 * * *",
      "erd.storage.cleanup.dry-run=true",
      "erd.storage.retention.uploads=90d",
      "erd.storage.retention.workspace=200d",
      "erd.storage.retention.artifact=730d"
    })
class StoragePropertiesBindingTest {

  @Autowired StorageProperties storageProperties;

  @Test
  void binding_durationValues_parsesSimpleDayNotation() {
    assertThat(storageProperties.retention().uploads()).isEqualTo(Duration.ofDays(90));
    assertThat(storageProperties.retention().workspace()).isEqualTo(Duration.ofDays(200));
    assertThat(storageProperties.retention().artifact()).isEqualTo(Duration.ofDays(730));
  }

  @Test
  void binding_cleanupBlock_readsCronAndDryRun() {
    assertThat(storageProperties.cleanup().cron()).isEqualTo("0 30 4 * * *");
    assertThat(storageProperties.cleanup().dryRun()).isTrue();
  }
}
