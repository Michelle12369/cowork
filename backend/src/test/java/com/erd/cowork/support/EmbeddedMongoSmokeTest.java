package com.erd.cowork.support;

import static org.assertj.core.api.Assertions.assertThat;

import com.erd.cowork.domain.ChatSession;
import com.erd.cowork.repo.ChatSessionRepository;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.data.mongo.DataMongoTest;

@DataMongoTest
class EmbeddedMongoSmokeTest {

  @Autowired ChatSessionRepository sessions;

  @Test
  void save_newSessionWithClientId_roundTripsById() {
    ChatSession session = new ChatSession();
    session.setId("11111111-1111-1111-1111-111111111111");
    session.setUserId("user-a");
    session.setTitle("t");
    sessions.save(session);

    assertThat(sessions.findById("11111111-1111-1111-1111-111111111111"))
        .get()
        .extracting(ChatSession::getUserId)
        .isEqualTo("user-a");
  }
}
