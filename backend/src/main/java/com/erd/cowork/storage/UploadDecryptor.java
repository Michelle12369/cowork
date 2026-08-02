package com.erd.cowork.storage;

import java.io.IOException;
import java.io.InputStream;

/**
 * Decrypts an uploaded file before it is written to storage.
 *
 * <p>Decryption MUST happen before {@link FileStorage#store}, not lazily on read: deepagent-service
 * points DuckDB at the stored file path directly (it never goes through {@link FileStorage#read}),
 * so the bytes at rest have to be plaintext or the Python side would need a second decryption
 * implementation.
 *
 * <p>The contract is stream-in/stream-out so that an implementation whose backing API cannot stream
 * may buffer internally — that choice stays inside the implementation instead of forcing every
 * caller to hold a whole file (uploads reach 2GB) in memory.
 */
public interface UploadDecryptor {

  /**
   * Returns a plaintext stream for {@code ciphertext}.
   *
   * @param ciphertext the uploaded bytes as received
   * @param originalFilename the client-supplied filename, for implementations that key off it
   * @return a stream of plaintext bytes. Returning {@code ciphertext} itself is permitted — that is
   *     exactly what the default {@code PassthroughUploadDecryptor} does — so the caller MAY end up
   *     closing the returned stream more than once (once via this return value, once via {@code
   *     ciphertext}); {@code close()} on the returned stream and on {@code ciphertext} MUST both be
   *     idempotent. A wrapper implementation that closes a delegate stream must likewise be safe
   *     under double-close. An implementation that buffers plaintext to a temp file owns deleting
   *     that file; it is not the caller's responsibility.
   * @throws IOException when decryption fails; the upload is then aborted and any partially stored
   *     object is cleaned up by the caller
   */
  InputStream decrypt(InputStream ciphertext, String originalFilename) throws IOException;
}
