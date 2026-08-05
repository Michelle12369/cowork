package com.erd.cowork.storage;

import com.erd.cowork.config.StorageProperties;
import java.io.IOException;
import java.nio.file.FileVisitResult;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.SimpleFileVisitor;
import java.nio.file.attribute.BasicFileAttributes;
import java.util.Set;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;

/**
 * Local-disk implementation of {@link WorkspacePurger}. Active by default (and whenever {@code
 * erd.storage.type=local}); the shared RWX volume makes {@code {workspaceDir}/{userId}/sessions/
 * {sessionId}} directly reachable from the backend.
 *
 * <p>Callers MUST validate that {@code userId}/{@code sessionId} are each a single, non-empty,
 * non-dot path segment before invoking either method here -- this class re-checks that the resolved
 * path lands inside the workspace root (belt-and-suspenders), but does not itself reject malformed
 * identifiers component-by-component.
 */
@Component
@RequiredArgsConstructor
@Slf4j
@ConditionalOnProperty(
    prefix = "erd.storage",
    name = "type",
    havingValue = "local",
    matchIfMissing = true)
public class LocalWorkspacePurger implements WorkspacePurger {

  private final StorageProperties properties;

  @Override
  public boolean sessionExists(String userId, String sessionId) {
    Path workspaceRoot = Paths.get(properties.workspaceDir()).toAbsolutePath().normalize();
    Path realWorkspaceRoot;
    try {
      realWorkspaceRoot = workspaceRoot.toRealPath();
    } catch (IOException exception) {
      log.info(
          "Workspace root {} cannot be resolved ({}), nothing to purge",
          workspaceRoot,
          exception.getMessage());
      return false;
    }
    Path sessionDir = sessionDir(workspaceRoot, userId, sessionId);
    // Belt-and-suspenders: confirms the resolved path still lands inside the workspace root.
    if (!sessionDir.startsWith(workspaceRoot)) {
      log.warn("Skipping workspace path outside root: {}", sessionDir);
      return false;
    }
    if (!Files.isDirectory(sessionDir)) {
      return false;
    }
    // startsWith is a lexical check and cannot see symlinks: a symlinked {userId} component
    // still reads as being under the root while resolving elsewhere. This mirrors the same check
    // in purgeSession so dry-run reporting (which only calls sessionExists, never purgeSession)
    // never counts a symlink-escaping session as "would purge".
    try {
      if (!sessionDir.toRealPath().startsWith(realWorkspaceRoot)) {
        log.warn("Skipping workspace path resolving outside root: {}", sessionDir);
        return false;
      }
    } catch (IOException exception) {
      log.warn(
          "Failed to resolve workspace dir={}: {}", sessionDir, exception.getMessage(), exception);
      return false;
    }
    return true;
  }

  @Override
  public boolean purgeSession(String userId, String sessionId) throws IOException {
    Path workspaceRoot = Paths.get(properties.workspaceDir()).toAbsolutePath().normalize();
    Path realWorkspaceRoot = workspaceRoot.toRealPath();
    Path sessionDir = sessionDir(workspaceRoot, userId, sessionId);
    // Belt-and-suspenders: confirms the resolved path still lands inside the workspace root.
    if (!sessionDir.startsWith(workspaceRoot)) {
      log.warn("Skipping workspace path outside root: {}", sessionDir);
      return false;
    }
    if (!Files.isDirectory(sessionDir)) {
      return false;
    }
    // startsWith is a lexical check and cannot see symlinks: a symlinked {userId} component
    // still reads as being under the root while resolving elsewhere, which would put the
    // recursive delete outside the volume. toRealPath resolves every component before deciding.
    try {
      if (!sessionDir.toRealPath().startsWith(realWorkspaceRoot)) {
        log.warn("Skipping workspace path resolving outside root: {}", sessionDir);
        return false;
      }
    } catch (IOException exception) {
      log.warn(
          "Failed to resolve workspace dir={}: {}", sessionDir, exception.getMessage(), exception);
      return false;
    }
    deleteRecursively(sessionDir);
    return true;
  }

  private Path sessionDir(Path workspaceRoot, String userId, String sessionId) {
    return workspaceRoot.resolve(userId).resolve("sessions").resolve(sessionId).normalize();
  }

  /**
   * Deletes the tree children-before-parents without following symbolic links: a symlinked entry is
   * unlinked, never followed into its target. Walking (rather than streaming) keeps failures as
   * checked {@link IOException}s, so an unreadable subtree stays a per-session failure instead of
   * an {@code UncheckedIOException} that unwinds the whole pass.
   */
  private void deleteRecursively(Path directory) throws IOException {
    Files.walkFileTree(
        directory,
        Set.of(),
        Integer.MAX_VALUE,
        new SimpleFileVisitor<>() {
          @Override
          public FileVisitResult visitFile(Path file, BasicFileAttributes attributes)
              throws IOException {
            Files.deleteIfExists(file);
            return FileVisitResult.CONTINUE;
          }

          @Override
          public FileVisitResult postVisitDirectory(Path visitedDirectory, IOException failure)
              throws IOException {
            if (failure != null) {
              throw failure;
            }
            Files.deleteIfExists(visitedDirectory);
            return FileVisitResult.CONTINUE;
          }
        });
  }
}
