package com.erd.cowork.exception;

/**
 * Thrown when a session contains one or more files that have been removed by the retention policy.
 * The user must delete the expired file entries and re-upload before the agent pipeline or artifact
 * repair flow can proceed.
 */
public class FilesExpiredException extends RuntimeException {

  private static final String MESSAGE_TEMPLATE = "檔案因超過 %d 天未活動已被清除，請移除過期檔案並重新上傳後再繼續。";

  public FilesExpiredException(int retentionDays) {
    super(String.format(MESSAGE_TEMPLATE, retentionDays));
  }
}
