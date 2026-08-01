package com.erd.cowork.parsing.model;

import java.util.List;

/**
 * Full-scan result returned by {@link com.erd.cowork.parsing.FileParsingService#readAll}. Contains
 * every data row in the file alongside the header column names.
 */
public record ParsedRows(List<String> columns, List<List<String>> rows) {}
