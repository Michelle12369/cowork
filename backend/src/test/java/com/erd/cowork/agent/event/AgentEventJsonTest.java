package com.erd.cowork.agent.event;

import static org.assertj.core.api.Assertions.assertThat;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.List;
import org.junit.jupiter.api.Test;

class AgentEventJsonTest {

  private final ObjectMapper mapper = new ObjectMapper();

  @Test
  void tokenEvent_serialize_containsTypeAndDelta() throws Exception {
    TokenEvent event = new TokenEvent("hi");
    String json = mapper.writeValueAsString(event);
    assertThat(json).contains("\"type\":\"TOKEN\"");
    assertThat(json).contains("\"delta\":\"hi\"");
  }

  @Test
  void tokenEvent_roundTrip_preservesValues() throws Exception {
    TokenEvent original = new TokenEvent("hello world");
    String json = mapper.writeValueAsString(original);
    AgentEvent deserialized = mapper.readValue(json, AgentEvent.class);
    assertThat(deserialized).isInstanceOf(TokenEvent.class);
    assertThat(((TokenEvent) deserialized).delta()).isEqualTo("hello world");
  }

  @Test
  void stepEvent_serialize_statusAsString() throws Exception {
    StepEvent event = new StepEvent("step-1", "Analyzing", "Processing data", StepStatus.RUNNING);
    String json = mapper.writeValueAsString(event);
    assertThat(json).contains("\"type\":\"STEP\"");
    assertThat(json).contains("\"status\":\"RUNNING\"");
    assertThat(json).contains("\"stepKey\":\"step-1\"");
  }

  @Test
  void stepEvent_roundTrip_preservesValues() throws Exception {
    StepEvent original = new StepEvent("k", "title", "desc", StepStatus.SUCCESS);
    String json = mapper.writeValueAsString(original);
    AgentEvent deserialized = mapper.readValue(json, AgentEvent.class);
    assertThat(deserialized).isInstanceOf(StepEvent.class);
    StepEvent step = (StepEvent) deserialized;
    assertThat(step.stepKey()).isEqualTo("k");
    assertThat(step.status()).isEqualTo(StepStatus.SUCCESS);
  }

  @Test
  void answerEvent_roundTrip_preservesText() throws Exception {
    AnswerEvent original = new AnswerEvent("The answer is 42");
    String json = mapper.writeValueAsString(original);
    assertThat(json).contains("\"type\":\"ANSWER\"");
    AgentEvent deserialized = mapper.readValue(json, AgentEvent.class);
    assertThat(deserialized).isInstanceOf(AnswerEvent.class);
    assertThat(((AnswerEvent) deserialized).text()).isEqualTo("The answer is 42");
  }

  @Test
  void artifactEvent_roundTrip_preservesFields() throws Exception {
    ArtifactEvent original = new ArtifactEvent("art-123", "Dashboard");
    String json = mapper.writeValueAsString(original);
    assertThat(json).contains("\"type\":\"ARTIFACT\"");
    assertThat(json).contains("\"artifactId\":\"art-123\"");
    AgentEvent deserialized = mapper.readValue(json, AgentEvent.class);
    assertThat(deserialized).isInstanceOf(ArtifactEvent.class);
    ArtifactEvent artifact = (ArtifactEvent) deserialized;
    assertThat(artifact.artifactId()).isEqualTo("art-123");
    assertThat(artifact.title()).isEqualTo("Dashboard");
  }

  @Test
  void errorEvent_roundTrip_preservesFields() throws Exception {
    ErrorEvent original = new ErrorEvent("ERR_001", "Something went wrong");
    String json = mapper.writeValueAsString(original);
    assertThat(json).contains("\"type\":\"ERROR\"");
    AgentEvent deserialized = mapper.readValue(json, AgentEvent.class);
    assertThat(deserialized).isInstanceOf(ErrorEvent.class);
    ErrorEvent error = (ErrorEvent) deserialized;
    assertThat(error.code()).isEqualTo("ERR_001");
    assertThat(error.message()).isEqualTo("Something went wrong");
  }

  @Test
  void thinkingEvent_serialize_containsTypeAndDelta() throws Exception {
    ThinkingEvent event = new ThinkingEvent("I am thinking...");
    String json = mapper.writeValueAsString(event);
    assertThat(json).contains("\"type\":\"THINKING\"");
    assertThat(json).contains("\"delta\":\"I am thinking...\"");
  }

