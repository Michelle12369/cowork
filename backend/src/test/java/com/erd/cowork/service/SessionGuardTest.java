package com.erd.cowork.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.erd.cowork.config.PersistenceConfig;
import com.erd.cowork.context.CoworkContext;
import com.erd.cowork.context.CoworkContextHolder;
import com.erd.cowork.context.CurrentContext;
import com.erd.cowork.domain.ChatSession;
import com.erd.cowork.exception.NotFoundException;
import com.erd.cowork.repo.ChatSessionRepository;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.jdbc.AutoConfigureTestDatabase;
import org.springframework.boot.test.autoconfigure.orm.jpa.DataJpaTest;
import org.springframework.context.annotation.Import;

// PersistenceConfig import is required: @DataJpaTest does not scan standalone @Configuration
// classes, so without it @EnableJpaAuditing is inactive and created_at/updated_at stay null —
// any test that flushes an insert would then fail on the NOT NULL constraints.
@DataJpaTest
@AutoConfigureTestDatabase(replace = AutoConfigureTestDatabase.Replace.NONE)
@Import(PersistenceConfig.class)
class SessionGuardTest {

  @Autowired ChatSessionRepository sessions;

  SessionGuard sessionGuard;

  @BeforeEach
  void setUp() {
    CoworkContextHolder.set(CoworkContext.external("user"));
    sessionGuard = new SessionGuard(sessions, new CurrentContext());
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

    // Force the INSERT to hit the database — without this, findById is served from the
    // persistence context and the test would pass even if the row were un-insertable.
    sessions.flush();

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
