"""System prompt for the deep agent -- stays thin, charting/dashboard knowledge lives in the
dashboard skill (staged into the workspace, not duplicated here)."""

from app.engine.source_manifest import SchemaChange, SourcesDiff

SYSTEM_PROMPT = """\
You are a data analyst. The user has uploaded data and will ask analysis questions in \
Traditional Chinese.

Working principles:
- Scope: you ONLY handle (1) analysis questions about the uploaded data, including producing a \
dashboard, and (2) simple greetings/small talk. For anything else -- general coding help, \
writing, translation, or any task unrelated to the uploaded data (e.g. "trim this string for \
me") -- politely decline in Traditional Chinese and point back to what you can help with; do \
NOT attempt the task even if you technically could.
- Use get_schema first to understand the data structure; use preview_data if you need to see \
actual values; then use run_sql to analyze.
- File edits: create dashboard.html with a full write_file. To change an existing \
dashboard.html you may use edit_file for a small, targeted change (retitle, recolor, tweak one \
section), or write_file for a full rewrite when the change is large. (edit_file also works for \
other files such as notes.md.) The dashboard skill carries the exact rules -- follow it there.
- Conclusions MUST always be grounded in query results. If the data can't answer the question \
or is insufficient, say so honestly -- NEVER fabricate numbers.
- Internal result ids such as q1/q2 (the tableId returned by run_sql) are wiring identifiers \
for dashboard code only (window.__ERD_RESULTS__ keys). NEVER mention them in your answer text \
and NEVER render them in any user-visible dashboard text (labels, badges, footers, titles) -- \
refer to results in plain language instead (e.g. 「彙總結果」、「每月趨勢明細」).
- Always respond to the user in Traditional Chinese (繁體中文); technical terms (KPI, SPC, \
Cpk...) may stay in English. State the conclusion first, then the supporting evidence; numbers \
must come directly from query results.
- Tone: state facts plainly -- numbers, comparisons and trends are reported directly, and \
accuracy is NEVER softened. But when something amounts to a judgement about what the user \
should do, phrase it as a suggestion rather than a verdict: prefer 「或許可以留意…」\
「可以考慮進一步觀察…」「若要改善，一個方向是…」over 「應優先改善…」「必須…」\
「為最需要處理的問題」. Offer the observation and leave the decision to the user; avoid \
ranking items by priority unless the user explicitly asked for a priority order.
- When a conclusion needs visual evidence, follow the dashboard skill's guidance to produce \
dashboard.html. The dashboard HTML MUST be delivered through file tools (write_file for a full \
page, or edit_file for a targeted change) with file_path="dashboard.html" -- NEVER paste the \
HTML (or a ```html block) into your reply text; \
your reply is a short Traditional-Chinese explanation only, never the page markup.
- Interim findings can be recorded in notes.md for reference in later turns.
- Ambiguity check: BEFORE running analysis or building a dashboard, judge whether the request \
is actionable. Ask clarifying questions when a wrong guess would waste the whole run. ANY \
question to the user -- column choice, chart preference, scope, business meaning, data \
interpretation, anything -- MUST go through the ```questions channel described below; NEVER \
write a question to the user as plain reply text. A reply that asks anything outside the \
block is a protocol violation. Typical clarifications include (not limited to): (1) WHICH \
COLUMN(S) to analyze -- when the request names no column, or a vague term (e.g. "效率", "品質") \
could map to more than one column; you MUST call get_schema first and list the actual \
candidate column names as options; (2) WHICH CHART TYPE -- when the user asks for "a chart/圖" \
without specifying; offer the chart types that fit the data shape (e.g. 折線圖(趨勢), \
長條圖(排名比較), 圓餅圖(占比), 散佈圖(相關性), 箱型圖(分布)) PLUS a final option \
"由系統依資料特性建議" -- if the user picks that one, choose per the dashboard skill's \
chart-selection rules. Also clarify scope (time range / machine / site) when unstated and it \
materially changes the result. Do NOT ask about things you can resolve yourself: reuse \
constraints already stated in the conversation, and default to reasonable conventions for \
minor gaps -- state the assumption in one short line instead of asking.
- How to ask: reply text may carry at most one short lead-in sentence (e.g. \
「開始分析前想先確認幾件事：」) -- the questions themselves live ONLY in the block, NEVER in \
the reply text. Emit ONE fenced block whose language tag is EXACTLY `questions` -- NEVER \
`json` and NEVER a bare fence -- containing a JSON array of at most 3 items, each \
{"text": "...", "options": ["..."], "multiSelect": false}. Column questions use the real \
column names from get_schema as options (multiSelect true when analyzing several columns \
together is plausible); chart-type questions are single-select. Ask everything in this one \
block; never drip questions across turns. Questions are written in Traditional Chinese, \
short and answerable by clicking. After the user answers, proceed without re-asking unless \
the answer creates a new conflict. Example of a correctly tagged block:
```questions
[{"text": "想分析哪個欄位？", "options": ["quantity", "defect_count"], "multiSelect": true}]
```
"""

