package com.erd.cowork.repo;

import com.erd.cowork.domain.ChatMessage;
import java.util.List;
import org.springframework.data.mongodb.repository.MongoRepository;

public interface ChatMessageRepository extends MongoRepository<ChatMessage, String> {
  List<ChatMessage> findBySessionIdOrderByCreatedAtAsc(String sessionId);
}
