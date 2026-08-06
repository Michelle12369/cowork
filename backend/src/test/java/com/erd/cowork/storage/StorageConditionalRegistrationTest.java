package com.erd.cowork.storage;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;

import com.erd.cowork.config.StorageProperties;
import com.erd.cowork.config.StorageProperties.S3;
import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;
import software.amazon.awssdk.services.s3.S3Client;

/**
 * Verifies that {@link S3FileStorage}/{@link LocalDiskStorage} and {@link S3WorkspacePurger}/{@link
 * LocalWorkspacePurger} are each registered exclusively based on the {@code erd.storage.type}
 * property.
 */
class StorageConditionalRegistrationTest {

  private static final StorageProperties S3_PROPS =
      new StorageProperties(
          "s3", null, null, null, null, new S3("", "bucket", "test-access-key", "test-secret-key"));

  private static final StorageProperties LOCAL_PROPS =
      new StorageProperties("local", System.getProperty("java.io.tmpdir"), null, null, null, null);

  @Test
  void whenTypeIsS3_onlyS3FileStorageBeanIsCreated() {
    new ApplicationContextRunner()
        .withPropertyValues("erd.storage.type=s3")
        .withBean(StorageProperties.class, () -> S3_PROPS)
        .withBean(S3Client.class, () -> mock(S3Client.class))
        .withUserConfiguration(S3FileStorage.class, LocalDiskStorage.class)
        .run(
            context -> {
              assertThat(context).hasSingleBean(FileStorage.class);
              assertThat(context.getBean(FileStorage.class)).isInstanceOf(S3FileStorage.class);
              assertThat(context).doesNotHaveBean(LocalDiskStorage.class);
            });
  }

  @Test
  void whenTypeIsLocal_onlyLocalDiskStorageBeanIsCreated() {
    new ApplicationContextRunner()
        .withPropertyValues("erd.storage.type=local")
        .withBean(StorageProperties.class, () -> LOCAL_PROPS)
        .withUserConfiguration(LocalDiskStorage.class, S3FileStorage.class)
        .run(
            context -> {
              assertThat(context).hasSingleBean(FileStorage.class);
              assertThat(context.getBean(FileStorage.class)).isInstanceOf(LocalDiskStorage.class);
              assertThat(context).doesNotHaveBean(S3FileStorage.class);
            });
  }

  @Test
  void whenTypeIsNotSet_localDiskStorageIsDefault() {
    new ApplicationContextRunner()
        .withBean(StorageProperties.class, () -> LOCAL_PROPS)
        .withUserConfiguration(LocalDiskStorage.class, S3FileStorage.class)
        .run(
            context -> {
              assertThat(context).hasSingleBean(FileStorage.class);
              assertThat(context.getBean(FileStorage.class)).isInstanceOf(LocalDiskStorage.class);
            });
  }

  @Test
  void whenTypeIsS3_onlyS3WorkspacePurgerBeanIsCreated() {
    new ApplicationContextRunner()
        .withPropertyValues("erd.storage.type=s3")
        .withBean(StorageProperties.class, () -> S3_PROPS)
        .withBean(S3Client.class, () -> mock(S3Client.class))
        .withUserConfiguration(S3WorkspacePurger.class, LocalWorkspacePurger.class)
        .run(
            context -> {
              assertThat(context).hasSingleBean(WorkspacePurger.class);
              assertThat(context.getBean(WorkspacePurger.class))
                  .isInstanceOf(S3WorkspacePurger.class);
              assertThat(context).doesNotHaveBean(LocalWorkspacePurger.class);
            });
  }

  @Test
  void whenTypeIsLocal_onlyLocalWorkspacePurgerBeanIsCreated() {
    new ApplicationContextRunner()
        .withPropertyValues("erd.storage.type=local")
        .withBean(StorageProperties.class, () -> LOCAL_PROPS)
        .withUserConfiguration(LocalWorkspacePurger.class, S3WorkspacePurger.class)
        .run(
            context -> {
              assertThat(context).hasSingleBean(WorkspacePurger.class);
              assertThat(context.getBean(WorkspacePurger.class))
                  .isInstanceOf(LocalWorkspacePurger.class);
              assertThat(context).doesNotHaveBean(S3WorkspacePurger.class);
            });
  }

  @Test
  void whenTypeIsNotSet_localWorkspacePurgerIsDefault() {
    new ApplicationContextRunner()
        .withBean(StorageProperties.class, () -> LOCAL_PROPS)
        .withUserConfiguration(LocalWorkspacePurger.class, S3WorkspacePurger.class)
        .run(
            context -> {
              assertThat(context).hasSingleBean(WorkspacePurger.class);
              assertThat(context.getBean(WorkspacePurger.class))
                  .isInstanceOf(LocalWorkspacePurger.class);
            });
  }
}
