package com.erd.cowork.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EntityListeners;
import jakarta.persistence.Id;
import jakarta.persistence.PostLoad;
import jakarta.persistence.PostPersist;
import jakarta.persistence.Table;
import jakarta.persistence.Transient;
import java.time.Instant;
import lombok.EqualsAndHashCode;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import org.springframework.data.annotation.CreatedDate;
import org.springframework.data.annotation.LastModifiedDate;
import org.springframework.data.domain.Persistable;
import org.springframework.data.jpa.domain.support.AuditingEntityListener;

/**
 * Chat session entity.
 *
 * <p><strong>Uses a client-specified id, not {@code @UuidGenerator}:</strong> sessions adopt
 * client-generated UUIDs ({@code crypto.randomUUID()} from the frontend), and the assigned id must
 * survive the JPA {@code save()} call. {@code @UuidGenerator} combined with Spring Data's {@code
 * merge()} path would re-generate the id on first insert, corrupting the client-assigned value.
 * Implementing {@link Persistable} forces {@code persist()} for new instances instead, preserving
 * the assigned id. All creation paths must explicitly assign a UUID before calling {@code save()}.
 */
@Entity
@Table(name = "chat_session")
@EntityListeners(AuditingEntityListener.class)
@Getter
@Setter
@EqualsAndHashCode(of = "id")
@NoArgsConstructor
public class ChatSession implements Persistable<String> {

  @Id
  @Column(length = 36)
  private String id;

  @Column(nullable = false, length = 200)
  private String title;

  @Column(nullable = false, length = 100)
  private String userId;

  @CreatedDate
  @Column(nullable = false, updatable = false)
  private Instant createdAt;

  @LastModifiedDate
  @Column(nullable = false)
  private Instant updatedAt;

  /**
   * Drives {@link Persistable#isNew()} so that Spring Data calls {@code persist()} (not {@code
   * merge()}) for instances created with {@code new}. {@link PostLoad} and {@link PostPersist} flip
   * this to {@code false} so that subsequent {@code save()} calls use {@code merge()} for updates.
   */
  @Transient private boolean isNew = true;

  @PostLoad
  @PostPersist
  void markNotNew() {
    this.isNew = false;
  }

  @Override
  public boolean isNew() {
    return isNew;
  }
}
