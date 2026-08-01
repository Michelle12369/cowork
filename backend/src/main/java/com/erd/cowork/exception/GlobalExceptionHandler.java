package com.erd.cowork.exception;

import com.erd.cowork.web.dto.ErrorResponseDto;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.multipart.MaxUploadSizeExceededException;
import org.springframework.web.multipart.MultipartException;

@RestControllerAdvice
public class GlobalExceptionHandler {

  @ExceptionHandler(FilesExpiredException.class)
  public ResponseEntity<ErrorResponseDto> filesExpired(FilesExpiredException exception) {
    return ResponseEntity.status(HttpStatus.CONFLICT)
        .body(new ErrorResponseDto(ErrorCode.FILES_EXPIRED.name(), exception.getMessage()));
  }

  @ExceptionHandler(ConflictException.class)
  public ResponseEntity<ErrorResponseDto> conflict(ConflictException exception) {
    return ResponseEntity.status(HttpStatus.CONFLICT)
        .body(new ErrorResponseDto(ErrorCode.CONFLICT.name(), exception.getMessage()));
  }

  @ExceptionHandler(BrowserRepairUnsupportedException.class)
  public ResponseEntity<ErrorResponseDto> browserRepairUnsupported(
      BrowserRepairUnsupportedException exception) {
    return ResponseEntity.status(HttpStatus.CONFLICT)
        .body(
            new ErrorResponseDto(
                ErrorCode.BROWSER_REPAIR_UNSUPPORTED.name(), exception.getMessage()));
  }

  @ExceptionHandler(NotFoundException.class)
  public ResponseEntity<ErrorResponseDto> notFound(NotFoundException exception) {
    return ResponseEntity.status(HttpStatus.NOT_FOUND)
        .body(new ErrorResponseDto(ErrorCode.NOT_FOUND.name(), exception.getMessage()));
  }

  @ExceptionHandler(UploadLimitException.class)
  public ResponseEntity<ErrorResponseDto> uploadLimit(UploadLimitException exception) {
    return ResponseEntity.badRequest()
        .body(new ErrorResponseDto(exception.getCode(), exception.getMessage()));
  }

  @ExceptionHandler(ParseException.class)
  public ResponseEntity<ErrorResponseDto> parseError(ParseException exception) {
    return ResponseEntity.badRequest()
        .body(new ErrorResponseDto(ErrorCode.PARSE_ERROR.name(), exception.getMessage()));
  }

  /**
   * Servlet-level multipart failures (payload beyond the container limit, or a malformed multipart
   * body) surface before the request reaches the controller. Both map to a 400 so oversized uploads
   * never leak a 500. MaxUploadSizeExceededException is a MultipartException subclass; the single
   * handler covers both since the response is identical.
   */
  @ExceptionHandler({MaxUploadSizeExceededException.class, MultipartException.class})
  public ResponseEntity<ErrorResponseDto> uploadTooLarge(MultipartException exception) {
    return ResponseEntity.badRequest()
        .body(
            new ErrorResponseDto(ErrorCode.UPLOAD_LIMIT.name(), "Upload exceeds the size limit."));
  }
}
