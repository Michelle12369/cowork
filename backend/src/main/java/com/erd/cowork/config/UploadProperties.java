package com.erd.cowork.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "erd.upload")
public record UploadProperties(
    int maxFiles, long maxSessionBytes, long maxCsvBytes, long maxXlsxBytes, int sampleRows) {}
