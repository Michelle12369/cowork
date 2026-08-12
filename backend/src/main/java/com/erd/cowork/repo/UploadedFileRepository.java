package com.erd.cowork.repo;

import com.erd.cowork.domain.UploadedFile;
import java.util.List;
import org.springframework.data.mongodb.repository.MongoRepository;

public interface UploadedFileRepository extends MongoRepository<UploadedFile, String> {
  List<UploadedFile> findBySessionId(String sessionId);

  List<UploadedFile> findBySessionIdAndExpiredFalse(String sessionId);
}
