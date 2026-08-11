package com.erd.cowork.domain;

import java.time.Instant;
import lombok.EqualsAndHashCode;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import org.springframework.data.annotation.CreatedDate;
import org.springframework.data.annotation.Id;
import org.springframework.data.mongodb.core.mapping.Document;

@Document(collection = "chat_message")
@Getter
@Setter
@EqualsAndHashCode(of = "id")
@NoArgsConstructor
public class ChatMessage {

  @Id private String id;
  private String sessionId;
  private Sender sender;
  private String text;
  private String stepsJson;
  private String artifactId;
  private String questionsJson;

  @CreatedDate private Instant createdAt;
}
