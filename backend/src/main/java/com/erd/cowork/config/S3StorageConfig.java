package com.erd.cowork.config;

import java.net.URI;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.util.StringUtils;
import software.amazon.awssdk.auth.credentials.AwsBasicCredentials;
import software.amazon.awssdk.auth.credentials.StaticCredentialsProvider;
import software.amazon.awssdk.regions.Region;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.S3ClientBuilder;
import software.amazon.awssdk.services.s3.S3Configuration;

/**
 * Creates the {@link S3Client} bean when {@code erd.storage.type=s3}. Credentials are built
 * explicitly from {@link StorageProperties.S3} — not resolved via the SDK's default provider chain
 * — mirroring deepagent's {@code build_s3_client()} (Settings 顯式建構，同一設定來源，不依賴 SDK 自動探測)。
 */
@Configuration
@ConditionalOnProperty(prefix = "erd.storage", name = "type", havingValue = "s3")
public class S3StorageConfig {

  @Bean
  public S3Client s3Client(StorageProperties storageProperties) {
    StorageProperties.S3 s3 = storageProperties.s3();
    // S3-compatible 物件儲存不使用 region，SDK 必填故用 AWS_GLOBAL。
    S3ClientBuilder builder =
        S3Client.builder()
            .region(Region.AWS_GLOBAL)
            .credentialsProvider(
                StaticCredentialsProvider.create(
                    AwsBasicCredentials.create(s3.accessKey(), s3.secretKey())))
            // MinIO/內部物件儲存需要 path-style，virtual-hosted 對非 AWS endpoint 解析失敗。
            .serviceConfiguration(S3Configuration.builder().pathStyleAccessEnabled(true).build());

    if (StringUtils.hasText(s3.endpoint())) {
      builder.endpointOverride(URI.create(s3.endpoint()));
    }

    return builder.build();
  }
}
