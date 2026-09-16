package com.erd.cowork.agent.mcp;

import static org.assertj.core.api.Assertions.assertThat;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

class McpCallErrorWriterTest {

  private final McpCallErrorWriter writer = new McpCallErrorWriter(new ObjectMapper());

  @Test
  void write_codeAndMessage_returnsErrorEnvelopeJson() {
    String body = writer.write(McpErrorCode.RETRYABLE, "host returned HTTP 503; retry");

    assertThat(body)
        .isEqualTo(
            "{\"error\":{\"code\":\"RETRYABLE\",\"message\":\"host returned HTTP 503; retry\"}}");
  }

  @Test
  void write_messageWithQuotes_escapesInsteadOfBreakingJson() {
    String body = writer.write(McpErrorCode.TOOL_ERROR, "unknown fab \"FAB_Z\"");

    assertThat(body).contains("\\\"FAB_Z\\\"");
    assertThat(body).startsWith("{\"error\":{\"code\":\"TOOL_ERROR\"");
  }
}
