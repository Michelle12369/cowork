package com.erd.cowork.domain;

import com.erd.cowork.domain.id.UuidV7;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EntityListeners;
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
@Table(name = "uploaded_file")
@EntityListeners(AuditingEntityListener.class)
@Getter
@Setter
@EqualsAndHashCode(of = "id")
@NoArgsConstructor
public class UploadedFile {

  @Id
  @UuidV7
  @Column(length = 36)
  private String id;

  @Column(nullable = false, length = 36)
  private String sessionId;

  @Column(nullable = false, length = 500)
  private String name;

  @Column(nullable = false, length = 100)
  private String alias;

  @Column(nullable = false, length = 500)
  private String storageKey;

  @Column(nullable = false)
  private long sizeBytes;

  @Column(nullable = false, length = 20)
  private String type;

  @Column(name = "row_count")
  private Long rowCount;

  @Lob private String metadataJson;

  @Column(nullable = false)
  private boolean expired;

  @CreatedDate
  @Column(nullable = false, updatable = false)
  private Instant createdAt;
}
