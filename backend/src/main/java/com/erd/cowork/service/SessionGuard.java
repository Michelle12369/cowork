package com.erd.cowork.service;

import com.erd.cowork.context.CoworkContextHolder;
import com.erd.cowork.domain.ChatSession;
import com.erd.cowork.exception.NotFoundException;
import com.erd.cowork.logging.LogAnnotation;
import com.erd.cowork.repo.ChatSessionRepository;
import java.util.Optional;
import java.util.regex.Pattern;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.stereotype.Component;

@Component
@RequiredArgsConstructor
@Slf4j
@LogAnnotation
public class SessionGuard {

  /**
   * Default title for sessions created via client-generated-id upsert, matching the title
   * previously assigned by {@code SessionService.create()}.
   */
  public static final String DEFAULT_SESSION_TITLE = "New analysis";

  /**
   * Canonical lowercase UUID format emitted by {@code crypto.randomUUID()}. Uppercase, braced, or
   * otherwise non-canonical ids are rejected — they can never be valid session addresses.
   */
  private static final Pattern UUID_PATTERN =
      Pattern.compile("^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$");

  private final ChatSessionRepository sessions;

  /** Loads a session owned by the current user; missing or foreign sessions both surface as 404. */
  public ChatSession loadOwned(String sessionId) {
    return loadOwnedAs(CoworkContextHolder.userId(), sessionId);
  }

  /**
   * Loads a session owned by the given userId; missing or foreign sessions both surface as 404.
   * Internal helper shared by {@link #loadOwned} and the upsert race fallback. Async paths (where
   * request-thread-only context reads are unsafe) use {@link #loadOrCreateOwnedAs}.
   */
  private ChatSession loadOwnedAs(String userId, String sessionId) {
    ChatSession session =
        sessions
            .findById(sessionId)
            .orElseThrow(() -> new NotFoundException("session not found: " + sessionId));
    // userId null 一律視為未擁有(false)而非讓 .equals 冒 NPE 風險——caller 恆為 request-scope
    // context 讀出值,理論上不會是 null,但守門邏輯本身不該預設非 null。
    if (userId == null || !session.getUserId().equals(userId)) {
      throw new NotFoundException("session not found: " + sessionId);
    }
    return session;
  }

  /**
   * Loads a session owned by the current user, creating it if it does not exist. The session id
   * must be a canonical lowercase UUID (the format emitted by {@code crypto.randomUUID()}).
   *
   * <p>Use this overload from request-thread paths (e.g. file upload) where {@link
   * CoworkContextHolder} is safe to read.
   *
   * @throws NotFoundException if {@code sessionId} is not a canonical UUID, or if the session
   *     exists and is owned by a different user.
   */
  public ChatSession loadOrCreateOwned(String sessionId) {
    return loadOrCreateOwnedAs(CoworkContextHolder.userId(), sessionId);
  }

  /**
   * Loads a session owned by the given userId, creating it if it does not exist. Use this overload
   * from async paths where {@link CoworkContextHolder} is not safe to access (the underlying
   * ThreadLocal does not cross threads).
   *
   * <p>Creation semantics:
   *
   * <ol>
   *   <li>The {@code sessionId} must be a canonical lowercase UUID; non-canonical ids are rejected
   *       immediately with 404 (no row is created).
   *   <li>If the session already exists and is owned by {@code userId}, it is returned unchanged.
   *   <li>If the session already exists and is owned by a different user, 404 is thrown.
   *   <li>If the session does not exist, it is inserted with the client-supplied id, {@code
   *       DEFAULT_SESSION_TITLE}, and {@code userId}.
   *   <li>A {@link DuplicateKeyException} (Mongo E11000) from a concurrent first-touch insert is
   *       caught and resolved by re-loading via the normal owned-load path.
   * </ol>
   *
   * @throws NotFoundException if {@code sessionId} is non-canonical, or if the session is owned by
   *     a different user.
   * @implNote Intentionally NOT {@code @Transactional}: this repo's Mongo migration forbids
   *     {@code @Transactional}/{@code MongoTransactionManager} on this path; the caught {@link
   *     DuplicateKeyException} is resolved by a plain fallback read, no transaction involved.
   */
  public ChatSession loadOrCreateOwnedAs(String userId, String sessionId) {
    if (!UUID_PATTERN.matcher(sessionId).matches()) {
      log.warn("rejected non-canonical sessionId format: {}", sessionId);
      throw new NotFoundException("session not found: " + sessionId);
    }
    Optional<ChatSession> existingSession = sessions.findById(sessionId);
    if (existingSession.isPresent()) {
      ChatSession session = existingSession.get();
      if (!session.getUserId().equals(userId)) {
        throw new NotFoundException("session not found: " + sessionId);
      }
      return session;
    }
    ChatSession newSession = new ChatSession();
    newSession.setId(sessionId);
    newSession.setUserId(userId);
    newSession.setTitle(DEFAULT_SESSION_TITLE);
    try {
      sessions.save(newSession);
      log.info("session created via upsert sessionId={}", sessionId);
      return newSession;
    } catch (DuplicateKeyException exception) {
      log.warn("upsert race on sessionId={}, falling back to load", sessionId);
      return loadOwnedAs(userId, sessionId);
    }
  }
}
