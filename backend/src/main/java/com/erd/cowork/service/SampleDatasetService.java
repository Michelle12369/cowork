package com.erd.cowork.service;

import com.erd.cowork.exception.NotFoundException;
import com.erd.cowork.logging.LogAnnotation;
import com.erd.cowork.web.dto.FileDto;
import com.erd.cowork.web.dto.SampleDatasetDto;
import java.util.List;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;

/**
 * Registry and loader for built-in demo datasets. Loading a dataset is equivalent to the user
 * uploading its bundled files directly — it reuses {@link FileService#upload} end to end (storage,
 * parsing, alias generation, and per-session limit checks), so no upload constraint is bypassed.
 */
@Slf4j
@Service
@RequiredArgsConstructor
@LogAnnotation
public class SampleDatasetService {

  private static final String RESOURCE_DIR = "samples/";

  /**
   * Program-defined catalogue of built-in sample datasets. Static on purpose — these ship with the
   * jar and are not user-editable, so a database table would be unwarranted indirection.
   */
  private static final List<SampleDataset> CATALOG =
      List.of(
          new SampleDataset(
              "product-usage-feedback",
              "產品使用行為與回饋",
              "使用行為紀錄與使用者回饋，適合分析功能採用度與滿意度之間的關聯。",
              List.of(
                  new SampleFile("usage_log", "usage_log_sample.csv"),
                  new SampleFile("feedback", "feedback_sample.csv"))),
          new SampleDataset(
              "spc-process-measurements",
              "SPC 製程量測",
              "半導體製程量測紀錄（lot/wafer、量測值與規格界限），適合 SPC 管制圖與製程能力分析。",
              List.of(new SampleFile("spc_data", "spc_demo_dataset.csv"))));

  private final FileService fileService;

  /** Lists all built-in sample datasets available to load. */
  public List<SampleDatasetDto> listDatasets() {
    return CATALOG.stream().map(SampleDatasetService::toDto).toList();
  }

  /**
   * Loads the named sample dataset's files into {@code sessionId}, exactly as if the user had
   * uploaded them — same storage, alias, and limit-checking path as {@link
   * com.erd.cowork.web.FileController}.
   *
   * @throws NotFoundException if no dataset is registered under {@code sampleName}, or (via {@link
   *     FileService#upload}) if the session belongs to a different user
   */
  public List<FileDto> load(String sessionId, String sampleName) {
    SampleDataset dataset = findByName(sampleName);
    log.info(
        "loading sample dataset session={} sample={} fileCount={}",
        sessionId,
        sampleName,
        dataset.files().size());
    List<MultipartFile> files =
        dataset.files().stream()
            .map(
                sampleFile ->
                    (MultipartFile)
                        new ClasspathMultipartFile(
                            sampleFile.alias() + fileExtension(sampleFile.resourceFileName()),
                            RESOURCE_DIR + sampleFile.resourceFileName()))
            .toList();
    return fileService.upload(sessionId, files);
  }

  private SampleDataset findByName(String sampleName) {
    return CATALOG.stream()
        .filter(dataset -> dataset.name().equals(sampleName))
        .findFirst()
        .orElseThrow(() -> new NotFoundException("sample dataset not found: " + sampleName));
  }

  private static String fileExtension(String resourceFileName) {
    int dot = resourceFileName.lastIndexOf('.');
    return dot < 0 ? "" : resourceFileName.substring(dot);
  }

  private static SampleDatasetDto toDto(SampleDataset dataset) {
    List<String> fileAliases = dataset.files().stream().map(SampleFile::alias).toList();
    return new SampleDatasetDto(
        dataset.name(), dataset.title(), dataset.description(), fileAliases);
  }
}
