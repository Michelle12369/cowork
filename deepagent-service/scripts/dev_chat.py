"""直打 deepagent `/chat` 的開發用 chat client——不需要起 Java/前端, 也身兼
`spike/mcp-shell/` 的驅動腳本(preflight、connector 模式首輪、失敗診斷都在這裡).

模擬 backend 的跨輪簿記: 自動維護 sessionId/history/sources/connectors/previousDashboardHtml,
把 CSV 排進 `uploads/` 佈局(resolve_source_path 的路徑形狀要求), SSE 事件即時印出, 原始 SSE
落成 `chat-<ts>.log`, DASHBOARD_HTML 落地成 `dashboard.html`. 狀態存在 `.dev-session/`
(gitignored), `--new` 開新對話.

設定來源(與 app/config.py 同一份 `one-local.properties`, 路徑看 `ONE_PROPERTIES_PATH`):
- 官方 key `AGENT_API_BEARER_TOKEN`/`SSO_TOKEN_HEADER`/`SSO_URL_HEADER` 一律經
  `app.config.get_settings()`(env > 檔案 > 預設, production 依賴這條, 不動).
- dev-only key `DEV_DEEPAGENT_URL`/`DEV_SSO_TOKEN`/`DEV_SSO_URL`/`DEV_CONNECTORS`(單行 JSON
  connector list)經 `scripts/dev_config.load_dev_config()`, 優先序是 CLI flag > 本檔 > 內建
  預設——這幾個 key NEVER 讀 env var; deepagent 位址預設 `http://127.0.0.1:8000`, 要換位址
  請改 `one-local.properties` 的 `DEV_DEEPAGENT_URL` 或直接 `--base-url`.
- `--verbose` 印出每個設定值實際來自哪一層(cli / env / properties / default)與讀的是哪個
  properties 檔; secrets(bearer token、SSO token/url)只印來源與有無, NEVER 印值.

認證與 connector:
- inbound bearer: `--token`(預設 `AGENT_API_BEARER_TOKEN`, 必須與 deepagent 端同值).
- MCP connector: 新 session 時 `DEV_CONNECTORS` 與 `--connector ID URL [NAME] [BEARER_TOKEN_KEY]`
  (可重複)合併, 同 id 以 CLI 覆蓋; `--no-connectors` 則捨棄 `DEV_CONNECTORS`, 只用 CLI 給的
  (檔案模式配 `--csv` 時常用). 續接輪的 connector 集合在首輪就固定, `--connector` 仍可加,
  不會重新套用 `DEV_CONNECTORS`.
  有 connector 時 deepagent 的 mcp_adapter 要求 SSO 兩個 header 非空, 用 `--sso-token`/
  `--sso-url`(預設讀 `DEV_SSO_TOKEN`/`DEV_SSO_URL`)給值, 不給就送 dummy 值(mock server 不檢查).
- token/SSO 值 NEVER 印出或寫進狀態檔.

POST 前會 preflight deepagent `/health` 與每個 connector 的 URL(任何 HTTP 狀態碼都算連得上,
只有連線層失敗才算不通), 失敗直接印診斷訊息並離開, 不送出這一輪.

用法:
    uv run scripts/dev_chat.py --csv ~/data.csv "哪個系統最需要改善?"            # 檔案模式首輪
    uv run scripts/dev_chat.py "Build a sales dashboard" \\
        --connector sales http://127.0.0.1:8765/mcp Sales   # connector 模式首輪(訊息放前面:
                                                             # --connector 吃可變長度清單)
    uv run scripts/dev_chat.py "改成圓餅圖"                                     # 後續輪自動帶狀態
    uv run scripts/dev_chat.py --new --csv ~/other.csv "換一份資料"             # 重開 session
    uv run scripts/dev_chat.py --state-dir spike/mcp-shell/out/.dev-session \\
        --dashboard-out spike/mcp-shell/out/dashboard.html "Build a sales dashboard"  # spike 用法
"""

