package com.erd.cowork.service;

/**
 * One file within a built-in {@link SampleDataset}.
 *
 * @param alias the desired short alias for this file once loaded into a session (e.g. {@code
 *     "usage_log"}); fed as the effective upload filename so the existing {@link
 *     FileAliasUtils#generateAlias} slug logic derives exactly this alias
 * @param resourceFileName the classpath file name under {@code samples/} holding the actual bytes
 *     (e.g. {@code "usage_log_sample.csv"})
 */
public record SampleFile(String alias, String resourceFileName) {}
