package com.erd.cowork.web;

import com.erd.cowork.logging.LogAnnotation;
import com.erd.cowork.service.ConnectorCatalogService;
import com.erd.cowork.web.dto.ConnectorInfoDto;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.responses.ApiResponse;
import io.swagger.v3.oas.annotations.tags.Tag;
import java.util.List;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@Slf4j
@RestController
@RequestMapping("/api/connectors")
@RequiredArgsConstructor
@Validated
@Tag(name = "Connectors", description = "API connector directory (MCP datasource, Phase 1)")
@LogAnnotation
public class ConnectorController {

  private final ConnectorCatalogService connectorCatalogService;

  @GetMapping
  @Operation(
      summary = "List available API connectors",
      description =
          "Reads the Mongo-backed connector catalog. Returns an empty list when no connectors are"
              + " configured.")
  @ApiResponse(responseCode = "200", description = "Connector list returned (possibly empty)")
  public List<ConnectorInfoDto> list() {
    return connectorCatalogService.listCatalog();
  }
}