import argparse
import json
import re
import shutil
import sys
import time
import urllib.parse
import uuid
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Settings, _properties_path, get_settings
from scripts.dev_config import (
    DEV_CONNECTORS,
    DEV_DEEPAGENT_URL,
    DEV_SSO_TOKEN,
    DEV_SSO_URL,
    SOURCE_CLI,
    SOURCE_DEFAULT,
    dev_key_sources,
    load_dev_config,
    official_key_source,
    properties_path_source,
)

DEFAULT_STATE_DIR = Path(__file__).resolve().parent.parent / ".dev-session"
DEFAULT_USER_ID = "dev-user"

# mcp_adapter._build_headers() 對任何 connector 都 require_sso_token()/require_sso_url(); mock
# server 不檢查, 所以沒給時用看得出是假的值頂著.
DUMMY_SSO_TOKEN = "dev-chat-sso-token"
DUMMY_SSO_URL = "https://sso.invalid/dev-chat"

FILE_TYPE_BY_SUFFIX = {".csv": "csv", ".xlsx": "xlsx", ".xls": "xlsx"}

STEP_STATUS_MARKS = {"RUNNING": "⏳", "SUCCESS": "✅", "ERROR": "❌"}

STATE_FILE_NAME = "state.json"
DASHBOARD_FILE_NAME = "dashboard.html"

PREFLIGHT_TIMEOUT_SECONDS = 5.0

AGENT_API_BEARER_TOKEN_KEY = "AGENT_API_BEARER_TOKEN"
SSO_TOKEN_HEADER_KEY = "SSO_TOKEN_HEADER"
SSO_URL_HEADER_KEY = "SSO_URL_HEADER"

# --verbose 表裡 secrets 的顯示: 只說有沒有值, NEVER 印值.
HIDDEN_VALUE = "(hidden)"
UNSET_VALUE = "(not set)"
NO_CONNECTORS_VALUE = "(none)"


@dataclass(frozen=True)
class ResolvedOption:
    """一個可用 CLI flag 覆寫的設定值, 連同它實際來自哪一層(cli/env/properties/default)."""

    value: str | None
    source: str


def resolve_option(
    cli_value: str | None, fallback_value: str | None, fallback_source: str
) -> ResolvedOption:
    """CLI flag 有給(即使是空字串)就是 cli; 否則用呼叫端算好的 fallback 值與其來源."""
    if cli_value is not None:
        return ResolvedOption(cli_value, SOURCE_CLI)
    return ResolvedOption(fallback_value, fallback_source)


@dataclass(frozen=True)
class ConfigSourceRow:
    """`--verbose` 表的一列: 設定名稱、來源、可以印的值(secrets 是 HIDDEN_VALUE/UNSET_VALUE)."""

    label: str
    source: str
    shown_value: str


def _secret_display(value: str | None) -> str:
    return HIDDEN_VALUE if value else UNSET_VALUE


def _connector_ids_display(connectors: list[dict[str, str | None]]) -> str:
    if not connectors:
        return NO_CONNECTORS_VALUE
    return "ids: " + ", ".join(str(connector["id"]) for connector in connectors)


def collect_config_source_rows(
    *,
    base_url: ResolvedOption,
    token: ResolvedOption,
    sso_token: ResolvedOption,
    sso_url: ResolvedOption,
    config_connectors: list[dict[str, str | None]],
    cli_connectors: list[dict[str, str | None]],
    settings: Settings,
) -> list[ConfigSourceRow]:
    """`--verbose` 要印的表: 每個設定值的來源(cli/env/properties/default)。secrets(bearer
    token、SSO token/url)只印來源與有無, NEVER 印值; connector 只印 id, NEVER 印 url(query
    string 可能藏 token)."""
    properties_file = _properties_path()
    properties_file_state = "exists" if properties_file.exists() else "missing"
    dev_sources = dev_key_sources()
    return [
        ConfigSourceRow(
            "ONE_PROPERTIES_PATH",
            properties_path_source(),
            f"{properties_file} ({properties_file_state})",
        ),
        ConfigSourceRow(f"--base-url ({DEV_DEEPAGENT_URL})", base_url.source, str(base_url.value)),
        ConfigSourceRow(
            f"--token ({AGENT_API_BEARER_TOKEN_KEY})", token.source, _secret_display(token.value)
        ),
        ConfigSourceRow(
            f"--sso-token ({DEV_SSO_TOKEN})", sso_token.source, _secret_display(sso_token.value)
        ),
        ConfigSourceRow(
            f"--sso-url ({DEV_SSO_URL})", sso_url.source, _secret_display(sso_url.value)
        ),
        ConfigSourceRow(
            DEV_CONNECTORS, dev_sources[DEV_CONNECTORS], _connector_ids_display(config_connectors)
        ),
        ConfigSourceRow(
            "--connector",
            SOURCE_CLI if cli_connectors else SOURCE_DEFAULT,
            _connector_ids_display(cli_connectors),
        ),
        ConfigSourceRow(
            SSO_TOKEN_HEADER_KEY,
            official_key_source(SSO_TOKEN_HEADER_KEY),
            settings.SSO_TOKEN_HEADER,
        ),
        ConfigSourceRow(
            SSO_URL_HEADER_KEY, official_key_source(SSO_URL_HEADER_KEY), settings.SSO_URL_HEADER
        ),
    ]


