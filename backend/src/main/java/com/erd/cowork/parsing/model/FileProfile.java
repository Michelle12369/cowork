package com.erd.cowork.parsing.model;

import java.util.List;

public record FileProfile(
    long rowCount,
    int colCount,
    List<String> headers,
    List<ColumnProfile> columns,
    List<List<String>> sampleRows) {}
