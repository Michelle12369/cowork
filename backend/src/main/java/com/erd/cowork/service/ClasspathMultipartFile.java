package com.erd.cowork.service;

import java.io.ByteArrayInputStream;
import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.io.UncheckedIOException;
import java.nio.file.Files;
import org.springframework.core.io.ClassPathResource;
import org.springframework.web.multipart.MultipartFile;

/**
 * Adapts a classpath resource (a bundled sample dataset file) into a {@link MultipartFile} so it
 * can be fed through the existing upload pipeline ({@code FileService#upload}) unchanged — same
 * storage, alias generation, and limit-checking code path as a real browser upload.
 *
 * <p>non-bean: instantiate per sample file being loaded.
 */
final class ClasspathMultipartFile implements MultipartFile {

  private final String originalFilename;
  private final byte[] content;

  /**
   * @param originalFilename the filename presented to the upload pipeline — drives the alias and
   *     display name derived by {@link FileAliasUtils}
   * @param classpathLocation the classpath path of the actual file bytes, e.g. {@code
   *     "samples/usage_log_sample.csv"}
   */
  ClasspathMultipartFile(String originalFilename, String classpathLocation) {
    this.originalFilename = originalFilename;
    ClassPathResource resource = new ClassPathResource(classpathLocation);
    try (InputStream in = resource.getInputStream()) {
      this.content = in.readAllBytes();
    } catch (IOException exception) {
      throw new UncheckedIOException(
          "failed to read sample dataset resource: " + classpathLocation, exception);
    }
  }

  @Override
  public String getName() {
    return "files";
  }

  @Override
  public String getOriginalFilename() {
    return originalFilename;
  }

  @Override
  public String getContentType() {
    return "text/csv";
  }

  @Override
  public boolean isEmpty() {
    return content.length == 0;
  }

  @Override
  public long getSize() {
    return content.length;
  }

  @Override
  public byte[] getBytes() {
    return content;
  }

  @Override
  public InputStream getInputStream() {
    return new ByteArrayInputStream(content);
  }

  @Override
  public void transferTo(File dest) throws IOException {
    Files.write(dest.toPath(), content);
  }
}