def _print_config_sources(rows: list[ConfigSourceRow]) -> None:
    label_width = max(len(row.label) for row in rows)
    source_width = max(len(row.source) for row in rows)
    print("ℹ️  設定來源(--verbose; secrets 只印來源不印值):")
    for row in rows:
        print(f"   {row.label:<{label_width}}  {row.source:<{source_width}}  {row.shown_value}")


def _alias_for(source_file: Path) -> str:
    """檔名 stem 轉成可當 SQL 資料表名的 alias: 小寫、非英數轉底線、開頭補字母."""
    alias = re.sub(r"[^a-z0-9]+", "_", source_file.stem.lower()).strip("_")
    if not alias or not alias[0].isalpha():
        alias = f"t_{alias}" if alias else "data"
    return alias


def parse_connector(tokens: list[str]) -> dict[str, str | None]:
    """`--connector ID URL [NAME] [BEARER_TOKEN_KEY]` 轉成 ChatRequest.connectors 的一筆."""
    if len(tokens) < 2 or len(tokens) > 4:
        raise ValueError(
            f"--connector 需要 2–4 個值: ID URL [NAME] [BEARER_TOKEN_KEY], 收到 {len(tokens)} 個"
        )
    connector_id, connector_url = tokens[0], tokens[1]
    if not connector_id or not connector_url:
        raise ValueError("--connector 的 ID 與 URL 不可為空")
    connector_name = tokens[2] if len(tokens) >= 3 and tokens[2] else connector_id.title()
    bearer_token_key = tokens[3] if len(tokens) == 4 and tokens[3] else None
    return {
        "id": connector_id,
        "name": connector_name,
        "url": connector_url,
        "bearerTokenKey": bearer_token_key,
    }


def merge_connectors(
    existing: list[dict[str, str | None]], incoming: list[dict[str, str | None]]
) -> list[dict[str, str | None]]:
    """同 id 以新值覆蓋, 其餘追加; 順序維持先 existing 後新 id."""
    merged_by_id: dict[str, dict[str, str | None]] = {
        str(connector["id"]): connector for connector in existing
    }
    for connector in incoming:
        merged_by_id[str(connector["id"])] = connector
    return list(merged_by_id.values())


def resolve_new_session_connectors(
    config_connectors: list[dict[str, str | None]],
    cli_connectors: list[dict[str, str | None]],
    *,
    use_config_connectors: bool,
) -> list[dict[str, str | None]]:
    """新 session(`--new` 或無既有狀態)的 connector 清單。`use_config_connectors=False`
    (`--no-connectors`)時只用 CLI 給的; 否則 `DEV_CONNECTORS` 與 CLI 合併, 同 id CLI 覆蓋."""
    if not use_config_connectors:
        return cli_connectors
    return merge_connectors(config_connectors, cli_connectors)


def _load_state(state_dir: Path) -> dict[str, Any] | None:
    state_path = state_dir / STATE_FILE_NAME
    if not state_path.exists():
        return None
    return json.loads(state_path.read_text(encoding="utf-8"))


