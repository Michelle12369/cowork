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
   * API connector ids locked for this session. {@code null} means undecided — the session may still
   * go either files or connector mode. Once set (first message with a non-empty request selection,
   * verified to have no active files), it is authoritative for the lifetime of the session: later
   * requests' connector values are ignored in favor of this stored selection, and csv/xlsx upload
   * is rejected outright ({@code FileService#upload}).
   */
  private List<String> selectedConnectors;

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
