package com.erd.cowork.agent.provider;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.ApplicationContext;

/**
 * Safety net for the SPI narrowing (spec §16.2): the Spring context must start cleanly under both
 * {@code erd.agent.provider} values, and each must expose exactly one {@link AgentProvider} bean.
 * The {@code langgraph-analysis} mode additionally must NOT expose a {@link DashboardAgentProvider}
 * bean — that capability only applies to modes where the LLM writes HTML directly (§16.2.1), and
 * browser-repair's {@code Optional<DashboardAgentProvider>} injection is what keeps context startup
 * from failing when that bean is absent.
 */
class ProviderContextLoadTest {

  @Nested
  @SpringBootTest(properties = "erd.agent.provider=openai-compatible")
  class OpenAiCompatible {

    @Autowired private ApplicationContext context;

    @Test
    void contextLoads_openAiCompatible_exactlyOneAgentProviderBean() {
      assertThat(context.getBeansOfType(AgentProvider.class)).hasSize(1);
    }

    @Test
    void contextLoads_openAiCompatible_exactlyOneDashboardAgentProviderBean() {
      assertThat(context.getBeansOfType(DashboardAgentProvider.class)).hasSize(1);
    }
  }

  @Nested
  @SpringBootTest(properties = "erd.agent.provider=langgraph-analysis")
  class LangGraphAnalysis {

    @Autowired private ApplicationContext context;

    @Test
    void contextLoads_langGraphAnalysis_exactlyOneAgentProviderBean() {
      assertThat(context.getBeansOfType(AgentProvider.class)).hasSize(1);
    }

    @Test
    void contextLoads_langGraphAnalysis_dashboardAgentProviderBeanAbsent() {
      assertThat(context.getBeansOfType(DashboardAgentProvider.class)).isEmpty();
    }
  }
}
