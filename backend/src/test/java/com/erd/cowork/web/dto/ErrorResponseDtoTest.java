package com.erd.cowork.web.dto;

import static org.assertj.core.api.Assertions.assertThat;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

/**
 * Verifies that {@link ErrorResponseDto} serialises to the exact JSON shape the frontend expects:
 * {@code {"code":"…","message":"…"}}. This is the wire-format contract — if field names ever drift,
 * this test will catch it before the front end breaks.
 */
class ErrorResponseDtoTest {

  private final ObjectMapper objectMapper = new ObjectMapper();

  @Test
  void serialize_notFoundCode_producesCodeAndMessageFields() throws Exception {
    ErrorResponseDto dto = new ErrorResponseDto("NOT_FOUND", "resource not found");
    String json = objectMapper.writeValueAsString(dto);
    assertThat(json)
        .contains("\"code\":\"NOT_FOUND\"")
        .contains("\"message\":\"resource not found\"");
  }

  @Test
  void serialize_uploadLimitCode_codeFieldMatchesEnumName() throws Exception {
    ErrorResponseDto dto = new ErrorResponseDto("UPLOAD_LIMIT", "CSV/TXT file exceeds size limit.");
    String json = objectMapper.writeValueAsString(dto);
    assertThat(json)
        .contains("\"code\":\"UPLOAD_LIMIT\"")
        .contains("\"message\":\"CSV/TXT file exceeds size limit.\"");
  }
}
