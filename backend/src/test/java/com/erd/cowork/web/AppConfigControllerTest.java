package com.erd.cowork.web;

import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.erd.cowork.config.StorageProperties;
import com.erd.cowork.config.UploadProperties;
import com.erd.cowork.context.CurrentUserFilter;
import java.time.Duration;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

/**
 * Slice test for {@link AppConfigController}. Verifies that GET /api/config returns all upload
 * limits drawn from {@link UploadProperties} plus retention days from {@link StorageProperties}.
 */
@WebMvcTest(AppConfigController.class)
@Import(CurrentUserFilter.class)
class AppConfigControllerTest {

  @Autowired MockMvc mockMvc;

  @MockitoBean StorageProperties storageProperties;
  @MockitoBean UploadProperties uploadProperties;

  @Test
  void getConfig_returnsRetentionDaysAndAllUploadLimits() throws Exception {
    when(storageProperties.retention())
        .thenReturn(
            new StorageProperties.Retention(
                Duration.ofDays(30), Duration.ofDays(30), Duration.ofDays(730)));
    when(uploadProperties.maxFiles()).thenReturn(5);
    when(uploadProperties.maxSessionBytes()).thenReturn(5_368_709_120L);
    when(uploadProperties.maxCsvBytes()).thenReturn(2_147_483_648L);
    when(uploadProperties.maxXlsxBytes()).thenReturn(209_715_200L);

    mockMvc
        .perform(get("/api/config"))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.retentionDays").value(30))
        .andExpect(jsonPath("$.maxFiles").value(5))
        .andExpect(jsonPath("$.maxSessionBytes").value(5_368_709_120L))
        .andExpect(jsonPath("$.singleFileLimits.csv").value(2_147_483_648L))
        .andExpect(jsonPath("$.singleFileLimits.xlsx").value(209_715_200L))
        .andExpect(jsonPath("$.singleFileLimits.txt").doesNotExist())
        .andExpect(jsonPath("$.singleFileLimits.tsv").doesNotExist());
  }
}
