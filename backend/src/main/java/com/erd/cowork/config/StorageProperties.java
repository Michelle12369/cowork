package com.erd.cowork.config;

import java.time.Duration;
import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "erd.storage")
public record StorageProperties(
    String localDir, String workspaceDir, Cleanup cleanup, Retention retention) {

  /** Scheduling knobs for {@code RetentionCleanupService}. */
  public record Cleanup(String cron, boolean dryRun) {}

  /**
   * Per-data-class retention windows. {@code uploads} and {@code workspace} are measured from the
   * session's last activity; {@code artifact} is measured from the artifact's own creation time.
   */
  public record Retention(Duration uploads, Duration workspace, Duration artifact) {}
}
