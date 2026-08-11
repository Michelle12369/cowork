package com.erd.cowork.repo;

import com.erd.cowork.domain.ChatSession;
import java.time.Instant;
import java.util.List;
import org.springframework.data.mongodb.repository.MongoRepository;

public interface ChatSessionRepository extends MongoRepository<ChatSession, String> {
  List<ChatSession> findByUserIdOrderByUpdatedAtDesc(String userId);

  List<ChatSession> findByUpdatedAtBefore(Instant cutoff);
}
