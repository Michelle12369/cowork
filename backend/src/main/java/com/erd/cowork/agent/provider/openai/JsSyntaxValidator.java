package com.erd.cowork.agent.provider.openai;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import lombok.extern.slf4j.Slf4j;
import org.graalvm.polyglot.Context;
import org.graalvm.polyglot.PolyglotException;
import org.graalvm.polyglot.Source;
import org.graalvm.polyglot.SourceSection;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;

/**
 * Validates JavaScript syntax in inline {@code <script>} blocks of an HTML string.
 *
 * <p>Uses GraalVM Polyglot {@code Context.parse()} in parse-only mode (never executes); a new
 * {@link Context} is created per call since GraalVM Context is not thread-safe.
 *
 * <p>Script boundaries use a two-phase approach: a regex locates the opening tag, then a JS-aware
 * scanner ({@link #findScriptEnd}) walks the content honouring string literals and comments, so
 * {@code </script>} embedded in JS strings (e.g. ECharts tooltip formatters) doesn't falsely split
 * a block.
 *
 * <p>On parser failure the exception is logged at WARN and an empty list is returned so the main
 * flow is never blocked. Openai-compatible–specific; not used by the browser-repair path ({@link
 * com.erd.cowork.agent.repair.ArtifactRepairer}).
 */
@Component
@ConditionalOnProperty(
    prefix = "erd.agent",
    name = "provider",
    havingValue = "openai-compatible",
    matchIfMissing = true)
@Slf4j
public class JsSyntaxValidator {

  /**
   * Matches the opening {@code <script...>} tag. Group 1 = attributes string (may be empty). The
   * content and closing tag are located by {@link #findScriptEnd(String, int)}.
   */
  private static final Pattern OPEN_TAG_PATTERN =
      Pattern.compile("<script([^>]*)>", Pattern.CASE_INSENSITIVE);

  // Lexer state constants used by findScriptEnd
  private static final int STATE_NORMAL = 0;
  private static final int STATE_SINGLE_QUOTE = 1;
  private static final int STATE_DOUBLE_QUOTE = 2;
  private static final int STATE_TEMPLATE = 3;
  private static final int STATE_LINE_COMMENT = 4;
  private static final int STATE_BLOCK_COMMENT = 5;
  private static final int STATE_REGEX = 6;

  // Keywords that end in an identifier character but syntactically expect an expression next --
  // isRegexContext's "identifier-ending -> division" default must not apply to these, or `/`
  // right after e.g. `return` would be misread as division instead of a regex literal.
  private static final Set<String> REGEX_CONTEXT_KEYWORDS =
      Set.of(
          "return",
          "typeof",
          "instanceof",
          "in",
          "of",
          "new",
          "delete",
          "void",
          "throw",
          "case",
          "do",
          "else",
          "yield",
          "await",
          "extends",
          "default");

  /**
   * Decides whether {@code text.charAt(slashIndex)} (a {@code '/'}) starts a regex literal or is a
   * division operator, by looking at the previous significant character -- a regex can only begin
   * where an expression is expected. Port of Python's {@code js_lexer._is_regex_context}; MUST stay
   * structurally parallel, including the {@code <}/{@code >} exclusion below.
   *
   * <p>A previous character that ends a value (identifier/number, {@code )}, {@code ]}) means an
   * operator is expected next, so {@code /} is division; the keyword list above is the exception --
   * those end in identifier characters but still expect an expression. Everything else (operators,
   * {@code (}/{@code [}/{@code {}/{@code ,}/{@code ;}/{@code :}, or start of input) expects an
   * expression, so {@code /} is a regex literal. {@code }} falls through to that default
   * deliberately: a statement-position regex right after a block (`if(x){}\n/re/.test(y)`) is far
   * more common than a division right after an object literal.
   *
   * <p>{@code <}/{@code >} are also deliberately excluded from "expression expected", returning
   * false (division-like, no state change): this method is also applied to full HTML documents (not
   * just isolated script content) by callers that scan for other purposes, and closing tags like
   * {@code </script>}/{@code </div>} put a {@code /} right after {@code <} -- treating that as
   * regex context would swallow everything up to the next stray {@code /}, reintroducing the exact
   * "rest of the document disappears" failure this method exists to fix. The sacrifice is the rare
   * `x < /re/.test(y)` right after a bare comparison operator, which degrades to inert
   * division-like handling instead -- a strictly safer failure mode.
   */
  private boolean isRegexContext(String text, int slashIndex) {
    int position = slashIndex - 1;
    while (position >= 0 && Character.isWhitespace(text.charAt(position))) {
      position--;
    }
    if (position < 0) {
      return true;
    }

    char previousCharacter = text.charAt(position);
    if (previousCharacter == ')'
        || previousCharacter == ']'
        || previousCharacter == '<'
        || previousCharacter == '>') {
      return false;
    }

    if (Character.isLetterOrDigit(previousCharacter)
        || previousCharacter == '_'
        || previousCharacter == '$') {
      int wordEnd = position + 1;
      int wordStart = wordEnd;
      while (wordStart > 0
          && (Character.isLetterOrDigit(text.charAt(wordStart - 1))
              || text.charAt(wordStart - 1) == '_'
              || text.charAt(wordStart - 1) == '$')) {
        wordStart--;
      }
      String word = text.substring(wordStart, wordEnd);
      if (Character.isDigit(word.charAt(0))) {
        return false; // ends a number literal
      }
      return REGEX_CONTEXT_KEYWORDS.contains(word);
    }

    return true;
  }

