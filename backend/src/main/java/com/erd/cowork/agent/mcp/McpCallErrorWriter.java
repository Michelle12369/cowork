package com.erd.cowork.agent.mcp;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;

/** Serialises an mcp() failure into the JSON string that goes back to the page unchanged. */
@Component
@RequiredArgsConstructor
public class McpCallErrorWriter {

  private final ObjectMapper objectMapper;

  public String write(McpErrorCode code, String message) {
    try {
      return objectMapper.writeValueAsString(
          new McpCallFailure(new McpCallError(code.name(), message)));
    } catch (JsonProcessingException serializationError) {
      throw new IllegalStateException("cannot serialise mcp() failure " + code, serializationError);
    }
  }
}
