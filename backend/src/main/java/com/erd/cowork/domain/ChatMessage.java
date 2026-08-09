package com.erd.cowork.domain;

import com.erd.cowork.domain.id.UuidV7;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EntityListeners;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.Id;
import jakarta.persistence.Lob;
import jakarta.persistence.Table;
import java.time.Instant;
import lombok.EqualsAndHashCode;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import org.springframework.data.annotation.CreatedDate;
import org.springframework.data.jpa.domain.support.AuditingEntityListener;

@Entity
@Table(name = "chat_message")
@EntityListeners(AuditingEntityListener.class)
@Getter
@Setter
@EqualsAndHashCode(of = "id")
@NoArgsConstructor
public class ChatMessage {

  @Id
  @UuidV7
  @Column(length = 36)
  private String id;

  @Column(nullable = false, length = 36)
  private String sessionId;

  @Enumerated(EnumType.STRING)
  @Column(nullable = false, length = 10)
  private Sender sender;

  @Lob private String text;

  @Lob private String stepsJson;

  @Column(length = 36)
  private String artifactId;

  @Lob private String questionsJson;

  @CreatedDate
  @Column(nullable = false, updatable = false)
  private Instant createdAt;
}
