package com.erd.cowork.repo;

import com.erd.cowork.domain.ChatMessage;
import java.util.List;
import org.springframework.data.jpa.repository.JpaRepository;

public interface ChatMessageRepository extends JpaRepository<ChatMessage, String> {
  List<ChatMessage> findBySessionIdOrderByCreatedAtAsc(String sessionId);
}