  /**
   * Extracts all inline {@code <script>} blocks from {@code html} and parses each for syntax errors
   * using GraalJS.
   *
   * @param html the full HTML string to validate
   * @return list of syntax errors found; empty list means no errors (or validator crashed)
   */
  public List<JsSyntaxError> validate(String html) {
    List<JsSyntaxError> errors = new ArrayList<>();
    Matcher openTagMatcher = OPEN_TAG_PATTERN.matcher(html);
    int scriptIndex = 0;
    int searchFrom = 0;

    while (openTagMatcher.find(searchFrom)) {
      String attrs = openTagMatcher.group(1);
      int contentStart = openTagMatcher.end();

      // Find the true </script terminator using the JS-aware scanner
      int contentEnd = findScriptEnd(html, contentStart);
      String content = html.substring(contentStart, contentEnd);

      // Advance past the close tag (find the '>' after </script)
      int closeGt = (contentEnd < html.length()) ? html.indexOf('>', contentEnd) : -1;
      searchFrom = (closeGt >= 0) ? closeGt + 1 : html.length();

      // Skip external scripts (src= attribute present)
      if (attrs != null && attrs.toLowerCase(Locale.ROOT).contains("src=")) {
        scriptIndex++;
        continue;
      }

      if (content.isBlank()) {
        scriptIndex++;
        continue;
      }

      errors.addAll(parseScript(scriptIndex, content));
      scriptIndex++;
    }

    return errors;
  }

  /**
   * Finds the position of the true {@code </script} terminator starting at {@code pos}, honoring JS
   * lexical context (string/template literals, line and block comments, backslash escapes) so
   * embedded {@code </script>} occurrences are not mistaken for the tag terminator.
   *
   * @param html full HTML string
   * @param pos index of the first character after the opening {@code <script...>} tag
   * @return index of the {@code <} in {@code </script} (case-insensitive), or {@code html.length()}
   *     if no terminator is found
   */
  private int findScriptEnd(String html, int pos) {
    int len = html.length();
    int state = STATE_NORMAL;
    boolean regexInCharacterClass = false;

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
            } else if (isRegexContext(html, pos)) {
              state = STATE_REGEX;
              regexInCharacterClass = false;
              pos++;
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

        case STATE_REGEX:
          if (ch == '\\') {
            pos += 2;
          } else if (ch == '[') {
            regexInCharacterClass = true;
            pos++;
          } else if (ch == ']') {
            regexInCharacterClass = false;
            pos++;
          } else if (ch == '/' && !regexInCharacterClass) {
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

    return len; // no terminator found — treat rest of html as content
  }

  private List<JsSyntaxError> parseScript(int scriptIndex, String content) {
    try (Context ctx =
        Context.newBuilder("js").option("engine.WarnInterpreterOnly", "false").build()) {
      Source source = Source.create("js", content);
      ctx.parse(source);
      return List.of();
    } catch (PolyglotException polyglotException) {
      if (polyglotException.isSyntaxError()) {
        SourceSection loc = polyglotException.getSourceLocation();
        int line = (loc != null) ? loc.getStartLine() : -1;
        int column = (loc != null) ? loc.getStartColumn() : -1;
        return List.of(
            new JsSyntaxError(scriptIndex, line, column, polyglotException.getMessage()));
      }
      // Non-syntax polyglot error — log and treat as no error
      log.warn(
          "JsSyntaxValidator: non-syntax polyglot error for script#{}: {}",
          scriptIndex,
          polyglotException.getMessage());
      return List.of();
    } catch (Exception exception) {
      log.warn(
          "JsSyntaxValidator: parser exception for script#{}: {}",
          scriptIndex,
          exception.getMessage());
      return List.of();
    }
  }
}
