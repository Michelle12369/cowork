package com.erd.cowork.agent.provider.openai;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;

/**
 * Detects placeholder-comment code omissions in HTML artifact strings.
 *
 * <p>Scans JS comments ({@code //} and block comments) inside inline {@code <script>} blocks, and
 * HTML comments ({@code <!-- -->}) in the body outside script blocks. String literals are tracked
 * so values such as {@code const label = '策略分析'} are never matched.
 *
 * <p>Script boundaries use a two-phase approach like {@link JsSyntaxValidator}: a regex locates the
 * opening tag, then a JS-aware state machine ({@link #findInlineScriptEnd}) finds the true {@code
 * </script>} terminator. Self-contained — shares no code with {@link JsSyntaxValidator}.
 *
 * <p>Openai-compatible–specific; lives in {@code agent/provider/openai/}. Not used by the
 * browser-repair path ({@link com.erd.cowork.agent.repair.ArtifactRepairer}).
 */
@Component
@ConditionalOnProperty(
    prefix = "erd.agent",
    name = "provider",
    havingValue = "openai-compatible",
    matchIfMissing = true)
@Slf4j
public class CodeOmissionValidator {

  /**
   * Minimum output/previous length ratio below which omission scanning activates.
   *
   * <p>A lazy omission shrinks output dramatically relative to the previous artifact; fresh
   * generations and normal-size iterations are skipped entirely, avoiding false positives from
   * legitimate comments that happen to contain a pattern word.
   */
  static final double SHRINK_RATIO_THRESHOLD = 0.7;

  /**
   * Chinese placeholder patterns — present in a comment text → omission finding.
   *
   * <p>Deliberately conservative: bare {@code 省略} was removed because it appears in legitimate
   * explanatory comments; only compound forms that unambiguously indicate elided code remain.
   */
  static final String[] ZH_PATTERNS = {
    "程式略", "代碼略", "圖表程式略", "保留原本", "其餘不變", "其餘相同", "同前一版", "原有程式碼不變", "以下省略", "其餘省略", "中略"
  };

  /**
   * English placeholder patterns matched case-insensitively.
   *
   * <p>Deliberately conservative: bare {@code existing code} / {@code unchanged} / {@code omitted}
   * / {@code placeholder} were removed — they appear in legitimate comments. Only compound forms
   * that unambiguously indicate elided code remain.
   */
  static final String[] EN_PATTERNS_LOWER = {
    "omitted for brevity",
    "code omitted",
    "rest of the code",
    "rest of the script",
    "same as before",
    "same as above",
    "remains the same",
    "remains unchanged",
    "code unchanged",
    "keep the original",
    "... existing code ..."
  };

  /**
   * Matches the opening {@code <script...>} tag. Group 1 = attributes string (may be empty). The
   * content end is located by {@link #findInlineScriptEnd}.
   */
  private static final Pattern OPEN_TAG_PATTERN =
      Pattern.compile("<script([^>]*)>", Pattern.CASE_INSENSITIVE);

  // ── Lexer state constants ──────────────────────────────────────────────────
  private static final int STATE_NORMAL = 0;
  private static final int STATE_SINGLE_QUOTE = 1;
  private static final int STATE_DOUBLE_QUOTE = 2;
  private static final int STATE_TEMPLATE = 3;
  private static final int STATE_LINE_COMMENT = 4;
  private static final int STATE_BLOCK_COMMENT = 5;

