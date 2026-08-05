package com.erd.cowork.storage;

import static org.assertj.core.api.Assertions.assertThat;

import com.erd.cowork.config.StorageProperties;
import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;

/**
 * Verifies that {@link LocalDiskStorage} is registered when {@code erd.storage.type=local} or
 * unset. The {@code type=s3} side (only {@code S3FileStorage} registered) is added once that class
 * exists.
 */
class StorageConditionalRegistrationTest {

  private static final StorageProperties LOCAL_PROPS =
      new StorageProperties("local", System.getProperty("java.io.tmpdir"), null, null, null, null);

  @Test
  void whenTypeIsLocal_onlyLocalDiskStorageBeanIsCreated() {
    new ApplicationContextRunner()
        .withPropertyValues("erd.storage.type=local")
        .withBean(StorageProperties.class, () -> LOCAL_PROPS)
        .withUserConfiguration(LocalDiskStorage.class)
        .run(
            context -> {
              assertThat(context).hasSingleBean(FileStorage.class);
              assertThat(context.getBean(FileStorage.class)).isInstanceOf(LocalDiskStorage.class);
            });
  }

  @Test
  void whenTypeIsNotSet_localDiskStorageIsDefault() {
    new ApplicationContextRunner()
        .withBean(StorageProperties.class, () -> LOCAL_PROPS)
        .withUserConfiguration(LocalDiskStorage.class)
        .run(
            context -> {
              assertThat(context).hasSingleBean(FileStorage.class);
              assertThat(context.getBean(FileStorage.class)).isInstanceOf(LocalDiskStorage.class);
            });
  }
}
