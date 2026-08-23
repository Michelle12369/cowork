package com.erd.cowork.web;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.Optional;
import org.junit.jupiter.api.Test;

/**
 * Plain-POJO test (no Spring context) for {@link ConnectorController}'s degrade path when {@link
 * com.erd.cowork.agent.provider.analysis.AnalysisConnectorsClient} is absent — the case when the
 * active provider is not {@code langgraph-analysis}, so the connector-catalog proxy has nothing to
 * call. Complements {@link ConnectorControllerTest}, which covers the client-present branches via
 * {@code @WebMvcTest}.
 */
class ConnectorControllerNoClientTest {

  @Test
  void listConnectors_noClientBound_returnsEmptyList() {
    ConnectorController controller = new ConnectorController(Optional.empty());

    assertThat(controller.listConnectors()).isEmpty();
  }
}
