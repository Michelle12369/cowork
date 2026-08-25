package com.erd.cowork.support;

import static org.assertj.core.api.Assertions.assertThat;

import com.erd.cowork.domain.ChatSession;
import com.erd.cowork.repo.ChatSessionRepository;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.data.mongo.DataMongoTest;

@DataMongoTest
class MongoSmokeTest {

  @Autowired ChatSessionRepository sessions;

  @Test
  void save_newSessionWithClientId_roundTripsById() {
    ChatSession session = new ChatSession();
    String sessionId = java.util.UUID.randomUUID().toString();
    session.setId(sessionId);
    session.setUserId("user-a");
    session.setTitle("t");
    sessions.save(session);

    assertThat(sessions.findById(sessionId))
        .get()
        .extracting(ChatSession::getUserId)
        .isEqualTo("user-a");
  }
}