  /**
   * Scans {@code html} for placeholder comments indicating code omissions.
   *
   * <p>Gated on output shrinkage relative to {@code previousArtifactHtml} — fresh generations and
   * normal-size iterations skip the scan entirely (see {@link #SHRINK_RATIO_THRESHOLD}).
   *
   * @param html the full HTML string to validate
   * @param previousArtifactHtml the previous artifact this output iterated on; {@code null} or
   *     blank means fresh generation (scan skipped)
   * @return list of findings; empty list means no omissions detected (or scan was gated off)
   */
  public List<CodeOmissionFinding> validate(String html, String previousArtifactHtml) {
    if (!StringUtils.hasText(html)) {
      return List.of();
    }

    // Shrinkage gate: only a dramatically shorter output than the previous artifact can be a
    // lazy omission. Fresh generations and normal-size iterations never reach the pattern scan.
    if (!StringUtils.hasText(previousArtifactHtml)) {
      return List.of();
    }
    if (html.length() >= previousArtifactHtml.length() * SHRINK_RATIO_THRESHOLD) {
      return List.of();
    }

    List<CodeOmissionFinding> findings = new ArrayList<>();

    // Collect full script-element spans [openTagStart, afterCloseTag] so that the
    // HTML-comment scanner can skip regions that are inside <script> elements.
    List<int[]> scriptSpans = new ArrayList<>();

    Matcher openTagMatcher = OPEN_TAG_PATTERN.matcher(html);
    int searchFrom = 0;

    while (openTagMatcher.find(searchFrom)) {
      String attrs = openTagMatcher.group(1);
      int contentStart = openTagMatcher.end();
      int openTagStart = openTagMatcher.start();

      // Find the true </script> terminator using a JS-aware scanner.
      int contentEnd = findInlineScriptEnd(html, contentStart);

      // Advance past the closing </script> tag.
      int closeGt = (contentEnd < html.length()) ? html.indexOf('>', contentEnd) : -1;
      int afterCloseTag = (closeGt >= 0) ? closeGt + 1 : html.length();
      searchFrom = afterCloseTag;

      // Record the span regardless of external/inline so HTML-comment scanner excludes it.
      scriptSpans.add(new int[] {openTagStart, afterCloseTag});

      // External scripts (src= attribute) have no inline JS comments to scan.
      if (attrs != null && attrs.toLowerCase(Locale.ROOT).contains("src=")) {
        continue;
      }

      String scriptContent = html.substring(contentStart, contentEnd);
      if (scriptContent.isBlank()) {
        continue;
      }

      scanJsComments(html, contentStart, contentEnd, findings);
    }

    // Scan HTML comments (<!-- -->) in regions NOT covered by any script element.
    scanHtmlComments(html, scriptSpans, findings);

    return findings;
  }

  // ── JS comment scanner ────────────────────────────────────────────────────

  /**
   * Scans the slice {@code html[from..to)} for JS {@code //} line comments and {@code /* ... *}
   * {@code /} block comments that match placeholder patterns. String literals are tracked so their
   * content is never checked against patterns.
   */
  private void scanJsComments(String html, int from, int to, List<CodeOmissionFinding> findings) {
    int pos = from;
    int state = STATE_NORMAL;
    StringBuilder commentBuffer = new StringBuilder();

    while (pos < to) {
      char ch = html.charAt(pos);

      switch (state) {
        case STATE_NORMAL:
          if (ch == '\'') {
            state = STATE_SINGLE_QUOTE;
            pos++;
          } else if (ch == '"') {
            state = STATE_DOUBLE_QUOTE;
            pos++;
          } else if (ch == '`') {
            state = STATE_TEMPLATE;
            pos++;
          } else if (ch == '/' && pos + 1 < to) {
            char next = html.charAt(pos + 1);
            if (next == '/') {
              state = STATE_LINE_COMMENT;
              commentBuffer.setLength(0);
              pos += 2;
            } else if (next == '*') {
              state = STATE_BLOCK_COMMENT;
              commentBuffer.setLength(0);
              pos += 2;
            } else {
              pos++;
            }
          } else {
            pos++;
          }
          break;

        case STATE_SINGLE_QUOTE:
          if (ch == '\\') {
            pos += 2; // skip escaped character
          } else if (ch == '\'') {
            state = STATE_NORMAL;
            pos++;
          } else {
            pos++;
          }
          break;

        case STATE_DOUBLE_QUOTE:
          if (ch == '\\') {
            pos += 2;
          } else if (ch == '"') {
            state = STATE_NORMAL;
            pos++;
          } else {
            pos++;
          }
          break;

        case STATE_TEMPLATE:
          if (ch == '\\') {
            pos += 2;
          } else if (ch == '`') {
            state = STATE_NORMAL;
            pos++;
          } else {
            pos++;
          }
          break;

        case STATE_LINE_COMMENT:
          if (ch == '\n') {
            addFindingIfMatch(commentBuffer.toString(), findings);
            commentBuffer.setLength(0);
            state = STATE_NORMAL;
            pos++;
          } else {
            commentBuffer.append(ch);
            pos++;
          }
          break;

        case STATE_BLOCK_COMMENT:
          if (ch == '*' && pos + 1 < to && html.charAt(pos + 1) == '/') {
            addFindingIfMatch(commentBuffer.toString(), findings);
            commentBuffer.setLength(0);
            state = STATE_NORMAL;
            pos += 2;
          } else {
            commentBuffer.append(ch);
            pos++;
          }
          break;

        default:
          pos++;
          break;
      }
    }

    // Flush a line comment that reaches the end of the script block without a trailing newline.
    if (state == STATE_LINE_COMMENT && commentBuffer.length() > 0) {
      addFindingIfMatch(commentBuffer.toString(), findings);
    }
  }

  // ── HTML comment scanner ──────────────────────────────────────────────────

