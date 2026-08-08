package com.erd.cowork.web;

import com.erd.cowork.logging.LogAnnotation;
import com.erd.cowork.service.SessionService;
import com.erd.cowork.web.dto.SessionDetailDto;
import com.erd.cowork.web.dto.SessionSummaryDto;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.responses.ApiResponse;
import io.swagger.v3.oas.annotations.tags.Tag;
import java.util.List;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@Slf4j
@RestController
@RequestMapping("/api/sessions")
@RequiredArgsConstructor
@Validated
@Tag(name = "Sessions", description = "Chat session management")
@LogAnnotation
public class SessionController {

  private final SessionService service;

  @GetMapping
  @Operation(summary = "List all chat sessions ordered by last updated")
  @ApiResponse(responseCode = "200", description = "Session list returned")
  public List<SessionSummaryDto> list() {
    return service.list();
  }

  @GetMapping("/{sessionId}")
  @Operation(summary = "Get session detail including messages and files")
  @ApiResponse(responseCode = "200", description = "Session detail returned")
  @ApiResponse(responseCode = "404", description = "Session not found")
  public SessionDetailDto get(@PathVariable String sessionId) {
    return service.get(sessionId);
  }
}
