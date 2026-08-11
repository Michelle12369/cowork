package com.erd.cowork.domain;

import java.time.Instant;
import lombok.EqualsAndHashCode;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import org.springframework.data.annotation.CreatedDate;
import org.springframework.data.annotation.Id;
import org.springframework.data.annotation.LastModifiedDate;
import org.springframework.data.annotation.Transient;
import org.springframework.data.domain.Persistable;
import org.springframework.data.mongodb.core.mapping.Document;

@Document(collection = "chat_session")
@Getter
@Setter
@EqualsAndHashCode(of = "id")
@NoArgsConstructor
public class ChatSession implements Persistable<String> {

  @Id private String id;
  private String title;
  private String userId;

  @CreatedDate private Instant createdAt;
  @LastModifiedDate private Instant updatedAt;

  @Transient private boolean isNew = true;

  @Override
  public boolean isNew() {
    return isNew;
  }

  public void markNotNew() {
    this.isNew = false;
  }
}
