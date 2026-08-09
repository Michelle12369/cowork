package com.erd.cowork.domain.id;

import com.fasterxml.uuid.Generators;
import com.fasterxml.uuid.impl.TimeBasedEpochGenerator;
import java.util.EnumSet;
import org.hibernate.engine.spi.SharedSessionContractImplementor;
import org.hibernate.generator.BeforeExecutionGenerator;
import org.hibernate.generator.EventType;
import org.hibernate.generator.EventTypeSets;

/** {@link UuidV7} 的 Hibernate 接縫：JUG 產生 v7 後轉 36 字元字串。 */
public class UuidV7Generator implements BeforeExecutionGenerator {

  private static final TimeBasedEpochGenerator UUID_V7_GENERATOR =
      Generators.timeBasedEpochGenerator();

  @Override
  public Object generate(
      SharedSessionContractImplementor session,
      Object owner,
      Object currentValue,
      EventType eventType) {
    return UUID_V7_GENERATOR.generate().toString();
  }

  @Override
  public EnumSet<EventType> getEventTypes() {
    return EventTypeSets.INSERT_ONLY;
  }
}