def _save_state(state_dir: Path, state: dict[str, Any]) -> None:
    (state_dir / STATE_FILE_NAME).write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _stage_sources(state_dir: Path, session_id: str, csv_paths: list[str]) -> list[dict[str, str]]:
    """把資料檔複製進含 `uploads` 段的佈局(鏡射 backend 給 deepagent 的路徑形狀)."""
    sources: list[dict[str, str]] = []
    for raw_path in csv_paths:
        source_file = Path(raw_path).expanduser().resolve()
        if not source_file.exists():
            sys.exit(f"找不到資料檔: {source_file}")
        file_type = FILE_TYPE_BY_SUFFIX.get(source_file.suffix.lower())
        if file_type is None:
            sys.exit(f"不支援的副檔名: {source_file.suffix}(支援 csv/xlsx)")
        staged_path = state_dir / "uploads" / session_id / source_file.name
        staged_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_file, staged_path)
        sources.append(
            {"alias": _alias_for(source_file), "path": str(staged_path), "fileType": file_type}
        )
    return sources


def build_headers(
    bearer_token: str,
    has_connectors: bool,
    sso_token: str | None,
    sso_url: str | None,
    sso_token_header: str,
    sso_url_header: str,
) -> dict[str, str]:
    """組 `/chat` 的 header: 永遠帶 bearer; 有 connector 才帶 SSO 兩個 header."""
    headers = {"authorization": f"Bearer {bearer_token}"}
    if has_connectors:
        headers[sso_token_header] = sso_token or DUMMY_SSO_TOKEN
        headers[sso_url_header] = sso_url or DUMMY_SSO_URL
    return headers


def _connector_host(connector_url: str) -> str:
    """只取 host:port, NEVER 印整個 URL——query string 裡可能藏 token(DEV_CONNECTORS 的註解
    早就這麼說了)."""
    return urllib.parse.urlsplit(connector_url).netloc


def _preflight(base_url: str, connectors: list[dict[str, str | None]]) -> None:
    """POST 前的存活檢查: deepagent `/health` 必須 200; 每個 connector 的 URL 只要連線層通得過
    (任何 HTTP 狀態碼都算)就算過關. 任一項失敗直接印診斷並離開, 不送出這一輪."""
    timeout = httpx.Timeout(PREFLIGHT_TIMEOUT_SECONDS, connect=PREFLIGHT_TIMEOUT_SECONDS)
    with httpx.Client(timeout=timeout, trust_env=False) as health_client:
        try:
            health_response = health_client.get(f"{base_url}/health")
        except httpx.HTTPError as request_error:
            sys.exit(
                f"✗ deepagent /health 連不上({type(request_error).__name__}): {base_url}——"
                f"先跑 spike/mcp-shell/run-deepagent.sh, 或用 --base-url/one-local.properties "
                f"的 {DEV_DEEPAGENT_URL} 校正位址"
            )
        if health_response.status_code != 200:
            sys.exit(f"✗ deepagent /health 回 {health_response.status_code}(非 200): {base_url}")
        print(f"✓ deepagent /health 200 — {base_url}")

    # trust_env=True(與上面 /health 相反): deepagent 自己的 httpx/fastmcp client 打 connector
    # 時會吃 proxy 環境變數, 這裡的探測不該比真正打出去的呼叫更嚴格.
    with httpx.Client(timeout=timeout, trust_env=True) as connector_client:
        for connector in connectors:
            connector_id = connector["id"]
            connector_host = _connector_host(str(connector["url"]))
            try:
                connector_client.get(str(connector["url"]))
            except httpx.HTTPError as request_error:
                sys.exit(
                    f"✗ connector {connector_id} 連不上({type(request_error).__name__}): "
                    f"{connector_host}——MCP server 起了嗎?"
                    "(mock server: uv run python spike/mcp-shell/mock_server.py)"
                )
            print(f"✓ connector {connector_id} 連得上 — {connector_host}")


