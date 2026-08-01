package com.erd.cowork.config;

import java.time.Duration;
import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "erd.storage")
public record StorageProperties(
    String type,
    String localDir,
    String workspaceDir,
    Cleanup cleanup,
    Retention retention,
    S3 s3) {

  /** Scheduling knobs for {@code RetentionCleanupService}. */
  public record Cleanup(String cron, boolean dryRun) {}

  /**
   * Per-data-class retention windows. {@code uploads} and {@code workspace} are measured from the
   * session's last activity; {@code artifact} is measured from the artifact's own creation time.
   */
  public record Retention(Duration uploads, Duration workspace, Duration artifact) {}

  /**
   * S3-compatible storage configuration. Used only when {@code erd.storage.type=s3}. Credentials
   * are never placed here — they are picked up from the standard AWS provider chain (env vars
   * {@code AWS_ACCESS_KEY_ID} / {@code AWS_SECRET_ACCESS_KEY}).
   */
  public record S3(String bucket, String region, String endpoint, boolean pathStyleAccess) {}
}
