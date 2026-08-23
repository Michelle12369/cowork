package com.erd.cowork.domain;

import java.time.Instant;
import java.util.List;
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

  /**
   * User-selected connector group names, locked for the lifetime of this session (§11.6): {@code
   * null} means 未定案 (not yet decided — the next message captures whatever it sends, including an
   * empty list); once non-null (even empty, meaning "all groups"), the value is 定案 and every later
   * message's {@code selectedGroups} field is ignored in favor of this stored value. Changing data
   * sources requires starting a new session.
   */
  private List<String> selectedGroups;

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