  /**
   * Scans {@code html} for {@code <!-- ... -->} HTML comments that are NOT inside any of the {@code
   * scriptSpans}, and checks each comment's text for placeholder patterns.
   */
  private void scanHtmlComments(
      String html, List<int[]> scriptSpans, List<CodeOmissionFinding> findings) {
    int pos = 0;
    int len = html.length();

    while (pos < len) {
      int commentStart = html.indexOf("<!--", pos);
      if (commentStart < 0) {
        break;
      }

      // Skip HTML comments that fall inside a <script> element span.
      if (isInsideAnySpan(commentStart, scriptSpans)) {
        pos = commentStart + 4;
        continue;
      }

      int commentContentStart = commentStart + 4;
      int closePos = html.indexOf("-->", commentContentStart);
      if (closePos < 0) {
        // Unclosed comment — no further matches possible.
        break;
      }

      String commentText = html.substring(commentContentStart, closePos);
      addFindingIfMatch(commentText, findings);

      pos = closePos + 3;
    }
  }

  // ── Script-end locator ────────────────────────────────────────────────────

  /**
   * Finds the position of the true {@code </script} terminator in {@code html} starting at {@code
   * pos}, honouring JS lexical context (string literals and comments) so that embedded {@code
   * </script>} occurrences are not mistaken for the tag terminator.
   *
   * <p>This scanner is self-contained and does not share code with {@link JsSyntaxValidator}.
   *
   * @param html full HTML string
   * @param pos index of the first character after the opening {@code <script...>} tag
   * @return index of the {@code <} in {@code </script}, or {@code html.length()} if not found
   */
  private int findInlineScriptEnd(String html, int pos) {
    int len = html.length();
    int state = STATE_NORMAL;

    while (pos < len) {
      char ch = html.charAt(pos);

      switch (state) {
        case STATE_NORMAL:
          if (ch == '\'') {
            state = STATE_SINGLE_QUOTE;
            pos++;
          } else if (ch == '"') {
            state = STATE_DOUBLE_QUOTE;
            pos++;
          } else if (ch == '`') {
            state = STATE_TEMPLATE;
            pos++;
          } else if (ch == '/' && pos + 1 < len) {
            char next = html.charAt(pos + 1);
            if (next == '/') {
              state = STATE_LINE_COMMENT;
              pos += 2;
            } else if (next == '*') {
              state = STATE_BLOCK_COMMENT;
              pos += 2;
            } else {
              pos++;
            }
          } else if (ch == '<'
              && pos + 8 <= len
              && html.substring(pos, pos + 8).toLowerCase(Locale.ROOT).equals("</script")) {
            return pos;
          } else {
            pos++;
          }
          break;

        case STATE_SINGLE_QUOTE:
          if (ch == '\\') {
            pos += 2;
          } else if (ch == '\'') {
            state = STATE_NORMAL;
            pos++;
          } else {
            pos++;
          }
          break;

        case STATE_DOUBLE_QUOTE:
          if (ch == '\\') {
            pos += 2;
          } else if (ch == '"') {
            state = STATE_NORMAL;
            pos++;
          } else {
            pos++;
          }
          break;

        case STATE_TEMPLATE:
          if (ch == '\\') {
            pos += 2;
          } else if (ch == '`') {
            state = STATE_NORMAL;
            pos++;
          } else {
            pos++;
          }
          break;

        case STATE_LINE_COMMENT:
          if (ch == '\n') {
            state = STATE_NORMAL;
          }
          pos++;
          break;

        case STATE_BLOCK_COMMENT:
          if (ch == '*' && pos + 1 < len && html.charAt(pos + 1) == '/') {
            state = STATE_NORMAL;
            pos += 2;
          } else {
            pos++;
          }
          break;

        default:
          pos++;
          break;
      }
    }

    return len; // no terminator found — treat remainder as script content
  }

  // ── Pattern matching helpers ──────────────────────────────────────────────

  private boolean isInsideAnySpan(int pos, List<int[]> spans) {
    for (int[] span : spans) {
      if (pos >= span[0] && pos < span[1]) {
        return true;
      }
    }
    return false;
  }

  private void addFindingIfMatch(String commentText, List<CodeOmissionFinding> findings) {
    if (matchesPlaceholderPattern(commentText)) {
      String trimmed = commentText.trim();
      findings.add(new CodeOmissionFinding(trimmed));
    }
  }

  private boolean matchesPlaceholderPattern(String commentText) {
    for (String zhPattern : ZH_PATTERNS) {
      if (commentText.contains(zhPattern)) {
        return true;
      }
    }
    String lower = commentText.toLowerCase(Locale.ROOT);
    for (String enPattern : EN_PATTERNS_LOWER) {
      if (lower.contains(enPattern)) {
        return true;
      }
    }
    return false;
  }
}
