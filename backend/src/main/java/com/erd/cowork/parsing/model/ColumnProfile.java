package com.erd.cowork.parsing.model;

import java.util.List;

public record ColumnProfile(
    String colName,
    String colType,
    Double min,
    Double max,
    Double mean,
    Double std,
    long nullCount,
    List<String> topValues) {}