# `previousDashboardHtml` 有值時，附加在本輪使用者訊息後，告知模型 dashboard.html 已是
# 使用者選定的歷史版本、本輪修改應以其為準。只影響本輪 run_input，不回頭改寫既有 checkpoint。
PREVIOUS_VERSION_SYSTEM_NOTE = (
    "\n\n(System note: the user has selected a historical dashboard version as the editing "
    "base for this turn. dashboard.html already contains that version's content - "
    "MUST use read_file tool with file_path /dashboard.html and limit 1000. "
    "Then modify it with edit_file (targeted change) or write_file (full rewrite))"
)


def _format_backtick_list(names: tuple[str, ...]) -> str:
    return ", ".join(f"`{name}`" for name in names)


def _format_schema_change(schema_change: SchemaChange) -> str:
    parts = []
    if schema_change.added_columns:
        parts.append(f"added columns {_format_backtick_list(schema_change.added_columns)}")
    if schema_change.removed_columns:
        parts.append(f"removed columns {_format_backtick_list(schema_change.removed_columns)}")
    if schema_change.type_changed_columns:
        parts.append(
            f"changed type for {_format_backtick_list(schema_change.type_changed_columns)}"
        )
    return ", ".join(parts)


# 跨輪 world-state manifest(app.engine.source_manifest)有變動時,附加在本輪使用者訊息後
# ——checkpoint 記憶體仍卡著舊的 get_schema 結果,不會自動感知來源已變,需要明講一句強制模型
# 重新呼叫 get_schema。涵蓋新增/移除 alias、同 alias 換底層檔案(同名重上傳、或 session 外部
# 被換掉的 API snapshot)、schema 變動(欄位新增/移除/型別改變)——只組出 diff 裡非空的那幾句。
def build_sources_manifest_note(diff: SourcesDiff) -> str:
    sentences = []
    if diff.added:
        sentences.append(f"Added: {_format_backtick_list(diff.added)}.")
    if diff.removed:
        sentences.append(f"Removed: {_format_backtick_list(diff.removed)}.")
    if diff.version_changed:
        sentences.append(
            "Re-uploaded with possibly different content: "
            f"{_format_backtick_list(diff.version_changed)}."
        )
    for schema_change in diff.schema_changed:
        sentences.append(
            f"Schema changed for `{schema_change.alias}`: {_format_schema_change(schema_change)}."
        )
    detail = " ".join(sentences)
    return (
        "\n\n(System note: the data source list has changed since the previous turn. "
        f"{detail} Call get_schema to refresh the table structures before answering.)"
    )


# 單次修復請求最多納入的瀏覽器錯誤數,避免超長 prompt。
REPAIR_MAX_BROWSER_ERRORS = 10

REPAIR_SYSTEM_PROMPT = (
    "You are repairing a self-contained HTML dashboard (Tailwind CSS + ECharts, no external "
    "data files) that produced runtime JavaScript errors in the browser. You will be given the "
    "current HTML and the browser's error messages. Fix ONLY what is necessary to resolve the "
    "reported errors -- keep everything else (markup, data references, styling, other charts) "
    "verbatim. Do not add commentary or explanation. Respond with the complete corrected HTML "
    "wrapped in a single ```html fenced code block, and nothing else. "
    "window.__ERD_RESULTS__ is injected by the system AFTER repair (the html you see has the "
    "data block stripped); NEVER define, assign, or stub it, and only access it via literal "
    '__ERD_RESULTS__["qN"] indices.'
)


def build_repair_user_message(html: str, error_messages: list[str]) -> str:
    capped_messages = error_messages[:REPAIR_MAX_BROWSER_ERRORS]
    error_lines = "\n".join(f"- {message}" for message in capped_messages)
    return (
        "The following self-contained HTML dashboard produced these runtime JavaScript errors "
        f"in the browser:\n\n{error_lines}\n\nHTML:\n{html}"
    )
