package com.erd.cowork.web;

import com.erd.cowork.logging.LogAnnotation;
import com.erd.cowork.service.FileService;
import com.erd.cowork.web.dto.FileDto;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.responses.ApiResponse;
import io.swagger.v3.oas.annotations.tags.Tag;
import java.util.List;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.HttpStatus;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

@Slf4j
@RestController
@RequestMapping("/api/sessions/{sessionId}/files")
@RequiredArgsConstructor
@Validated
@Tag(name = "Files", description = "File upload and management per chat session")
@LogAnnotation
public class FileController {

  private final FileService fileService;

  @PostMapping(consumes = "multipart/form-data")
  @ResponseStatus(HttpStatus.CREATED)
  @Operation(summary = "Upload one or more files to a session")
  @ApiResponse(responseCode = "201", description = "Files uploaded successfully")
  @ApiResponse(responseCode = "400", description = "Upload limit exceeded or unsupported file type")
  @ApiResponse(responseCode = "404", description = "Session not found")
  public List<FileDto> upload(
      @PathVariable String sessionId, @RequestParam("files") List<MultipartFile> files) {
    log.info("upload session={} fileCount={}", sessionId, files.size());
    return fileService.upload(sessionId, files);
  }

  @DeleteMapping("/{fileId}")
  @ResponseStatus(HttpStatus.NO_CONTENT)
  @Operation(summary = "Delete a file from a session")
  @ApiResponse(responseCode = "204", description = "File deleted")
  @ApiResponse(responseCode = "404", description = "Session or file not found")
  public void delete(@PathVariable String sessionId, @PathVariable String fileId) {
    fileService.delete(sessionId, fileId);
  }
}
