package com.erd.cowork.agent.provider.openai;

import com.erd.cowork.agent.model.AgentFileContext;
import com.erd.cowork.agent.model.AgentRequest;
import com.erd.cowork.agent.model.HistoryMessage;
import com.erd.cowork.parsing.model.ColumnProfile;
import java.io.StringWriter;
import java.util.List;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.apache.velocity.VelocityContext;
import org.apache.velocity.app.VelocityEngine;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;
import org.springframework.util.CollectionUtils;
import org.springframework.util.StringUtils;

/** Assembles system and user prompts for the dashboard-generation agent. */
@Component
@ConditionalOnProperty(
    prefix = "erd.agent",
    name = "provider",
    havingValue = "openai-compatible",
    matchIfMissing = true)
@RequiredArgsConstructor
@Slf4j
public class PromptAssembler {

  /**
   * Fixed overhead outside the running StringBuilder: the system prompt (~15.6k chars after the
   * dataviz-method section) plus re-feed wrapping, JSON and HTTP framing (~500 chars buffer).
   */
  private static final int PROMPT_OVERHEAD_CHARS = 16_500;

  private static final String SYSTEM_PROMPT_TEMPLATE = "templates/openai/system-prompt.vm";

  private final VelocityEngine velocityEngine;
  private volatile String cachedSystemPrompt;

  /** Returns the fixed system prompt that instructs the LLM on its role and output rules. */
  public String systemPrompt() {
    if (cachedSystemPrompt == null) {
      synchronized (this) {
        if (cachedSystemPrompt == null) {
          StringWriter writer = new StringWriter();
          velocityEngine.mergeTemplate(
              SYSTEM_PROMPT_TEMPLATE, "UTF-8", new VelocityContext(), writer);
          cachedSystemPrompt = writer.toString();
        }
      }
    }
    return cachedSystemPrompt;
  }

  /**
   * Assembles the user-turn prompt from the request, serialising file profiles and conversation
   * history into structured Markdown.
   *
   * @param request the agent request including optional previousArtifactHtml for iteration
   * @param contextWindow the provider's context window size (tokens); used by the budget guard to
   *     decide whether to include the previous HTML feedback section
   */
  public String userPrompt(AgentRequest request, int contextWindow) {
    String historySection = buildHistorySection(request.history());
    String questionSection = buildQuestionSection(request.question());
    String filesSection = buildFilesSection(request.files());
    String prevHtmlSection =
        buildPreviousHtmlSection(
            request.previousArtifactHtml(),
            contextWindow,
            historySection.length() + questionSection.length() + filesSection.length());

    VelocityContext ctx = new VelocityContext();
    ctx.put("historySection", historySection);
    ctx.put("questionSection", questionSection);
    ctx.put("filesSection", filesSection);
    ctx.put("prevHtmlSection", prevHtmlSection);

    StringWriter writer = new StringWriter();
    velocityEngine.evaluate(
        ctx,
        writer,
        "user-prompt",
        "${historySection}${questionSection}${filesSection}${prevHtmlSection}");
    return writer.toString();
  }

  // ── private section builders ──────────────────────────────────────────────

  private String buildHistorySection(List<HistoryMessage> history) {
    if (CollectionUtils.isEmpty(history)) {
      return "";
    }
    StringBuilder sectionBuilder = new StringBuilder();
    sectionBuilder.append("## conversation so far\n");
    for (HistoryMessage msg : history) {
      // Truncation is applied upstream in AgentOrchestrator.buildHistoryMessage() so that the
      // options summary (appended after truncation) is never cut off. Do not re-truncate here.
      sectionBuilder.append(msg.sender()).append(": ").append(msg.text()).append("\n");
    }
    sectionBuilder.append("\n");
    return sectionBuilder.toString();
  }

  private String buildQuestionSection(String question) {
    return "# Question\n" + question + "\n\n";
  }

  private String buildFilesSection(List<AgentFileContext> files) {
    if (CollectionUtils.isEmpty(files)) {
      return "(no files uploaded)\n";
    }
    StringBuilder sectionBuilder = new StringBuilder();
    for (AgentFileContext file : files) {
      appendFileSection(sectionBuilder, file);
    }
    sectionBuilder.append(
        "Remember: output the complete HTML document inside a single ```html fenced block.\n");
    return sectionBuilder.toString();
  }

