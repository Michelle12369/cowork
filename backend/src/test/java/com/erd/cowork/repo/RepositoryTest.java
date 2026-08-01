package com.erd.cowork.repo;

import static org.assertj.core.api.Assertions.assertThat;

import com.erd.cowork.config.PersistenceConfig;
import com.erd.cowork.domain.ChatMessage;
import com.erd.cowork.domain.ChatSession;
import com.erd.cowork.domain.Sender;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.jdbc.AutoConfigureTestDatabase;
import org.springframework.boot.test.autoconfigure.orm.jpa.DataJpaTest;
import org.springframework.context.annotation.Import;

// PersistenceConfig import is required: @DataJpaTest does not scan standalone @Configuration
// classes, so without it @EnableJpaAuditing is inactive and created_at/updated_at stay null,
// failing this class's timestamp assertions and NOT NULL constraints when run standalone.
@DataJpaTest
@AutoConfigureTestDatabase(replace = AutoConfigureTestDatabase.Replace.NONE)
@Import(PersistenceConfig.class)
class RepositoryTest {

  @Autowired ChatSessionRepository sessions;
  @Autowired ChatMessageRepository messages;

  @Test
  void saveSession_newEntity_persistsWithAssignedIdAndTimestamps() {
    ChatSession session = new ChatSession();
    String assignedId = UUID.randomUUID().toString();
    session.setId(assignedId);
    session.setTitle("New analysis");
    session.setUserId("u1");
    ChatSession saved = sessions.save(session);
    assertThat(saved.getId()).isEqualTo(assignedId);
    assertThat(saved.getCreatedAt()).isNotNull();
    assertThat(saved.getUpdatedAt()).isNotNull();
  }

  @Test
  void findBySessionId_multipleMessages_orderedByCreatedAtAsc() throws InterruptedException {
    ChatSession session = new ChatSession();
    session.setId(UUID.randomUUID().toString());
    session.setTitle("t");
    session.setUserId("u1");
    session = sessions.save(session);

    ChatMessage message1 = new ChatMessage();
    message1.setSessionId(session.getId());
    message1.setSender(Sender.USER);
    message1.setText("q1");
    messages.save(message1);

    Thread.sleep(2);

    ChatMessage message2 = new ChatMessage();
    message2.setSessionId(session.getId());
    message2.setSender(Sender.AI);
    message2.setText("a1");
    messages.save(message2);

    assertThat(messages.findBySessionIdOrderByCreatedAtAsc(session.getId()))
        .extracting(ChatMessage::getText)
        .containsExactly("q1", "a1");
  }
}
