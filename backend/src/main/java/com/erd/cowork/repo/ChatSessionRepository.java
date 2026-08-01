package com.erd.cowork.repo;

import com.erd.cowork.domain.ChatSession;
import java.time.Instant;
import java.util.List;
import org.springframework.data.jpa.repository.JpaRepository;

public interface ChatSessionRepository extends JpaRepository<ChatSession, String> {
  List<ChatSession> findByUserIdOrderByUpdatedAtDesc(String userId);

  List<ChatSession> findByUpdatedAtBefore(Instant cutoff);
}
