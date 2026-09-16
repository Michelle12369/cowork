"""直打 deepagent `/chat` 的開發用 chat client——不需要起 Java/前端, 也身兼
`scripts/mcp-shell/` 的驅動腳本(preflight、connector 模式首輪、失敗診斷都在這裡).

模擬 backend 的跨輪簿記: 自動維護 sessionId/history/sources/connectors/previousDashboardHtml,
把 CSV 排進 `uploads/` 佈局(resolve_source_path 的路徑形狀要求), SSE 事件即時印出, 原始 SSE
落成 `chat-<ts>.log`, DASHBOARD_HTML 落地成 `dashboard.html`. 狀態存在 `.dev-session/`
(gitignored), `--new` 開新對話.

設定來源(與 app/config.py 同一份 `one-local.properties`, 路徑看 `ONE_PROPERTIES_PATH`):
- 每個 key 同一條規則: CLI flag > env > 檔案 > 內建預設, 由 `scripts/dev_config.resolve()`
  解析(官方 key `AGENT_API_BEARER_TOKEN`/`SSO_TOKEN_HEADER`/`SSO_URL_HEADER` 的值仍取自
  `app.config.get_settings()`; dev-only key `DEV_DEEPAGENT_URL`/`DEV_SSO_TOKEN`/`DEV_SSO_URL`/
  `DEV_CONNECTORS`(單行 JSON connector list)服務本身不讀). deepagent 位址預設
  `http://127.0.0.1:8000`, 要換位址請改 `DEV_DEEPAGENT_URL` 或直接 `--base-url`.
- dev 期間檔案才是權威來源: env 蓋掉檔案裡有值的 key 時(多半是上一個 session 留下的 export)
  會印一段警告, 不用等 `--verbose`.
- `--verbose` 印出每個設定值實際來自哪一層(cli / env / properties / default)與讀的是哪個
  properties 檔; secrets(bearer token、SSO token/url)只印來源與有無, NEVER 印值.

認證與 connector:
- inbound bearer: `--token`(預設 `AGENT_API_BEARER_TOKEN`, 必須與 deepagent 端同值).
- MCP connector: 新 session 時 `DEV_CONNECTORS` 與 `--connector ID URL [NAME] [BEARER_TOKEN_KEY]`
  (可重複)合併, 同 id 以 CLI 覆蓋; `--no-connectors` 則捨棄 `DEV_CONNECTORS`, 只用 CLI 給的
  (檔案模式配 `--csv` 時常用). 續接輪的 connector 集合在首輪就固定, `--connector` 仍可加,
  不會重新套用 `DEV_CONNECTORS`.
  有 connector 時 deepagent 的 mcp_adapter 要求 SSO 兩個 header 非空, 用 `--sso-token`/
  `--sso-url`(預設讀 `DEV_SSO_TOKEN`/`DEV_SSO_URL`)給值. 沒給時只有「所有 connector 都在本機
  (loopback)」才送 dummy 值(mock server 不檢查); 只要有一個 connector 指向真的主機就直接早退,
  NEVER 把假憑證送出去換一張 MCP 深處的 AUTH 卡.
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
    uv run scripts/dev_chat.py --state-dir scripts/mcp-shell/out/.dev-session \\
        --dashboard-out scripts/mcp-shell/out/dashboard.html "Build a sales dashboard"  # spike 用法
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
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.dev_config import (
    AGENT_API_BEARER_TOKEN,
    DEV_CONNECTORS,
    DEV_DEEPAGENT_URL,
    DEV_SSO_TOKEN,
    DEV_SSO_URL,
    ONE_PROPERTIES_PATH,
    DevConfig,
    DevSetting,
    connectors_needing_real_sso,
    env_shadow_warning_lines,
    resolve,
)

DEFAULT_STATE_DIR = Path(__file__).resolve().parent.parent / ".dev-session"
DEFAULT_USER_ID = "dev-user"

# mcp_adapter._build_headers() 對任何 connector 都 require_sso_token()/require_sso_url(); mock
# server 不檢查, 所以沒給時用看得出是假的值頂著. 只在 connector 全在本機時才走到這裡——
# main() 會先用 connectors_needing_real_sso() 擋掉指向真主機的情況.
DUMMY_SSO_TOKEN = "dev-chat-sso-token"
DUMMY_SSO_URL = "https://sso.invalid/dev-chat"

FILE_TYPE_BY_SUFFIX = {".csv": "csv", ".xlsx": "xlsx", ".xls": "xlsx"}

STEP_STATUS_MARKS = {"RUNNING": "⏳", "SUCCESS": "✅", "ERROR": "❌"}

STATE_FILE_NAME = "state.json"
DASHBOARD_FILE_NAME = "dashboard.html"

PREFLIGHT_TIMEOUT_SECONDS = 5.0

# 可用 CLI flag 覆寫的設定 key: argparse dest -> key 名. `_cli_overrides()` 靠它組給 `resolve()`
# 的覆寫 dict, `--verbose` 靠它把 flag 名印在 key 旁邊. 新增一個可覆寫的 key: 這裡一筆, 加
# `_build_parser()` 一個 `add_argument`(default 一律 None, 才分得出值是 CLI 給的還是落回哪一層).
CLI_OVERRIDE_KEYS: dict[str, str] = {
    "base_url": DEV_DEEPAGENT_URL,
    "token": AGENT_API_BEARER_TOKEN,
    "sso_token": DEV_SSO_TOKEN,
    "sso_url": DEV_SSO_URL,
}

# --verbose 表裡 secrets 的顯示: 只說有沒有值, NEVER 印值.
HIDDEN_VALUE = "(hidden)"
UNSET_VALUE = "(not set)"
NO_CONNECTORS_VALUE = "(none)"


def _cli_overrides(args: argparse.Namespace) -> dict[str, str | None]:
    return {key: getattr(args, dest) for dest, key in CLI_OVERRIDE_KEYS.items()}


def _connector_ids_display(connectors: list[dict[str, str | None]]) -> str:
    if not connectors:
        return NO_CONNECTORS_VALUE
    return "ids: " + ", ".join(str(connector["id"]) for connector in connectors)


def setting_label(setting: DevSetting) -> str:
    """`--verbose` 表的第一欄: 有 flag 的 key 印成 `--flag (KEY)`, 其餘就是 key 名."""
    for dest, key in CLI_OVERRIDE_KEYS.items():
        if key == setting.key:
            return f"--{dest.replace('_', '-')} ({key})"
    return setting.key


def setting_display_value(setting: DevSetting, config: DevConfig) -> str:
    """`--verbose` 表的第三欄. secrets 只印有無; connector 只印 id, NEVER 印 url(query string
    可能藏 token); properties 檔路徑帶上存不存在."""
    if setting.secret:
        return HIDDEN_VALUE if setting.value else UNSET_VALUE
    if setting.key == DEV_CONNECTORS:
        return _connector_ids_display(config.connectors)
    if setting.key == ONE_PROPERTIES_PATH:
        properties_file = Path(str(setting.value))
        return f"{properties_file} ({'exists' if properties_file.exists() else 'missing'})"
    return setting.value if setting.value is not None else UNSET_VALUE


def print_config(config: DevConfig) -> None:
    """`--verbose`: 每個設定值的來源(cli/env/properties/default), 一列一個 key."""
    labels = [setting_label(setting) for setting in config.settings]
    label_width = max(len(label) for label in labels)
    source_width = max(len(setting.source) for setting in config.settings)
    print("ℹ️  設定來源(--verbose; secrets 只印來源不印值):")
    for label, setting in zip(labels, config.settings, strict=True):
        shown_value = setting_display_value(setting, config)
        print(f"   {label:<{label_width}}  {setting.source:<{source_width}}  {shown_value}")


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
                f"先起 deepagent(uv run fastapi dev --port 8000 --reload-dir app), 或用 --base-url/one-local.properties "
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
                    "(mock server: uv run python scripts/mcp-shell/mock_server.py)"
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
    # 下面四個 flag(CLI_OVERRIDE_KEYS)的 default 一律 None: resolve() 才疊上 env/
    # one-local.properties/內建預設, 這樣 --verbose 分得出值是 CLI 給的還是落回哪一層.
    parser.add_argument(
        "--base-url",
        default=None,
        help=f"deepagent 服務位址(預設讀 {DEV_DEEPAGENT_URL}: env > one-local.properties)",
    )
    parser.add_argument(
        "--state-dir", type=Path, default=DEFAULT_STATE_DIR, help="session 狀態資料夾"
    )
    parser.add_argument(
        "--token",
        default=None,
        help=f"inbound bearer token(預設讀 {AGENT_API_BEARER_TOKEN}: env > one-local.properties)",
    )
    parser.add_argument(
        "--sso-token",
        default=None,
        help=f"SSO token header 值(預設讀 {DEV_SSO_TOKEN}: env > one-local.properties; 有 "
        "connector 且未給時送 dummy)",
    )
    parser.add_argument(
        "--sso-url",
        default=None,
        help=f"SSO url header 值(預設讀 {DEV_SSO_URL}: env > one-local.properties; 有 "
        "connector 且未給時送 dummy)",
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


def _resolve_config(args: argparse.Namespace) -> DevConfig:
    """`resolve()` 加上兩道 dev 專用的檢查: 設定壞掉一律轉成單行訊息離開(NEVER 冒裸
    traceback), 缺 bearer token 也在這裡擋. `--verbose` 表印在缺 token 的離開之前——缺 token
    正是最需要看「到底讀了哪個檔、哪一層」的時候."""
    try:
        config = resolve(_cli_overrides(args))
    except ValueError as config_error:
        sys.exit(str(config_error))
    if args.verbose:
        print_config(config)
    for warning_line in env_shadow_warning_lines(config):
        print(warning_line)
    if not config.bearer_token:
        sys.exit(
            f"缺 bearer token: 用 --token 或在 one-local.properties 設 {AGENT_API_BEARER_TOKEN}"
            "(--verbose 可看目前讀到哪個檔)"
        )
    return config


def _load_or_start_session(
    args: argparse.Namespace, config: DevConfig, cli_connectors: list[dict[str, str | None]]
) -> dict[str, Any]:
    """讀 `--state-dir` 下的既有 session, 或(`--new`/沒有狀態時)開一個新的. 新 session 的
    connector 集合在這裡定案; 續接輪只接受 `--connector` 追加, 不重新套用 DEV_CONNECTORS."""
    state_dir: Path = args.state_dir
    state_dir.mkdir(parents=True, exist_ok=True)
    dashboard_path = state_dir / DASHBOARD_FILE_NAME

    state = None if args.new else _load_state(state_dir)
    if state is None:
        session_connectors = resolve_new_session_connectors(
            config.connectors, cli_connectors, use_config_connectors=not args.no_connectors
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
        return state

    if args.csv:
        state["sources"].extend(_stage_sources(state_dir, state["sessionId"], args.csv))
        print(
            "➕ 加入資料檔, sources 現有: "
            + ", ".join(source["alias"] for source in state["sources"])
        )
    if cli_connectors:
        state["connectors"] = merge_connectors(state.get("connectors", []), cli_connectors)
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
    return state


def _build_payload(args: argparse.Namespace, state: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "sessionId": state["sessionId"],
        "userId": state.get("userId", args.user_id),
        "message": args.message,
        "history": state["history"],
        "sources": state["sources"],
        "connectors": state.get("connectors", []),
    }
    dashboard_path = args.state_dir / DASHBOARD_FILE_NAME
    if dashboard_path.exists():
        payload["previousDashboardHtml"] = dashboard_path.read_text(encoding="utf-8")
    return payload


def _build_headers_for_turn(config: DevConfig, payload: dict[str, Any]) -> dict[str, str]:
    """SSO 閘門 + header 組裝: 有 connector 指向真主機而 SSO 值沒設時早退, NEVER 把假憑證送出去."""
    has_real_sso = bool(config.sso_token and config.sso_url)
    if payload["connectors"] and not has_real_sso:
        remote_connector_ids = connectors_needing_real_sso(payload["connectors"])
        if remote_connector_ids:
            sys.exit(
                f"connector {', '.join(remote_connector_ids)} 不在本機(loopback), 但 SSO 值沒設: "
                f"送 dummy 值只會在 MCP 呼叫深處變成 AUTH 失敗. 請用 --sso-token/--sso-url, "
                f"或在 one-local.properties 設 {DEV_SSO_TOKEN}/{DEV_SSO_URL}"
            )
    if payload["connectors"]:
        connector_ids = ", ".join(str(connector["id"]) for connector in payload["connectors"])
        sso_status = "real" if has_real_sso else "dummy(loopback)"
        print(f"ℹ️  connectors 本輪: {connector_ids}(SSO: {sso_status})")
    return build_headers(
        bearer_token=str(config.bearer_token),
        has_connectors=bool(payload["connectors"]),
        sso_token=config.sso_token,
        sso_url=config.sso_url,
        sso_token_header=config.sso_token_header,
        sso_url_header=config.sso_url_header,
    )


def _stream_and_report(
    args: argparse.Namespace,
    config: DevConfig,
    state: dict[str, Any],
    payload: dict[str, Any],
    headers: dict[str, str],
) -> None:
    """POST /chat、落地 dashboard、推進 history; exit code: 1=沒有 ANSWER 也沒有 dashboard,
    2=有 --dashboard-out 但這輪沒有 DASHBOARD_HTML."""
    state_dir: Path = args.state_dir
    dashboard_path = state_dir / DASHBOARD_FILE_NAME
    raw_log_path = state_dir / f"chat-{int(time.time())}.log"
    print(
        f"POST {config.deepagent_url}/chat  sessionId={state['sessionId']}  raw SSE → {raw_log_path}"
    )

    try:
        answer_text, dashboard_html = _stream_chat(
            config.deepagent_url, payload, headers, dashboard_path, raw_log_path
        )
    except httpx.ConnectError:
        sys.exit(f"連不上 {config.deepagent_url}——deepagent 起了嗎?(uv run uvicorn app.main:app)")
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


def main() -> None:
    args = _build_parser().parse_args()
    config = _resolve_config(args)
    try:
        cli_connectors = [parse_connector(tokens) for tokens in args.connector]
    except ValueError as parse_error:
        sys.exit(str(parse_error))
    state = _load_or_start_session(args, config, cli_connectors)
    payload = _build_payload(args, state)
    headers = _build_headers_for_turn(config, payload)
    _preflight(config.deepagent_url, payload["connectors"])
    _stream_and_report(args, config, state, payload, headers)


if __name__ == "__main__":
    main()
