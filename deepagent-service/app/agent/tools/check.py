"""`check_dashboard` tool — lints `dashboard.html` in connector mode: a syntax pass (every
inline `<script>` through `node --check`) and a contract pass (regex/scanner checks against the
`mcp()` data contract documented in `skills/mcp-data-dashboard/SKILL.md`). Never raises — every
failure mode (missing file, node unavailable, malformed script) becomes a text finding or an
overall fallback message, matching the never-raise contract the other agent tools follow (see
`app.agent.tools.data`).

This is a best-effort scanner, not a JS/HTML parser: it uses regexes and a small bracket/string
aware tokenizer rather than a real parser. Each simplification is called out at its use site.
"""

import logging
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass

from langchain_core.tools import BaseTool, tool

from app.agent.connectors.model import Connector
from app.engine.replay_manifest import load_landings
from app.engine.workspace import SessionWorkspace

logger = logging.getLogger(__name__)

_NODE_CHECK_TIMEOUT_SECONDS = 10

_DASHBOARD_NOT_FOUND_MESSAGE = "dashboard.html not found — write it first"

# Every <script ...>...</script>, src attribute captured separately below. Non-greedy content
# group + DOTALL so multi-line inline scripts match. Known limitation: a literal "</script"
# inside a JS string or comment inside the block would end the match early — dashboards built
# from the skill's examples never embed that substring, so this is not worth a real HTML parser.
_SCRIPT_TAG_PATTERN = re.compile(r"<script\b([^>]*)>(.*?)</script\s*>", re.IGNORECASE | re.DOTALL)
_SRC_ATTRIBUTE_PATTERN = re.compile(r"""src\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.IGNORECASE)

# `mcp(` call sites. Word boundary keeps this off identifiers merely ending in "mcp" (e.g. a
# hypothetical `startMcp(`); it does not exclude `obj.mcp(`, which is not a real pattern in this
# skill's contract (mcp is always a bare global call).
_MCP_CALL_PATTERN = re.compile(r"\bmcp\s*\(")
_ECHARTS_INIT_PATTERN = re.compile(r"echarts\.init\s*\(")

_STRING_LITERAL_PATTERN = re.compile(r"""^('[^']*'|"[^"]*")$""")
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_$][\w$]*$")

_STRING_QUOTE_CHARACTERS = ("'", '"', "`")

# (literal token, finding message) — literal substring matching, not tokenized JS. This can
# false-positive on the token appearing inside an unrelated string or comment; kept simple
# per the scanner's design brief rather than building a JS lexer for a lint tool.
_FORBIDDEN_TOKENS: tuple[tuple[str, str], ...] = (
    ("fetch(", "forbidden token 'fetch(' — network calls are brokered via mcp(), not fetch"),
    ("XMLHttpRequest", "forbidden token 'XMLHttpRequest' — network calls are brokered via mcp()"),
    ("WebSocket(", "forbidden token 'WebSocket(' — network calls are brokered via mcp()"),
    ("EventSource(", "forbidden token 'EventSource(' — network calls are brokered via mcp()"),
    ("window.parent", "forbidden token 'window.parent' — the page has no state outside itself"),
    ("window.top", "forbidden token 'window.top' — the page has no state outside itself"),
    ("postMessage(", "forbidden token 'postMessage(' — the page has no state outside itself"),
    ("localStorage", "forbidden token 'localStorage' — the page has no state outside itself"),
    ("sessionStorage", "forbidden token 'sessionStorage' — the page has no state outside itself"),
    ("document.cookie", "forbidden token 'document.cookie' — the page has no state outside itself"),
    ("typeof mcp", "forbidden token 'typeof mcp' — mcp is host-provided, never feature-test it"),
    ("function mcp(", "forbidden token 'function mcp(' — mcp is host-provided, never define it"),
    ("window.mcp =", "forbidden token 'window.mcp =' — mcp is host-provided, never define it"),
    ("const mcp", "forbidden token 'const mcp' — mcp is host-provided, never define it"),
    ("let mcp", "forbidden token 'let mcp' — mcp is host-provided, never define it"),
    ("var mcp", "forbidden token 'var mcp' — mcp is host-provided, never define it"),
    (
        "__ERD_RESULTS__",
        "forbidden token '__ERD_RESULTS__' — wrong mode; connector mode has no pre-injected results",
    ),
)


