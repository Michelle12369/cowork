package com.erd.cowork.parsing;

import java.nio.file.Path;

/**
 * Result of normalizing one upload: the bytes to store and the format they are in.
 *
 * @param content path to the content to store. For xlsx this is a temp file the caller MUST delete
 *     after storing (open it with {@code StandardOpenOption.DELETE_ON_CLOSE}); for csv it is a temp
 *     copy of the upload, deleted the same way.
 * @param type the on-disk format, which is what {@code uploaded_file.type} records — always {@code
 *     csv} today, since xlsx is converted. NEVER the uploaded file's extension.
 */
public record NormalizedUpload(Path content, String type) {}
