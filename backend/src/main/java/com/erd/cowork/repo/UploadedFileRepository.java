package com.erd.cowork.repo;

import com.erd.cowork.domain.UploadedFile;
import java.util.List;
import org.springframework.data.jpa.repository.JpaRepository;

public interface UploadedFileRepository extends JpaRepository<UploadedFile, String> {
  List<UploadedFile> findBySessionId(String sessionId);

  List<UploadedFile> findBySessionIdAndExpiredFalse(String sessionId);
}
