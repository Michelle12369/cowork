package com.erd.cowork.domain.id;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;
import org.hibernate.annotations.IdGeneratorType;

/**
 * 時間有序 UUIDv7 字串 id：前 48 bits 為毫秒 timestamp，插入落點集中在 MariaDB clustered index 最右側熱 page。Hibernate 6.6
 * 的 {@code @UuidGenerator} 無 v7 才自訂此接縫；產生邏輯委給 JUG。
 */
@IdGeneratorType(UuidV7Generator.class)
@Retention(RetentionPolicy.RUNTIME)
@Target({ElementType.FIELD, ElementType.METHOD})
public @interface UuidV7 {}