@dataclass(frozen=True)
class _ScriptBlock:
    has_src: bool
    src: str | None
    tag_line: int
    tag_start_offset: int
    content: str
    content_start_offset: int


def build_check_tools(
    workspace: SessionWorkspace, connectors: Sequence[Connector]
) -> list[BaseTool]:
    """One tool, `check_dashboard` (no args): reads `dashboard.html` from the workspace root and
    returns a plain-text lint report. Registered only in connector mode — see
    `app.agent.chat_turn.ChatTurn.prepare`."""

    @tool("check_dashboard")
    def check_dashboard_tool() -> str:
        """Lint dashboard.html: syntax-check every inline <script> and validate the mcp() call
        contract (literal connector/tool, arg keys matching a call actually made this session,
        forbidden APIs, CDN whitelist, 'erd' ECharts theme). Run this after every write_file or
        edit_file of dashboard.html and fix every finding before answering the user."""
        try:
            return _check_dashboard(workspace, connectors)
        except Exception as error:  # noqa: BLE001 -- never-raise contract, forward as actionable text
            logger.warning("check_dashboard failed unexpectedly: %s", type(error).__name__)
            return f"check_dashboard failed unexpectedly: {type(error).__name__}"

    return [check_dashboard_tool]


def _check_dashboard(workspace: SessionWorkspace, connectors: Sequence[Connector]) -> str:
    if not workspace.dashboard_path.exists():
        return _DASHBOARD_NOT_FOUND_MESSAGE

    html_text = workspace.dashboard_path.read_text(encoding="utf-8")
    script_blocks = _extract_script_blocks(html_text)

    findings: list[tuple[int, str, str]] = []
    findings.extend(_run_syntax_pass(script_blocks))
    findings.extend(_run_contract_pass(html_text, script_blocks, connectors, workspace))
    return _render_report(findings)


def _render_report(findings: list[tuple[int, str, str]]) -> str:
    if not findings:
        return "OK: no findings"
    ordered_findings = sorted(findings, key=lambda finding: finding[0])
    report_lines = [f"{len(ordered_findings)} finding(s):"]
    report_lines.extend(
        f"- [{kind}] line {line}: {message}" for line, kind, message in ordered_findings
    )
    return "\n".join(report_lines)


def _html_line(html_text: str, offset: int) -> int:
    return html_text.count("\n", 0, offset) + 1


def _extract_script_blocks(html_text: str) -> list[_ScriptBlock]:
    blocks: list[_ScriptBlock] = []
    for match in _SCRIPT_TAG_PATTERN.finditer(html_text):
        attributes_text = match.group(1)
        content = match.group(2)
        src_match = _SRC_ATTRIBUTE_PATTERN.search(attributes_text)
        src_value = (src_match.group(1) or src_match.group(2)) if src_match else None
        blocks.append(
            _ScriptBlock(
                has_src=src_match is not None,
                src=src_value,
                tag_line=_html_line(html_text, match.start()),
                tag_start_offset=match.start(),
                content=content,
                content_start_offset=match.start(2),
            )
        )
    return blocks


# -- syntax pass -------------------------------------------------------------------------------


def _run_syntax_pass(script_blocks: list[_ScriptBlock]) -> list[tuple[int, str, str]]:
    inline_blocks = [
        block for block in script_blocks if not block.has_src and block.content.strip()
    ]
    if not inline_blocks:
        return []
    if shutil.which("node") is None:
        return [
            (
                0,
                "syntax",
                "syntax check unavailable (node not installed); contract checks still ran",
            )
        ]
    findings: list[tuple[int, str, str]] = []
    for block in inline_blocks:
        findings.extend(_check_block_syntax(block))
    return findings


