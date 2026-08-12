package com.erd.cowork.domain;

import java.time.Instant;
import lombok.EqualsAndHashCode;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import org.springframework.data.annotation.CreatedDate;
import org.springframework.data.annotation.Id;
import org.springframework.data.mongodb.core.mapping.Document;

@Document(collection = "uploaded_file")
@Getter
@Setter
@EqualsAndHashCode(of = "id")
@NoArgsConstructor
public class UploadedFile {

  @Id private String id;
  private String sessionId;
  private String name;
  private String alias;
  private String storageKey;
  private long sizeBytes;
  private String type;
  private Long rowCount;
  private String metadataJson;
  private boolean expired;

  @CreatedDate private Instant createdAt;
}
