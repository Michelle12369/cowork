package com.erd.cowork.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.erd.cowork.exception.NotFoundException;
import com.erd.cowork.web.dto.FileDto;
import com.erd.cowork.web.dto.SampleDatasetDto;
import java.util.List;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.web.multipart.MultipartFile;

@ExtendWith(MockitoExtension.class)
class SampleDatasetServiceTest {

  @Mock FileService fileService;

  SampleDatasetService service;

  @BeforeEach
  void setUp() {
    service = new SampleDatasetService(fileService);
  }

  @Test
  void listDatasets_returnsRegisteredCatalog() {
    List<SampleDatasetDto> datasets = service.listDatasets();

    assertThat(datasets).hasSize(2);
    SampleDatasetDto dataset = datasets.get(0);
    assertThat(dataset.name()).isEqualTo("product-usage-feedback");
    assertThat(dataset.title()).isEqualTo("產品使用行為與回饋");
    assertThat(dataset.description()).isNotBlank();
    assertThat(dataset.fileAliases()).containsExactly("usage_log", "feedback");
    SampleDatasetDto spcDataset = datasets.get(1);
    assertThat(spcDataset.name()).isEqualTo("spc-process-measurements");
    assertThat(spcDataset.fileAliases()).containsExactly("spc_data");
  }

  @Test
  void load_spcSample_readsClasspathResourceAndDelegatesToFileServiceUpload() {
    List<FileDto> expected =
        List.of(new FileDto("id9", "spc_data.csv", "spc_data", 100L, "csv", 2L, false));
    when(fileService.upload(eq("session-1"), any())).thenReturn(expected);

    List<FileDto> result = service.load("session-1", "spc-process-measurements");

    assertThat(result).isEqualTo(expected);
  }

  @Test
  void load_knownSample_readsClasspathResourcesAndDelegatesToFileServiceUpload() {
    List<FileDto> expected =
        List.of(new FileDto("id1", "usage_log.csv", "usage_log", 100L, "csv", 2L, false));
    when(fileService.upload(eq("session-1"), any())).thenReturn(expected);

    List<FileDto> result = service.load("session-1", "product-usage-feedback");

    assertThat(result).isSameAs(expected);

    @SuppressWarnings("unchecked")
    ArgumentCaptor<List<MultipartFile>> captor = ArgumentCaptor.forClass(List.class);
    verify(fileService).upload(eq("session-1"), captor.capture());
    List<MultipartFile> uploaded = captor.getValue();

    assertThat(uploaded).hasSize(2);
    assertThat(uploaded.get(0).getOriginalFilename()).isEqualTo("usage_log.csv");
    assertThat(uploaded.get(0).getSize()).isGreaterThan(0);
    assertThat(uploaded.get(1).getOriginalFilename()).isEqualTo("feedback.csv");
    assertThat(uploaded.get(1).getSize()).isGreaterThan(0);
  }

  @Test
  void load_unknownSample_throwsNotFoundException_andNeverCallsUpload() {
    assertThatThrownBy(() -> service.load("session-1", "does-not-exist"))
        .isInstanceOf(NotFoundException.class)
        .hasMessageContaining("does-not-exist");

    verify(fileService, never()).upload(any(), any());
  }
}
