package com.erd.cowork.support;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatCode;

import com.erd.cowork.config.PersistenceConfig;
import com.erd.cowork.domain.ChatSession;
import com.erd.cowork.repo.ChatSessionRepository;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.data.mongo.DataMongoTest;
import org.springframework.context.annotation.Import;

/**
 * Covers the {@code AfterSaveCallback<ChatSession>} added to {@code PersistenceConfig}: without it,
 * a freshly-constructed {@code ChatSession} stays {@code isNew=true} after its first {@code
 * save()}, so a second {@code save()} on the same in-memory instance (e.g. {@code
 * AgentOrchestrator.prepare} touching {@code updatedAt} right after {@code
 * SessionGuard.loadOrCreateOwnedAs} creates the row) re-attempts an insert against an
 * already-existing {@code _id} and throws {@code DuplicateKeyException}.
 *
 * <p>{@code @DataMongoTest} does not component-scan arbitrary {@code @Configuration} classes, so
 * {@code PersistenceConfig} (and its entity callback beans) must be imported explicitly.
 */
@DataMongoTest
@Import(PersistenceConfig.class)
class ChatSessionRepeatedSaveTest {

  @Autowired ChatSessionRepository sessions;

  @Test
  void save_sameNewSessionInstanceTwice_secondSaveUpdatesInsteadOfInserting() {
    String sessionId = "33333333-3333-3333-3333-333333333333";
    ChatSession session = new ChatSession();
    session.setId(sessionId);
    session.setUserId("user-a");
    session.setTitle("first title");

    assertThatCode(() -> sessions.save(session)).doesNotThrowAnyException();

    session.setTitle("updated title");
    assertThatCode(() -> sessions.save(session)).doesNotThrowAnyException();

    assertThat(sessions.count()).isEqualTo(1);
    assertThat(sessions.findById(sessionId))
        .get()
        .extracting(ChatSession::getTitle)
        .isEqualTo("updated title");
  }
}
