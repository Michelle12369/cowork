package com.erd.cowork.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "erd.storage")
public record StorageProperties(String type, String localDir, int retentionDays, S3 s3) {

  /**
   * S3-compatible storage configuration. Used only when {@code erd.storage.type=s3}. Credentials
   * are never placed here — they are picked up from the standard AWS provider chain (env vars
   * {@code AWS_ACCESS_KEY_ID} / {@code AWS_SECRET_ACCESS_KEY}).
   */
  public record S3(String bucket, String region, String endpoint, boolean pathStyleAccess) {}
}
