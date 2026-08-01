package com.erd.cowork.web;

import com.erd.cowork.service.SampleDatasetService;
import com.erd.cowork.web.dto.FileDto;
import com.erd.cowork.web.dto.SampleDatasetDto;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.responses.ApiResponse;
import io.swagger.v3.oas.annotations.tags.Tag;
import java.util.List;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.HttpStatus;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

@Slf4j
@RestController
@RequiredArgsConstructor
@Validated
@Tag(
    name = "Sample Datasets",
    description = "Built-in demo datasets that can be loaded into a session")
public class SampleDatasetController {

  private final SampleDatasetService sampleDatasetService;

  @GetMapping("/api/samples")
  @Operation(summary = "List built-in sample datasets")
  @ApiResponse(responseCode = "200", description = "Sample datasets returned")
  public List<SampleDatasetDto> list() {
    return sampleDatasetService.listDatasets();
  }

  @PostMapping("/api/sessions/{sessionId}/files/samples/{sampleName}")
  @ResponseStatus(HttpStatus.CREATED)
  @Operation(summary = "Load a built-in sample dataset's files into a session")
  @ApiResponse(responseCode = "201", description = "Sample dataset files loaded")
  @ApiResponse(responseCode = "400", description = "Upload limit exceeded")
  @ApiResponse(responseCode = "404", description = "Sample dataset or session not found")
  public List<FileDto> load(@PathVariable String sessionId, @PathVariable String sampleName) {
    log.info("load sample dataset session={} sample={}", sessionId, sampleName);
    return sampleDatasetService.load(sessionId, sampleName);
  }
}
