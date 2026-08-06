package com.erd.cowork.storage;

import java.io.IOException;

/**
 * Removes a session's deepagent workspace, backed by either the local RWX volume or S3, selected
 * via {@code erd.storage.type}. Consumed by {@code WorkspaceRetentionService}, which drives the
 * nightly retention pass from {@code chat_session.updated_at}.
 */
public interface WorkspacePurger {

  /** True if the session leaves anything purgeable (directory / objects). Never deletes. */
  boolean sessionExists(String userId, String sessionId);

  /** Deletes the session's workspace. Returns true if anything was actually removed. */
  boolean purgeSession(String userId, String sessionId) throws IOException;
}