def _print_event(event: dict[str, Any], dashboard_path: Path) -> tuple[str | None, str | None]:
    """印出單一 wire 事件; 回傳 (answer_text, dashboard_html) 中本事件產出的部分."""
    event_type = event.get("type")
    if event_type == "STEP":
        mark = STEP_STATUS_MARKS.get(event.get("status", ""), "·")
        print(f"{mark} {event.get('title')}", flush=True)
    elif event_type == "TOKEN":
        print(event.get("delta", ""), end="", flush=True)
    elif event_type == "TABLE":
        rows = event.get("rows", [])
        print(f"▦ {event.get('tableId')} — {event.get('intent')}({len(rows)} rows)", flush=True)
    elif event_type == "QUESTION":
        print("\n❓ 模型反問:")
        for question in event.get("questions", []):
            options = "/".join(question.get("options", []))
            print(f"   - {question.get('text')}({options})")
    elif event_type == "ANSWER":
        answer_text = event.get("text", "")
        print(f"\n──── ANSWER ────\n{answer_text}")
        return answer_text, None
    elif event_type == "DASHBOARD_HTML":
        dashboard_html = event.get("html", "")
        dashboard_path.write_text(dashboard_html, encoding="utf-8")
        print(f"📊 dashboard 已更新 → {dashboard_path}", flush=True)
        return None, dashboard_html
    elif event_type == "ERROR":
        print(f"\n💥 ERROR [{event.get('code')}] {event.get('message')}")
    return None, None


def _stream_chat(
    base_url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    dashboard_path: Path,
    raw_log_path: Path,
) -> tuple[str | None, str | None]:
    """POST /chat 並即時消化 SSE; 原始行寫進 raw_log_path; 回傳 (最終 answer, 最終 dashboard html)."""
    answer_text: str | None = None
    dashboard_html: str | None = None
    timeout = httpx.Timeout(600.0, connect=10.0)
    with (
        httpx.Client(timeout=timeout, trust_env=False) as client,
        client.stream("POST", f"{base_url}/chat", json=payload, headers=headers) as response,
    ):
        if response.status_code == 401:
            sys.exit(
                "401 Unauthorized——bearer token 不符或 deepagent 端未設 AGENT_API_BEARER_TOKEN"
            )
        response.raise_for_status()
        # 狀態碼過了才開 raw log, 免得 401/5xx 留下空檔誤導事後診斷.
        with raw_log_path.open("w", encoding="utf-8") as raw_log:
            for line in response.iter_lines():
                raw_log.write(line + "\n")
                if not line.startswith("data:"):
                    continue  # comment ping/空行
                event = json.loads(line[len("data:") :].strip())
                event_answer, event_dashboard = _print_event(event, dashboard_path)
                answer_text = event_answer or answer_text
                dashboard_html = event_dashboard or dashboard_html
    return answer_text, dashboard_html


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="直打 deepagent /chat 的開發用 client(bearer auth + MCP connector)"
    )
    parser.add_argument("message", help="這一輪要對 agent 說的話")
    parser.add_argument("--csv", action="append", default=[], help="資料檔路徑(csv/xlsx, 可重複)")
    parser.add_argument(
        "--connector",
        action="append",
        nargs="+",
        default=[],
        metavar="TOKEN",
        help="MCP connector: ID URL [NAME] [BEARER_TOKEN_KEY], 可重複; 同 id 覆蓋既有(含 "
        "DEV_CONNECTORS 給的). 吃可變長度清單, 訊息請放在它前面",
    )
    parser.add_argument(
        "--no-connectors",
        action="store_true",
        help="新 session 時不套用 DEV_CONNECTORS, 只用 --connector 給的(檔案模式配 --csv 常用)",
    )
    parser.add_argument("--new", action="store_true", help="放棄現有 session 重新開始")
    parser.add_argument("--session-id", default=None, help="指定 sessionId(預設 dev-<8 hex>)")
    parser.add_argument("--user-id", default=DEFAULT_USER_ID, help="ChatRequest.userId")
    # 下面四個 flag 的 default 一律 None: main() 才疊上 one-local.properties/env/內建預設, 這樣
    # --verbose 分得出值是 CLI 給的還是落回哪一層.
    parser.add_argument(
        "--base-url",
        default=None,
        help=f"deepagent 服務位址(預設讀 one-local.properties 的 {DEV_DEEPAGENT_URL})",
    )
    parser.add_argument(
        "--state-dir", type=Path, default=DEFAULT_STATE_DIR, help="session 狀態資料夾"
    )
    parser.add_argument(
        "--token",
        default=None,
        help=f"inbound bearer token(預設讀 {AGENT_API_BEARER_TOKEN_KEY}: env > one-local.properties)",
    )
    parser.add_argument(
        "--sso-token",
        default=None,
        help="SSO token header 值(預設讀 one-local.properties 的 DEV_SSO_TOKEN; 有 connector 且"
        "未給時送 dummy)",
    )
    parser.add_argument(
        "--sso-url",
        default=None,
        help="SSO url header 值(預設讀 one-local.properties 的 DEV_SSO_URL; 有 connector 且"
        "未給時送 dummy)",
    )
    parser.add_argument(
        "--dashboard-out",
        type=Path,
        default=None,
        help="本輪有 DASHBOARD_HTML 時另存一份到此路徑(給外部腳本接手)",
    )
    parser.add_argument("--open", action="store_true", help="本輪結束後用瀏覽器開 dashboard")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="印出每個設定值的來源(cli/env/properties/default); secrets 只印來源不印值",
    )
    return parser


