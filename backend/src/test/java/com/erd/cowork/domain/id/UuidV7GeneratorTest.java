package com.erd.cowork.domain.id;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.ArrayList;
import java.util.List;
import org.hibernate.generator.EventType;
import org.junit.jupiter.api.Test;

class UuidV7GeneratorTest {

  private final UuidV7Generator generator = new UuidV7Generator();

  private String generate() {
    return (String) generator.generate(null, null, null, EventType.INSERT);
  }

  @Test
  void generate_returnsCanonical36CharUuidVersion7() {
    String uuid = generate();
    assertThat(uuid).hasSize(36);
    assertThat(uuid.charAt(14)).isEqualTo('7'); // version nibble
    assertThat("89ab").contains(String.valueOf(uuid.charAt(19))); // variant nibble
  }

  @Test
  void generate_consecutiveCalls_lexicographicallyIncreasingAndUnique() {
    List<String> generated = new ArrayList<>();
    for (int index = 0; index < 1000; index++) {
      generated.add(generate());
    }
    List<String> sorted = new ArrayList<>(generated);
    sorted.sort(String::compareTo);
    assertThat(generated).isEqualTo(sorted); // JUG timeBasedEpochGenerator 同毫秒單調
    assertThat(generated).doesNotHaveDuplicates();
  }
}
