"""直打 deepagent `/chat` 的開發用 chat client——不需要起 Java/前端。

模擬 backend 的跨輪簿記: 自動維護 sessionId/history/sources/connectors/previousDashboardHtml,
把 CSV 排進 `uploads/` 佈局(resolve_source_path 的路徑形狀要求), SSE 事件即時印出, 原始 SSE
落成 `chat-<ts>.log`, DASHBOARD_HTML 落地成 `dashboard.html`. 狀態存在 `.dev-session/`
(gitignored), `--new` 開新對話.

認證與 connector:
- inbound bearer: `--token` 或環境變數 `AGENT_API_BEARER_TOKEN`(必填, 與 deepagent 端同值).
- MCP connector: `--connector ID URL [NAME] [BEARER_TOKEN_KEY]`, 可重複; 有 connector 時
  deepagent 的 mcp_adapter 要求 SSO 兩個 header 非空, 用 `--sso-token`/`--sso-url`
  (或 `DEV_SSO_TOKEN`/`DEV_SSO_URL`)給值, 不給就送 dummy 值(mock server 不檢查).
  header 名稱預設 `X-SSO-Token`/`X-SSO-Url`, 可用 `SSO_TOKEN_HEADER`/`SSO_URL_HEADER` 覆寫
  (與 app/config.py 同名).
- token/SSO 值 NEVER 印出或寫進狀態檔.

用法:
    uv run scripts/dev_chat.py --csv ~/data.csv "哪個系統最需要改善?"            # 檔案模式首輪
    uv run scripts/dev_chat.py "Build a sales dashboard" \\
        --connector sales http://127.0.0.1:8765/mcp Sales   # connector 模式首輪(訊息放前面:
                                                             # --connector 吃可變長度清單)
    uv run scripts/dev_chat.py "改成圓餅圖"                                     # 後續輪自動帶狀態
    uv run scripts/dev_chat.py --new --csv ~/other.csv "換一份資料"             # 重開 session
"""

import argparse
import json
import os
import re
import shutil
import sys
import time
import uuid
import webbrowser
from pathlib import Path
from typing import Any

import httpx

DEFAULT_BASE_URL = "http://localhost:8000"
DEFAULT_STATE_DIR = Path(__file__).resolve().parent.parent / ".dev-session"
DEFAULT_USER_ID = "dev-user"

BEARER_TOKEN_ENV = "AGENT_API_BEARER_TOKEN"
SSO_TOKEN_ENV = "DEV_SSO_TOKEN"
SSO_URL_ENV = "DEV_SSO_URL"
SSO_TOKEN_HEADER_ENV = "SSO_TOKEN_HEADER"
SSO_URL_HEADER_ENV = "SSO_URL_HEADER"
DEFAULT_SSO_TOKEN_HEADER = "X-SSO-Token"
DEFAULT_SSO_URL_HEADER = "X-SSO-Url"
# mcp_adapter._build_headers() 對任何 connector 都 require_sso_token()/require_sso_url(); mock
# server 不檢查, 所以沒給時用看得出是假的值頂著.
DUMMY_SSO_TOKEN = "dev-chat-sso-token"
DUMMY_SSO_URL = "https://sso.invalid/dev-chat"

FILE_TYPE_BY_SUFFIX = {".csv": "csv", ".xlsx": "xlsx", ".xls": "xlsx"}

STEP_STATUS_MARKS = {"RUNNING": "⏳", "SUCCESS": "✅", "ERROR": "❌"}

STATE_FILE_NAME = "state.json"
DASHBOARD_FILE_NAME = "dashboard.html"


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
            sys.exit(f"401 Unauthorized——bearer token 不符或 deepagent 端未設 {BEARER_TOKEN_ENV}")
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
        help="MCP connector: ID URL [NAME] [BEARER_TOKEN_KEY], 可重複; 同 id 覆蓋既有. 吃可變長度清單, 訊息請放在它前面",
    )
    parser.add_argument("--new", action="store_true", help="放棄現有 session 重新開始")
    parser.add_argument("--session-id", default=None, help="指定 sessionId(預設 dev-<8 hex>)")
    parser.add_argument("--user-id", default=DEFAULT_USER_ID, help="ChatRequest.userId")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="deepagent 服務位址")
    parser.add_argument(
        "--state-dir", type=Path, default=DEFAULT_STATE_DIR, help="session 狀態資料夾"
    )
    parser.add_argument(
        "--token",
        default=os.environ.get(BEARER_TOKEN_ENV),
        help=f"inbound bearer token(預設讀環境變數 {BEARER_TOKEN_ENV})",
    )
    parser.add_argument(
        "--sso-token",
        default=os.environ.get(SSO_TOKEN_ENV),
        help=f"SSO token header 值(預設讀 {SSO_TOKEN_ENV}; 有 connector 且未給時送 dummy)",
    )
    parser.add_argument(
        "--sso-url",
        default=os.environ.get(SSO_URL_ENV),
        help=f"SSO url header 值(預設讀 {SSO_URL_ENV}; 有 connector 且未給時送 dummy)",
    )
    parser.add_argument(
        "--dashboard-out",
        type=Path,
        default=None,
        help="本輪有 DASHBOARD_HTML 時另存一份到此路徑(給外部腳本接手)",
    )
    parser.add_argument("--open", action="store_true", help="本輪結束後用瀏覽器開 dashboard")
    return parser


def main() -> None:
    args = _build_parser().parse_args()

    if not args.token:
        sys.exit(f"缺 bearer token: 用 --token 或設環境變數 {BEARER_TOKEN_ENV}")

    try:
        incoming_connectors = [parse_connector(tokens) for tokens in args.connector]
    except ValueError as parse_error:
        sys.exit(str(parse_error))

    state_dir: Path = args.state_dir
    state_dir.mkdir(parents=True, exist_ok=True)
    dashboard_path = state_dir / DASHBOARD_FILE_NAME

    state = None if args.new else _load_state(state_dir)
    if state is None:
        if not args.csv and not incoming_connectors:
            sys.exit("首輪(或 --new)必須用 --csv 或 --connector 指定至少一個資料來源")
        session_id = args.session_id or f"dev-{uuid.uuid4().hex[:8]}"
        state = {
            "sessionId": session_id,
            "userId": args.user_id,
            "sources": _stage_sources(state_dir, session_id, args.csv),
            "connectors": incoming_connectors,
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
        bearer_token=args.token,
        has_connectors=bool(payload["connectors"]),
        sso_token=args.sso_token,
        sso_url=args.sso_url,
        sso_token_header=os.environ.get(SSO_TOKEN_HEADER_ENV, DEFAULT_SSO_TOKEN_HEADER),
        sso_url_header=os.environ.get(SSO_URL_HEADER_ENV, DEFAULT_SSO_URL_HEADER),
    )
    if payload["connectors"] and not (args.sso_token and args.sso_url):
        print(
            "ℹ️  SSO header 未給值, 送 dummy(mock server 不檢查; 真 connector 請設 --sso-token/--sso-url)"
        )

    raw_log_path = state_dir / f"chat-{int(time.time())}.log"
    print(f"POST {args.base_url}/chat  sessionId={state['sessionId']}  raw SSE → {raw_log_path}")

    try:
        answer_text, dashboard_html = _stream_chat(
            args.base_url, payload, headers, dashboard_path, raw_log_path
        )
    except httpx.ConnectError:
        sys.exit(f"連不上 {args.base_url}——deepagent 起了嗎?(uv run uvicorn app.main:app)")
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

    if args.open and dashboard_html is not None:
        webbrowser.open(dashboard_path.resolve().as_uri())


if __name__ == "__main__":
    main()
