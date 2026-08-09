package com.erd.cowork.storage;

import java.io.InputStream;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;

/**
 * Default {@link UploadDecryptor}: returns the upload untouched.
 *
 * <p>Active unless {@code erd.upload.decryption.enabled=true}, so environments without an internal
 * decryption API (local dev, this repo's docker stacks) behave exactly as before.
 */
@Component
@ConditionalOnProperty(
    prefix = "erd.upload.decryption",
    name = "enabled",
    havingValue = "false",
    matchIfMissing = true)
public class PassthroughUploadDecryptor implements UploadDecryptor {

  @Override
  public InputStream decrypt(InputStream ciphertext, String originalFilename) {
    return ciphertext;
  }
}
