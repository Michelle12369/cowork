"""System prompt for the deep agent -- stays thin, charting/dashboard knowledge lives in the
dashboard skill (staged into the workspace, not duplicated here)."""

from app.engine.api_registry import ApiDefinition, ApiParameter
from app.engine.api_snapshot import SnapshotMeta
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
- File edits: to produce or change dashboard.html you MUST call write_file with the complete \
file (a full rewrite) EVERY time, even for a one-line tweak -- edit_file on dashboard.html is \
rejected. (edit_file may still be used for other files such as notes.md.) The dashboard skill \
carries the exact rules -- follow it there.
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
dashboard.html. The dashboard HTML MUST be delivered by calling write_file with \
file_path="dashboard.html" -- NEVER paste the HTML (or a ```html block) into your reply text; \
your reply is a short Traditional-Chinese explanation only, never the page markup.
- Interim findings can be recorded in notes.md for reference in later turns.
- API datasources: the system message may end with an "API datasources" context listing \
available (not yet fetched) and fetched APIs. To use an available API you MUST first collect \
every required parameter, then call fetch_api_data(source_id, params). If required parameters \
are missing and the conversation does not imply their values, ask the user by emitting a \
fenced block in your reply text whose language tag is EXACTLY `questions` -- NEVER `json` and \
NEVER a bare fence -- containing a JSON array where each element is \
{"text": "...", "options": ["..."], "multiSelect": false}: one question per missing parameter, \
all missing parameters in one block, list the parameter's options when the context shows them \
(e.g. "options: 7d, 30d, 90d"). Do not invent an "other" option; the UI adds free-form input \
automatically. Example of a correctly tagged block:
```questions
[{"text": "想查詢哪個時間區間的訂單？", "options": ["7d", "30d", "90d"], "multiSelect": false}]
```
- Re-fetching: to change parameters (e.g. 30d -> 90d) do a partial update -- reuse the current \
params shown in the fetched list for anything the user did not mention, then call \
fetch_api_data again. "Refresh the data" means calling fetch_api_data again with the same \
params. Calling fetch_api_data always fetches; it never deduplicates.
"""


def _format_api_parameter_summary(parameter: ApiParameter) -> str:
    required_or_optional = "required" if parameter.required else "optional"
    detail = f"{required_or_optional}, multi" if parameter.multi else required_or_optional
    if parameter.options:
        detail = f"{detail}; options: {', '.join(parameter.options)}"
    return f"{parameter.name} ({detail})"


def _format_api_param_value(value: object) -> str:
    if isinstance(value, list):
        return ",".join(str(item) for item in value)
    return str(value)


# API 資料源可用/已取數清單,附加在 SYSTEM_PROMPT 尾端(見 chat_turn 接線)——不含路徑/uuid,
# 只列 alias、顯示名、參數摘要、取數時間,讓模型知道哪些 API 可 fetch_api_data、哪些已掛表
# 可部分更新重抓。unfetched 依 registry 插入順序,fetched 依 alias 排序(輸出穩定,便於比對)。
def build_api_sources_context(
    registry: dict[str, ApiDefinition], snapshots: list[SnapshotMeta]
) -> str:
    if not registry:
        return ""
    fetched_by_alias = {snapshot.alias: snapshot for snapshot in snapshots}
    unfetched_definitions = [
        definition for definition in registry.values() if definition.alias not in fetched_by_alias
    ]
    fetched_snapshots = sorted(snapshots, key=lambda snapshot: snapshot.alias)

    sections = []
    if unfetched_definitions:
        lines = [
            "Available API datasources (call fetch_api_data after collecting required params):"
        ]
        for definition in unfetched_definitions:
            param_summary = ", ".join(
                _format_api_parameter_summary(parameter) for parameter in definition.parameters
            )
            lines.append(f"- `{definition.alias}` — {definition.name}; params: {param_summary}")
        sections.append("\n".join(lines))
    if fetched_snapshots:
        lines = [
            (
                "Fetched API datasources (already mounted as tables; params shown for "
                "partial re-fetch):"
            )
        ]
        for snapshot in fetched_snapshots:
            param_summary = ", ".join(
                f"{name}={_format_api_param_value(value)}"
                for name, value in snapshot.params.items()
            )
            lines.append(
                f"- `{snapshot.alias}` — fetched {snapshot.fetched_at}; params: {param_summary}"
            )
        sections.append("\n".join(lines))
    return "\n\n" + "\n\n".join(sections)


# `previousDashboardHtml` 有值時，附加在本輪使用者訊息後，告知模型 dashboard.html 已是
# 使用者選定的歷史版本、本輪修改應以其為準。只影響本輪 run_input，不回頭改寫既有 checkpoint。
PREVIOUS_VERSION_SYSTEM_NOTE = (
    "\n\n(System note: the user has selected a historical dashboard version as the editing "
    "base for this turn. dashboard.html already contains that version's content - "
    "MUST use read_file tool with file_path /dashboard.html and limit 1000. "
    "MUST call write_file for modifying dashboard)"
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
    "wrapped in a single ```html fenced code block, and nothing else."
)


def build_repair_user_message(html: str, error_messages: list[str]) -> str:
    capped_messages = error_messages[:REPAIR_MAX_BROWSER_ERRORS]
    error_lines = "\n".join(f"- {message}" for message in capped_messages)
    return (
        "The following self-contained HTML dashboard produced these runtime JavaScript errors "
        f"in the browser:\n\n{error_lines}\n\nHTML:\n{html}"
    )
