package com.erd.cowork.service;

import java.util.List;

/**
 * A built-in demo dataset the user can load into a session with one click, equivalent to uploading
 * {@link #files()} directly.
 *
 * @param name stable machine identifier used in the load API path (e.g. {@code
 *     "product-usage-feedback"})
 * @param title human-facing title shown in the UI
 * @param description one-sentence explanation of what the dataset contains
 * @param files the files bundled in this dataset, in upload order
 */
public record SampleDataset(
    String name, String title, String description, List<SampleFile> files) {}