def main() -> None:
    try:
        config = load_dev_config()
    except ValueError as config_error:
        # NEVER 讓這種例外冒成裸 traceback——連 --help 都會先跑到這裡, 一律轉成單行訊息離開.
        sys.exit(str(config_error))
    args = _build_parser().parse_args()

    settings = get_settings()
    dev_sources = dev_key_sources()
    base_url = resolve_option(args.base_url, config.deepagent_url, dev_sources[DEV_DEEPAGENT_URL])
    token = resolve_option(
        args.token,
        settings.AGENT_API_BEARER_TOKEN or None,
        official_key_source(AGENT_API_BEARER_TOKEN_KEY),
    )
    sso_token = resolve_option(args.sso_token, config.sso_token, dev_sources[DEV_SSO_TOKEN])
    sso_url = resolve_option(args.sso_url, config.sso_url, dev_sources[DEV_SSO_URL])
    # base_url 的 fallback 恆為 str, `or` 只是把 `str | None` 收斂成 str.
    deepagent_url = base_url.value or config.deepagent_url

    try:
        incoming_connectors = [parse_connector(tokens) for tokens in args.connector]
    except ValueError as parse_error:
        sys.exit(str(parse_error))

    # 放在缺 token 的檢查之前: 缺 token 正是最需要看「到底讀了哪個檔、哪一層」的時候.
    if args.verbose:
        _print_config_sources(
            collect_config_source_rows(
                base_url=base_url,
                token=token,
                sso_token=sso_token,
                sso_url=sso_url,
                config_connectors=config.connectors,
                cli_connectors=incoming_connectors,
                settings=settings,
            )
        )

    if not token.value:
        sys.exit(
            f"缺 bearer token: 用 --token 或在 one-local.properties 設 {AGENT_API_BEARER_TOKEN_KEY}"
            "(--verbose 可看目前讀到哪個檔)"
        )

    state_dir: Path = args.state_dir
    state_dir.mkdir(parents=True, exist_ok=True)
    dashboard_path = state_dir / DASHBOARD_FILE_NAME

    state = None if args.new else _load_state(state_dir)
    if state is None:
        session_connectors = resolve_new_session_connectors(
            config.connectors, incoming_connectors, use_config_connectors=not args.no_connectors
        )
        if not args.csv and not session_connectors:
            sys.exit(
                "首輪(或 --new)必須有至少一個資料來源: --csv、--connector, 或 "
                "one-local.properties 的 DEV_CONNECTORS(--no-connectors 會忽略後者)"
            )
        session_id = args.session_id or f"dev-{uuid.uuid4().hex[:8]}"
        state = {
            "sessionId": session_id,
            "userId": args.user_id,
            "sources": _stage_sources(state_dir, session_id, args.csv),
            "connectors": session_connectors,
            "history": [],
        }
        if dashboard_path.exists():
            dashboard_path.unlink()
        source_aliases = ", ".join(source["alias"] for source in state["sources"]) or "無"
        connector_ids = ", ".join(str(connector["id"]) for connector in state["connectors"]) or "無"
        print(
            f"🆕 新 session: {session_id}(sources: {source_aliases}; connectors: {connector_ids})"
        )
    else:
        if args.csv:
            state["sources"].extend(_stage_sources(state_dir, state["sessionId"], args.csv))
            print(
                "➕ 加入資料檔, sources 現有: "
                + ", ".join(source["alias"] for source in state["sources"])
            )
        if incoming_connectors:
            state["connectors"] = merge_connectors(state.get("connectors", []), incoming_connectors)
            print(
                "➕ 加入 connector, 現有: "
                + ", ".join(str(connector["id"]) for connector in state["connectors"])
            )
        if args.session_id and args.session_id != state["sessionId"]:
            sys.exit(
                f"--session-id {args.session_id} 與現有 session {state['sessionId']} 不同; "
                "要換 session 請加 --new"
            )
        print(f"↩︎ 續 session: {state['sessionId']}(第 {len(state['history']) // 2 + 1} 輪)")

    payload: dict[str, Any] = {
        "sessionId": state["sessionId"],
        "userId": state.get("userId", args.user_id),
        "message": args.message,
        "history": state["history"],
        "sources": state["sources"],
        "connectors": state.get("connectors", []),
    }
    if dashboard_path.exists():
        payload["previousDashboardHtml"] = dashboard_path.read_text(encoding="utf-8")

    headers = build_headers(
        bearer_token=token.value,
        has_connectors=bool(payload["connectors"]),
        sso_token=sso_token.value,
        sso_url=sso_url.value,
        sso_token_header=settings.SSO_TOKEN_HEADER,
        sso_url_header=settings.SSO_URL_HEADER,
    )
    if payload["connectors"]:
        connector_ids = ", ".join(str(connector["id"]) for connector in payload["connectors"])
        sso_status = "real" if (sso_token.value and sso_url.value) else "dummy"
        print(f"ℹ️  connectors 本輪: {connector_ids}(SSO: {sso_status})")

    _preflight(deepagent_url, payload["connectors"])

    raw_log_path = state_dir / f"chat-{int(time.time())}.log"
    print(f"POST {deepagent_url}/chat  sessionId={state['sessionId']}  raw SSE → {raw_log_path}")

    try:
        answer_text, dashboard_html = _stream_chat(
            deepagent_url, payload, headers, dashboard_path, raw_log_path
        )
    except httpx.ConnectError:
        sys.exit(f"連不上 {deepagent_url}——deepagent 起了嗎?(uv run uvicorn app.main:app)")
    except httpx.HTTPStatusError as http_error:
        sys.exit(f"/chat 回 {http_error.response.status_code}: {http_error.response.text[:500]}")

    if dashboard_html is not None and args.dashboard_out is not None:
        args.dashboard_out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(dashboard_path, args.dashboard_out)
        print(f"📄 dashboard 另存 → {args.dashboard_out}")

    if answer_text is None:
        print("\n(本輪沒有 ANSWER——多半以 ERROR 收場, history 不推進)")
        if dashboard_html is None:
            sys.exit(1)
        return

    state["history"].append({"role": "user", "text": args.message})
    state["history"].append({"role": "assistant", "text": answer_text})
    _save_state(state_dir, state)

    if args.dashboard_out is not None and dashboard_html is None:
        print(
            "turn completed but no DASHBOARD_HTML event arrived -- the model answered without "
            "emitting a dashboard."
        )
        if dashboard_path.exists():
            print(f"{dashboard_path} is unchanged from before this turn (stale).")
        print(
            "check the ANSWER text above (it may have asked a question); reply with another turn."
        )
        sys.exit(2)

    if args.open and dashboard_html is not None:
        webbrowser.open(dashboard_path.resolve().as_uri())


if __name__ == "__main__":
    main()
