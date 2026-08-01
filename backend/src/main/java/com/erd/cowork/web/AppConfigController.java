package com.erd.cowork.web;

import com.erd.cowork.config.StorageProperties;
import com.erd.cowork.config.UploadProperties;
import com.erd.cowork.web.dto.AppConfigDto;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.responses.ApiResponse;
import io.swagger.v3.oas.annotations.tags.Tag;
import java.util.Map;
import lombok.RequiredArgsConstructor;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/config")
@RequiredArgsConstructor
@Validated
@Tag(name = "Config", description = "Public application configuration")
public class AppConfigController {

  private final StorageProperties storageProperties;
  private final UploadProperties uploadProperties;

  @GetMapping
  @Operation(summary = "Get public application configuration")
  @ApiResponse(responseCode = "200", description = "Configuration returned")
  public AppConfigDto getConfig() {
    Map<String, Long> singleFileLimits =
        Map.of("csv", uploadProperties.maxCsvBytes(), "xlsx", uploadProperties.maxXlsxBytes());
    return new AppConfigDto(
        (int) storageProperties.retention().uploads().toDays(),
        uploadProperties.maxFiles(),
        uploadProperties.maxSessionBytes(),
        singleFileLimits);
  }
}
