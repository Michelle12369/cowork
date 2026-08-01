package com.erd.cowork.web.dto;

import static org.assertj.core.api.Assertions.assertThat;

import com.erd.cowork.domain.ChatMessage;
import com.erd.cowork.domain.Sender;
import org.junit.jupiter.api.Test;
import org.mapstruct.factory.Mappers;

/**
 * Verifies {@code referencedTablesJson} survives the entity→DTO mapping (auto-mapped by MapStruct
 * on matching field names — see the generated {@code SessionMapperImpl} — but a rename on either
 * side would silently break it without this test).
 */
class SessionMapperTest {

  private final SessionMapper mapper = Mappers.getMapper(SessionMapper.class);

  @Test
  void toMessageDto_referencedTablesJsonPresent_copiedToDto() {
    ChatMessage chatMessage = new ChatMessage();
    chatMessage.setSender(Sender.AI);
    chatMessage.setText("here it is [[table:tbl_1]]");
    String referencedTablesJson = "[{\"tableId\":\"tbl_1\",\"intent\":\"row count\"}]";
    chatMessage.setReferencedTablesJson(referencedTablesJson);

    MessageDto dto = mapper.toMessageDto(chatMessage);

    assertThat(dto.referencedTablesJson()).isEqualTo(referencedTablesJson);
  }

  @Test
  void toMessageDto_referencedTablesJsonAbsent_dtoFieldIsNull() {
    ChatMessage chatMessage = new ChatMessage();
    chatMessage.setSender(Sender.AI);
    chatMessage.setText("純文字回答");

    MessageDto dto = mapper.toMessageDto(chatMessage);

    assertThat(dto.referencedTablesJson()).isNull();
  }
}