  @Test
  void thinkingEvent_roundTrip_preservesValues() throws Exception {
    ThinkingEvent original = new ThinkingEvent("deep thought");
    String json = mapper.writeValueAsString(original);
    AgentEvent deserialized = mapper.readValue(json, AgentEvent.class);
    assertThat(deserialized).isInstanceOf(ThinkingEvent.class);
    assertThat(((ThinkingEvent) deserialized).delta()).isEqualTo("deep thought");
  }

  @Test
  void questionEvent_serialize_containsTypeAndQuestions() throws Exception {
    QuestionEvent event =
        new QuestionEvent(
            List.of(
                new com.erd.cowork.agent.model.ClarifyingQuestion(
                    "要分析哪個特性？", List.of("Bore Diameter", "全部"), false)));
    String json = mapper.writeValueAsString(event);
    assertThat(json).contains("\"type\":\"QUESTION\"");
    assertThat(json).contains("要分析哪個特性");
    assertThat(json).contains("Bore Diameter");
  }

  @Test
  void questionEvent_roundTrip_preservesValues() throws Exception {
    QuestionEvent original =
        new QuestionEvent(
            List.of(
                new com.erd.cowork.agent.model.ClarifyingQuestion(
                    "Which feature?", List.of("A", "B"), true)));
    String json = mapper.writeValueAsString(original);
    AgentEvent deserialized = mapper.readValue(json, AgentEvent.class);
    assertThat(deserialized).isInstanceOf(QuestionEvent.class);
    QuestionEvent qe = (QuestionEvent) deserialized;
    assertThat(qe.questions()).hasSize(1);
    assertThat(qe.questions().get(0).text()).isEqualTo("Which feature?");
    assertThat(qe.questions().get(0).options()).containsExactly("A", "B");
    assertThat(qe.questions().get(0).multiSelect()).isTrue();
  }

  @Test
  void codeEvent_serialize_containsTypeAndDelta() throws Exception {
    CodeEvent event = new CodeEvent("<div>x</div>");
    String json = mapper.writeValueAsString(event);
    assertThat(json).contains("\"type\":\"CODE\"");
    assertThat(json).contains("\"delta\":\"<div>x</div>\"");
  }

  @Test
  void codeEvent_roundTrip_preservesValues() throws Exception {
    CodeEvent original = new CodeEvent("<html>");
    String json = mapper.writeValueAsString(original);
    AgentEvent deserialized = mapper.readValue(json, AgentEvent.class);
    assertThat(deserialized).isInstanceOf(CodeEvent.class);
    assertThat(((CodeEvent) deserialized).delta()).isEqualTo("<html>");
  }

  @Test
  void tableEvent_serialize_containsTypeAndFields() throws Exception {
    TableEvent event =
        new TableEvent(
            "tbl_1",
            "計算各機台的不良率",
            List.of("machine_id", "defect_rate"),
            List.of(List.of("M1", 0.02)),
            false);
    String json = mapper.writeValueAsString(event);
    assertThat(json).contains("\"type\":\"TABLE\"");
    assertThat(json).contains("\"tableId\":\"tbl_1\"");
    assertThat(json).contains("\"truncated\":false");
  }

  @Test
  void tableEvent_roundTrip_preservesValues() throws Exception {
    TableEvent original =
        new TableEvent(
            "tbl_2",
            "查詢意圖",
            List.of("machine_id", "defect_rate"),
            List.of(List.of("M1", 0.02), List.of("M2", 0.05)),
            true);
    String json = mapper.writeValueAsString(original);
    AgentEvent deserialized = mapper.readValue(json, AgentEvent.class);
    assertThat(deserialized).isInstanceOf(TableEvent.class);
    TableEvent table = (TableEvent) deserialized;
    assertThat(table.tableId()).isEqualTo("tbl_2");
    assertThat(table.intent()).isEqualTo("查詢意圖");
    assertThat(table.columns()).containsExactly("machine_id", "defect_rate");
    assertThat(table.rows()).hasSize(2);
    assertThat(table.rows().get(0)).containsExactly("M1", 0.02);
    assertThat(table.truncated()).isTrue();
  }
}