  /**
   * Builds the previous dashboard HTML section for iterative refinement, subject to a budget guard.
   * The budget guard estimates total prompt tokens as {@code totalChars / 3.5}; if the estimate
   * exceeds {@code contextWindow * 0.5}, the section is omitted and a warning is logged.
   */
  private String buildPreviousHtmlSection(
      String previousArtifactHtml, int contextWindow, int alreadyBuiltChars) {
    if (!StringUtils.hasText(previousArtifactHtml)) {
      return "";
    }

    double estimatedTokens =
        (alreadyBuiltChars + previousArtifactHtml.length() + PROMPT_OVERHEAD_CHARS) / 3.5;
    double budgetThreshold = contextWindow * 0.5;

    if (estimatedTokens > budgetThreshold) {
      log.warn(
          "Skipping previous HTML feedback: estimated tokens {} exceeds budget {}"
              + " (contextWindow={})",
          (int) estimatedTokens,
          (int) budgetThreshold,
          contextWindow);
      return "";
    }

    StringBuilder sectionBuilder = new StringBuilder();
    sectionBuilder.append("\n## previous dashboard html (the user is iterating on this)\n");
    sectionBuilder.append("```html\n");
    sectionBuilder.append(previousArtifactHtml);
    if (!previousArtifactHtml.endsWith("\n")) {
      sectionBuilder.append("\n");
    }
    sectionBuilder.append("```\n");
    sectionBuilder.append(
        "Modify ONLY what the user asked for. Keep all other markup, layout, colors,\n");
    sectionBuilder.append(
        "chart configs and text byte-identical. Output the complete updated HTML.\n");
    return sectionBuilder.toString();
  }

  private void appendFileSection(StringBuilder builder, AgentFileContext file) {
    var profile = file.profile();
    builder.append("## file: ").append(file.alias()).append(" (").append(file.name()).append(")\n");
    builder
        .append("type: ")
        .append(file.type())
        .append(" · rows: ")
        .append(profile.rowCount())
        .append("\n\n");

    List<ColumnProfile> columns = profile.columns();
    if (!CollectionUtils.isEmpty(columns)) {
      appendColumnTable(builder, columns);
      appendTopValues(builder, columns);
    }

    List<List<String>> sampleRows = profile.sampleRows();
    if (!CollectionUtils.isEmpty(sampleRows)) {
      appendSampleTable(builder, profile.headers(), sampleRows);
    }
  }

  private void appendColumnTable(StringBuilder builder, List<ColumnProfile> columns) {
    builder.append("| column | type | min | max | mean | std | nullCount |\n");
    builder.append("|---|---|---|---|---|---|---|\n");
    for (ColumnProfile col : columns) {
      boolean numeric = "number".equals(col.colType());
      builder
          .append("| ")
          .append(col.colName())
          .append(" | ")
          .append(col.colType())
          .append(" | ")
          .append(numeric ? String.valueOf(col.min()) : "-")
          .append(" | ")
          .append(numeric ? String.valueOf(col.max()) : "-")
          .append(" | ")
          .append(numeric ? String.valueOf(col.mean()) : "-")
          .append(" | ")
          .append(numeric ? String.valueOf(col.std()) : "-")
          .append(" | ")
          .append(col.nullCount())
          .append(" |\n");
    }
    builder.append("\n");
  }

  private void appendTopValues(StringBuilder builder, List<ColumnProfile> columns) {
    boolean any = false;
    for (ColumnProfile col : columns) {
      if (!"number".equals(col.colType()) && !CollectionUtils.isEmpty(col.topValues())) {
        builder
            .append("top values (")
            .append(col.colName())
            .append("): ")
            .append(String.join(", ", col.topValues()))
            .append("\n");
        any = true;
      }
    }
    if (any) {
      builder.append("\n");
    }
  }

  private void appendSampleTable(
      StringBuilder builder, List<String> headers, List<List<String>> sampleRows) {
    builder.append("### sample (first ").append(sampleRows.size()).append(" rows)\n");

    // header row — escape any pipe characters inside column names
    List<String> escapedHeaders =
        headers.stream().map(headerCell -> headerCell.replace("|", "\\|")).toList();
    builder.append("| ").append(String.join(" | ", escapedHeaders)).append(" |\n");

    // separator row driven by headers.size() (not row.size()) to stay aligned
    builder.append("|");
    for (int columnIndex = 0; columnIndex < headers.size(); columnIndex++) {
      builder.append("---|");
    }
    builder.append("\n");

    // data rows — pad short rows with empty cells, truncate long rows to headers.size()
    for (List<String> row : sampleRows) {
      builder.append("| ");
      int limit = Math.min(headers.size(), row.size());
      for (int columnIndex = 0; columnIndex < headers.size(); columnIndex++) {
        String cell = columnIndex < limit ? row.get(columnIndex).replace("|", "\\|") : "";
        builder.append(cell);
        if (columnIndex < headers.size() - 1) {
          builder.append(" | ");
        }
      }
      builder.append(" |\n");
    }
    builder.append("\n");
  }
}
