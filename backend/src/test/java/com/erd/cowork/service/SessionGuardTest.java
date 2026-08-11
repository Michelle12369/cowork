package com.erd.cowork.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.erd.cowork.config.PersistenceConfig;
import com.erd.cowork.context.CoworkContext;
import com.erd.cowork.context.CoworkContextHolder;
import com.erd.cowork.domain.ChatSession;
import com.erd.cowork.exception.NotFoundException;
import com.erd.cowork.repo.ChatSessionRepository;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.data.mongo.DataMongoTest;
import org.springframework.context.annotation.Import;

// PersistenceConfig import is required: @DataMongoTest does not scan standalone @Configuration
// classes, so without it @EnableMongoAuditing is inactive and createdAt/updatedAt stay null.
@DataMongoTest
@Import(PersistenceConfig.class)
class SessionGuardTest {

  @Autowired ChatSessionRepository sessions;

  SessionGuard sessionGuard;

  @BeforeEach
  void setUp() {
    CoworkContextHolder.set(CoworkContext.external("user"));
    sessionGuard = new SessionGuard(sessions);
  }

  @AfterEach
  void tearDown() {
    CoworkContextHolder.clear();
  }

  // ── Pinning test (§2): must run first to verify @UuidGenerator honours assigned ids ──────────

  @Test
  void loadOrCreateOwnedAs_missingSession_createsWithAssignedId() {
    String assignedId = "a1b2c3d4-e5f6-7890-abcd-ef1234567890";

    ChatSession result = sessionGuard.loadOrCreateOwnedAs("owner-u1", assignedId);

    assertThat(result.getId()).isEqualTo(assignedId);
    assertThat(sessions.findById(assignedId)).isPresent();
    assertThat(sessions.findById(assignedId).get().getTitle())
        .isEqualTo(SessionGuard.DEFAULT_SESSION_TITLE);
    assertThat(sessions.findById(assignedId).get().getUserId()).isEqualTo("owner-u1");
  }

  // ── Behavioural tests ────────────────────────────────────────────────────────────────────────

  @Test
  void loadOrCreateOwnedAs_existingOwnedSession_returnsIt() {
    String sessionId = "b1b2c3d4-e5f6-7890-abcd-ef1234567890";
    ChatSession existing = new ChatSession();
    existing.setId(sessionId);
    existing.setTitle("Original title");
    existing.setUserId("owner-u2");
    sessions.save(existing);

    ChatSession result = sessionGuard.loadOrCreateOwnedAs("owner-u2", sessionId);

    assertThat(result.getId()).isEqualTo(sessionId);
    assertThat(result.getTitle()).isEqualTo("Original title");
  }

  @Test
  void loadOrCreateOwnedAs_idAlreadyExistsSameUser_returnsExistingNotThrows() {
    String sessionId = "22222222-2222-2222-2222-222222222222";
    ChatSession existing = new ChatSession();
    existing.setId(sessionId);
    existing.setUserId("user-a");
    existing.setTitle("t");
    sessions.save(existing);

    ChatSession result = sessionGuard.loadOrCreateOwnedAs("user-a", sessionId);
    assertThat(result.getId()).isEqualTo(sessionId);
  }

  @Test
  void loadOrCreateOwnedAs_saveThrowsDuplicateKeyException_fallsBackToLoad() {
    ChatSessionRepository mockRepo = org.mockito.Mockito.mock(ChatSessionRepository.class);
    SessionGuard guard = new SessionGuard(mockRepo);
    String sessionId = "44444444-4444-4444-4444-444444444444";
    ChatSession existing = new ChatSession();
    existing.setId(sessionId);
    existing.setUserId("user-a");

    org.mockito.Mockito.when(mockRepo.findById(sessionId))
        .thenReturn(java.util.Optional.empty(), java.util.Optional.of(existing));
    org.mockito.Mockito.when(mockRepo.save(org.mockito.ArgumentMatchers.any()))
        .thenThrow(new org.springframework.dao.DuplicateKeyException("E11000"));

    ChatSession result = guard.loadOrCreateOwnedAs("user-a", sessionId);

    assertThat(result.getId()).isEqualTo(sessionId);
  }

  @Test
  void loadOrCreateOwnedAs_foreignSession_throws404() {
    String sessionId = "c1b2c3d4-e5f6-7890-abcd-ef1234567890";
    ChatSession foreign = new ChatSession();
    foreign.setId(sessionId);
    foreign.setTitle("Foreign");
    foreign.setUserId("other-user");
    sessions.save(foreign);

    assertThatThrownBy(() -> sessionGuard.loadOrCreateOwnedAs("requester-u3", sessionId))
        .isInstanceOf(NotFoundException.class)
        .hasMessageContaining(sessionId);
  }

  @Test
  void loadOrCreateOwnedAs_malformedId_throws404AndCreatesNothing() {
    long countBefore = sessions.count();

    // plain short string
    assertThatThrownBy(() -> sessionGuard.loadOrCreateOwnedAs("any-user", "abc"))
        .isInstanceOf(NotFoundException.class);

    // uppercase UUID (not canonical)
    assertThatThrownBy(
            () ->
                sessionGuard.loadOrCreateOwnedAs(
                    "any-user", "A1B2C3D4-E5F6-7890-ABCD-EF1234567890"))
        .isInstanceOf(NotFoundException.class);

    // braces-wrapped UUID
    assertThatThrownBy(
            () ->
                sessionGuard.loadOrCreateOwnedAs(
                    "any-user", "{a1b2c3d4-e5f6-7890-abcd-ef1234567890}"))
        .isInstanceOf(NotFoundException.class);

    assertThat(sessions.count()).isEqualTo(countBefore);
  }
}
