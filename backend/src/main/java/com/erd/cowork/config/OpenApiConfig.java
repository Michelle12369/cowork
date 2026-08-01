package com.erd.cowork.config;

import io.swagger.v3.oas.models.parameters.HeaderParameter;
import io.swagger.v3.oas.models.parameters.Parameter;
import org.springdoc.core.customizers.OpenApiCustomizer;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class OpenApiConfig {

  /**
   * Adds the optional {@code X-User-Id} header to every operation so the identity carried by {@link
   * com.erd.cowork.context.CurrentUser} stays documented after it was removed from controller
   * signatures. Missing/blank defaults to {@code local-dev} on the server side.
   */
  @Bean
  public OpenApiCustomizer currentUserHeaderCustomizer() {
    return openApi ->
        openApi
            .getPaths()
            .values()
            .forEach(
                pathItem ->
                    pathItem
                        .readOperations()
                        .forEach(
                            operation -> {
                              Parameter userHeader =
                                  new HeaderParameter()
                                      .name("X-User-Id")
                                      .required(false)
                                      .description(
                                          "Caller identity. Optional; defaults to 'local-dev' when"
                                              + " absent or blank.");
                              operation.addParametersItem(userHeader);
                            }));
  }
}