def _check_block_syntax(block: _ScriptBlock) -> list[tuple[int, str, str]]:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".js", delete=False, encoding="utf-8"
    ) as temp_file:
        temp_file.write(block.content)
        temp_path = temp_file.name
    try:
        try:
            result = subprocess.run(
                ["node", "--check", temp_path],
                capture_output=True,
                text=True,
                timeout=_NODE_CHECK_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return [(block.tag_line, "syntax", "syntax check timed out")]
        if result.returncode == 0:
            return []
        return [_parse_node_error(block, temp_path, result.stderr)]
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass


def _parse_node_error(block: _ScriptBlock, temp_path: str, stderr: str) -> tuple[int, str, str]:
    # node --check reports "<path>:<line>" on the first line of a syntax error. The reported line
    # is relative to the temp file (which holds only the inline script body), so the HTML line is
    # approximated by adding the offset of the <script> tag itself — a documented simplification,
    # not an exact HTML line (the true offset also depends on whether the body starts on the tag's
    # own line or the next one).
    line_match = re.search(rf"{re.escape(temp_path)}:(\d+)", stderr)
    stderr_lines = [line for line in stderr.splitlines() if line.strip()]
    message_line = next(
        (line for line in stderr_lines if "Error" in line),
        stderr_lines[-1] if stderr_lines else "syntax error",
    )
    html_line = block.tag_line + int(line_match.group(1)) if line_match else block.tag_line
    return (html_line, "syntax", message_line.strip())


# -- bracket/string aware mini-scanner (shared by the contract checks below) ------------------
#
# JS argument lists and object literals can nest (), {}, [] and string literals arbitrarily
# (`mcp('a', 'b', { tags: ['x,y'] }, cb)`), so a plain split(",") breaks. These helpers give the
# minimum needed: find a bracket's match, and split/scan text while treating nested
# brackets/strings as opaque. This is not a JS tokenizer — template literal interpolation
# (`${...}`) and regex literals are not specially handled, matching the "simple, well-commented"
# scanner brief.

_BRACKET_PAIRS = {"(": ")", "{": "}", "[": "]"}
_BRACKET_CLOSERS = set(_BRACKET_PAIRS.values())


def _skip_string(text: str, index: int) -> int:
    """`text[index]` is an opening quote; returns the index just past the matching close quote,
    honoring backslash escapes. Unterminated strings return `len(text)` (best-effort)."""
    quote_character = text[index]
    position = index + 1
    while position < len(text):
        character = text[position]
        if character == "\\":
            position += 2
            continue
        if character == quote_character:
            return position + 1
        position += 1
    return len(text)


def _find_matching_bracket(text: str, open_index: int) -> int | None:
    """`text[open_index]` is one of `({[`; returns the index of its matching close bracket, or
    `None` if unmatched before the end of text. Uses a bracket stack so differently-typed nested
    brackets (`{[()]}`) resolve correctly; string literals are skipped as opaque."""
    bracket_stack = [_BRACKET_PAIRS[text[open_index]]]
    position = open_index + 1
    while position < len(text) and bracket_stack:
        character = text[position]
        if character in _STRING_QUOTE_CHARACTERS:
            position = _skip_string(text, position)
            continue
        if character in _BRACKET_PAIRS:
            bracket_stack.append(_BRACKET_PAIRS[character])
        elif character in _BRACKET_CLOSERS:
            if character == bracket_stack[-1]:
                bracket_stack.pop()
                if not bracket_stack:
                    return position
            else:
                # Mismatched bracket type in source -- tolerate rather than abort, this is a
                # best-effort scanner, not a validator of the surrounding JS.
                bracket_stack.pop()
        position += 1
    return None


def _split_top_level(text: str) -> list[str]:
    """Splits `text` on commas that are not nested inside `()`, `{}`, `[]`, or a string
    literal. Used both for a call's top-level arguments and an object literal's top-level
    key/value pairs."""
    segments: list[str] = []
    bracket_stack: list[str] = []
    segment_start = 0
    position = 0
    while position < len(text):
        character = text[position]
        if character in _STRING_QUOTE_CHARACTERS:
            position = _skip_string(text, position)
            continue
        if character in _BRACKET_PAIRS:
            bracket_stack.append(_BRACKET_PAIRS[character])
        elif character in _BRACKET_CLOSERS and bracket_stack and character == bracket_stack[-1]:
            bracket_stack.pop()
        elif character == "," and not bracket_stack:
            segments.append(text[segment_start:position])
            segment_start = position + 1
        position += 1
    segments.append(text[segment_start:])
    return segments


def _find_top_level_colon(text: str) -> int | None:
    bracket_stack: list[str] = []
    position = 0
    while position < len(text):
        character = text[position]
        if character in _STRING_QUOTE_CHARACTERS:
            position = _skip_string(text, position)
            continue
        if character in _BRACKET_PAIRS:
            bracket_stack.append(_BRACKET_PAIRS[character])
        elif character in _BRACKET_CLOSERS and bracket_stack and character == bracket_stack[-1]:
            bracket_stack.pop()
        elif character == ":" and not bracket_stack:
            return position
        position += 1
    return None


def _parse_key_name(raw_key_text: str) -> str | None:
    if (
        len(raw_key_text) >= 2
        and raw_key_text[0] == raw_key_text[-1]
        and raw_key_text[0]
        in (
            "'",
            '"',
        )
    ):
        return raw_key_text[1:-1]
    if _IDENTIFIER_PATTERN.match(raw_key_text):
        return raw_key_text
    return None


def _extract_object_keys(object_inner_text: str) -> set[str]:
    """`object_inner_text` is the text strictly between an object literal's outer `{` and `}`.
    Returns the set of top-level (depth-1) keys — identifier or quoted string before a `:`.
    Segments that don't parse as `key: value` (computed keys, spreads, trailing commas) are
    silently skipped, matching the scanner's simple/best-effort brief."""
    observed_keys: set[str] = set()
    for segment in _split_top_level(object_inner_text):
        if not segment.strip():
            continue
        colon_index = _find_top_level_colon(segment)
        if colon_index is None:
            continue
        key_name = _parse_key_name(segment[:colon_index].strip())
        if key_name is not None:
            observed_keys.add(key_name)
    return observed_keys


# -- contract pass -------------------------------------------------------------------------------


def _group_landings_by_pair(landings: list[dict]) -> dict[tuple[str, str], list[frozenset[str]]]:
    grouped: dict[tuple[str, str], list[frozenset[str]]] = {}
    for landing in landings:
        pair_key = (landing.get("connector_id"), landing.get("tool_name"))
        landing_args = landing.get("args") or {}
        grouped.setdefault(pair_key, []).append(frozenset(landing_args.keys()))
    return grouped


def _run_contract_pass(
    html_text: str,
    script_blocks: list[_ScriptBlock],
    connectors: Sequence[Connector],
    workspace: SessionWorkspace,
) -> list[tuple[int, str, str]]:
    findings: list[tuple[int, str, str]] = []
    inline_blocks = [block for block in script_blocks if not block.has_src]
    combined_inline_text = "".join(block.content for block in inline_blocks)

    connector_tool_names = {
        connector.connector_id: {connector_tool.name for connector_tool in connector.tools}
        for connector in connectors
    }
    landings_by_pair = _group_landings_by_pair(load_landings(workspace))

    mcp_call_found = False
    for block in inline_blocks:
        for match in _MCP_CALL_PATTERN.finditer(block.content):
            mcp_call_found = True
            findings.extend(
                _check_mcp_call(html_text, block, match, connector_tool_names, landings_by_pair)
            )
        findings.extend(_check_forbidden_tokens(html_text, block))
        findings.extend(_check_echarts_theme(html_text, block))

    for block in script_blocks:
        if block.has_src:
            findings.extend(_check_script_src(html_text, block))

    if mcp_call_found and ".error" not in combined_inline_text:
        findings.append((0, "contract", "no handler checks r.error"))
    if not mcp_call_found:
        findings.append(
            (
                0,
                "contract",
                "dashboard has no mcp() call — in connector mode data must come from mcp()",
            )
        )

    return findings


def _check_mcp_call(
    html_text: str,
    block: _ScriptBlock,
    match: re.Match,
    connector_tool_names: dict[str, set[str]],
    landings_by_pair: dict[tuple[str, str], list[frozenset[str]]],
) -> list[tuple[int, str, str]]:
    findings: list[tuple[int, str, str]] = []
    call_line = _html_line(html_text, block.content_start_offset + match.start())

    open_paren_index = match.end() - 1
    close_paren_index = _find_matching_bracket(block.content, open_paren_index)
    if close_paren_index is None:
        findings.append((call_line, "contract", "could not parse mcp() call arguments"))
        return findings

    raw_arguments = _split_top_level(block.content[open_paren_index + 1 : close_paren_index])
    arguments = [argument for argument in raw_arguments if argument.strip()]
    if len(arguments) < 3:
        findings.append(
            (
                call_line,
                "contract",
                "mcp() call has too few arguments (expected connector id, tool name, args object)",
            )
        )
        return findings

    connector_argument = arguments[0].strip()
    tool_argument = arguments[1].strip()
    args_argument = arguments[2].strip()

    connector_literal_ok = bool(_STRING_LITERAL_PATTERN.match(connector_argument))
    tool_literal_ok = bool(_STRING_LITERAL_PATTERN.match(tool_argument))
    if not connector_literal_ok or not tool_literal_ok:
        findings.append(
            (call_line, "contract", "connector id and tool name must be string literals")
        )

    if not args_argument.startswith("{"):
        findings.append((call_line, "contract", "args must be an object literal at the call site"))

    if not (connector_literal_ok and tool_literal_ok):
        return findings

    connector_id = connector_argument[1:-1]
    tool_name = tool_argument[1:-1]

    if connector_id not in connector_tool_names:
        available_connectors = ", ".join(sorted(connector_tool_names)) or "(none)"
        message = (
            f"connector '{connector_id}' is not available this session — available "
            f"connectors: {available_connectors}"
        )
        findings.append((call_line, "contract", message))
        return findings
    if tool_name not in connector_tool_names[connector_id]:
        available_tools = ", ".join(sorted(connector_tool_names[connector_id])) or "(none)"
        message = (
            f"tool '{tool_name}' is not a tool of connector '{connector_id}' — available "
            f"tools: {available_tools}"
        )
        findings.append((call_line, "contract", message))
        return findings

    if not args_argument.startswith("{"):
        return findings

    object_close_index = _find_matching_bracket(args_argument, 0)
    if object_close_index is None:
        findings.append((call_line, "contract", "could not parse args object literal"))
        return findings

    observed_keys = _extract_object_keys(args_argument[1:object_close_index])
    landed_key_sets = landings_by_pair.get((connector_id, tool_name), [])
    if not landed_key_sets:
        findings.append(
            (
                call_line,
                "contract",
                "tool was never called (landed) in this session — call it first",
            )
        )
    elif frozenset(observed_keys) not in landed_key_sets:
        observed_sets_text = "; ".join(
            "{" + ", ".join(sorted(key_set)) + "}" for key_set in dict.fromkeys(landed_key_sets)
        )
        call_keys_text = ", ".join(sorted(observed_keys)) or "(none)"
        message = (
            f"args keys {{{call_keys_text}}} do not match any landed call — observed key "
            f"sets: {observed_sets_text}"
        )
        findings.append((call_line, "contract", message))
    return findings


def _check_forbidden_tokens(html_text: str, block: _ScriptBlock) -> list[tuple[int, str, str]]:
    findings: list[tuple[int, str, str]] = []
    for token, message in _FORBIDDEN_TOKENS:
        search_start = 0
        while True:
            found_index = block.content.find(token, search_start)
            if found_index == -1:
                break
            line = _html_line(html_text, block.content_start_offset + found_index)
            findings.append((line, "contract", message))
            search_start = found_index + len(token)
    return findings


def _check_echarts_theme(html_text: str, block: _ScriptBlock) -> list[tuple[int, str, str]]:
    findings: list[tuple[int, str, str]] = []
    for match in _ECHARTS_INIT_PATTERN.finditer(block.content):
        line = _html_line(html_text, block.content_start_offset + match.start())
        open_paren_index = match.end() - 1
        close_paren_index = _find_matching_bracket(block.content, open_paren_index)
        if close_paren_index is None:
            findings.append((line, "contract", "echarts.init() second argument must be 'erd'"))
            continue
        arguments = [
            argument.strip()
            for argument in _split_top_level(
                block.content[open_paren_index + 1 : close_paren_index]
            )
            if argument.strip()
        ]
        if len(arguments) < 2 or arguments[1] not in ("'erd'", '"erd"'):
            findings.append((line, "contract", "echarts.init() second argument must be 'erd'"))
    return findings


def _check_script_src(html_text: str, block: _ScriptBlock) -> list[tuple[int, str, str]]:
    src_value = block.src or ""
    if src_value == "https://cdn.tailwindcss.com" or src_value.startswith(
        "https://cdn.jsdelivr.net/npm/echarts@"
    ):
        return []
    line = _html_line(html_text, block.tag_start_offset)
    message = (
        f"disallowed script src '{src_value}' — only https://cdn.tailwindcss.com or "
        "https://cdn.jsdelivr.net/npm/echarts@* is allowed"
    )
    return [(line, "contract", message)]
