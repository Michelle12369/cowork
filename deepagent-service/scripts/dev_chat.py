"""直打 deepagent `/chat` 的開發用 chat client——不需要起 Java/前端。

模擬 backend 的跨輪簿記:自動維護 sessionId/history/previousDashboardHtml,把 CSV 排進
`uploads/` 佈局(resolve_source_path 的路徑形狀要求),SSE 事件即時印出,DASHBOARD_HTML
落地成檔案。狀態存在 `.dev-session/`(gitignored),`--new` 開新對話。

用法:
    uv run scripts/dev_chat.py --csv ~/data.csv "哪個系統最需要改善?"   # 首輪
    uv run scripts/dev_chat.py "改成圓餅圖"                             # 後續輪自動帶狀態
    uv run scripts/dev_chat.py --new --csv ~/other.csv "換一份資料"     # 重開 session
"""

import argparse
import json
import re
import shutil
import sys
import uuid
import webbrowser
from pathlib import Path
from typing import Any

import httpx

DEFAULT_BASE_URL = "http://localhost:8000"
DEFAULT_STATE_DIR = Path(__file__).resolve().parent.parent / ".dev-session"
DEV_USER_ID = "dev-user"

FILE_TYPE_BY_SUFFIX = {".csv": "csv", ".xlsx": "xlsx", ".xls": "xlsx"}

STEP_STATUS_MARKS = {"RUNNING": "⏳", "SUCCESS": "✅", "ERROR": "❌"}


def _alias_for(source_file: Path) -> str:
    """檔名 stem 轉成可當 SQL 資料表名的 alias:小寫、非英數轉底線、開頭補字母。"""
    alias = re.sub(r"[^a-z0-9]+", "_", source_file.stem.lower()).strip("_")
    if not alias or not alias[0].isalpha():
        alias = f"t_{alias}" if alias else "data"
    return alias


def _load_state(state_dir: Path) -> dict[str, Any] | None:
    state_path = state_dir / "state.json"
    if not state_path.exists():
        return None
    return json.loads(state_path.read_text(encoding="utf-8"))


def _save_state(state_dir: Path, state: dict[str, Any]) -> None:
    (state_dir / "state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _stage_sources(state_dir: Path, session_id: str, csv_paths: list[str]) -> list[dict[str, str]]:
    """把資料檔複製進含 `uploads` 段的佈局(鏡射 backend 給 deepagent 的路徑形狀)。"""
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


def _print_event(event: dict[str, Any], state_dir: Path) -> tuple[str | None, str | None]:
    """印出單一 wire 事件;回傳 (answer_text, dashboard_html) 中本事件產出的部分。"""
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
        dashboard_path = state_dir / "dashboard.html"
        dashboard_path.write_text(dashboard_html, encoding="utf-8")
        print(f"📊 dashboard 已更新 → {dashboard_path}", flush=True)
        return None, dashboard_html
    elif event_type == "ERROR":
        print(f"\n💥 ERROR [{event.get('code')}] {event.get('message')}")
    return None, None


def _stream_chat(
    base_url: str, payload: dict[str, Any], state_dir: Path
) -> tuple[str | None, str | None]:
    """POST /chat 並即時消化 SSE;回傳 (最終 answer, 最終 dashboard html)。"""
    answer_text: str | None = None
    dashboard_html: str | None = None
    timeout = httpx.Timeout(600.0, connect=10.0)
    with (
        httpx.Client(timeout=timeout, trust_env=False) as client,
        client.stream("POST", f"{base_url}/chat", json=payload) as response,
    ):
        response.raise_for_status()
        for line in response.iter_lines():
            if not line.startswith("data:"):
                continue  # comment ping/空行
            event = json.loads(line[len("data:") :].strip())
            event_answer, event_dashboard = _print_event(event, state_dir)
            answer_text = event_answer or answer_text
            dashboard_html = event_dashboard or dashboard_html
    return answer_text, dashboard_html


def main() -> None:
    parser = argparse.ArgumentParser(description="直打 deepagent /chat 的開發用 client")
    parser.add_argument("message", help="這一輪要對 agent 說的話")
    parser.add_argument(
        "--csv", action="append", default=[], help="資料檔路徑(csv/xlsx,可重複;首輪必填)"
    )
    parser.add_argument("--new", action="store_true", help="放棄現有 session 重新開始")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="deepagent 服務位址")
    parser.add_argument(
        "--state-dir", type=Path, default=DEFAULT_STATE_DIR, help="session 狀態資料夾"
    )
    parser.add_argument("--open", action="store_true", help="本輪結束後用瀏覽器開 dashboard")
    args = parser.parse_args()

    state_dir: Path = args.state_dir
    state_dir.mkdir(parents=True, exist_ok=True)

    state = None if args.new else _load_state(state_dir)
    if state is None:
        if not args.csv:
            sys.exit("首輪(或 --new)必須用 --csv 指定至少一個資料檔")
        session_id = f"dev-{uuid.uuid4().hex[:8]}"
        state = {
            "sessionId": session_id,
            "sources": _stage_sources(state_dir, session_id, args.csv),
            "history": [],
        }
        dashboard_file = state_dir / "dashboard.html"
        if dashboard_file.exists():
            dashboard_file.unlink()
        print(
            f"🆕 新 session: {session_id}(sources: "
            f"{', '.join(source['alias'] for source in state['sources'])})"
        )
    elif args.csv:
        state["sources"].extend(_stage_sources(state_dir, state["sessionId"], args.csv))
        print(
            f"➕ 加入資料檔,sources 現有: "
            f"{', '.join(source['alias'] for source in state['sources'])}"
        )

    payload: dict[str, Any] = {
        "sessionId": state["sessionId"],
        "userId": DEV_USER_ID,
        "message": args.message,
        "history": state["history"],
        "sources": state["sources"],
    }
    dashboard_file = state_dir / "dashboard.html"
    if dashboard_file.exists():
        payload["previousDashboardHtml"] = dashboard_file.read_text(encoding="utf-8")

    try:
        answer_text, dashboard_html = _stream_chat(args.base_url, payload, state_dir)
    except httpx.ConnectError:
        sys.exit(f"連不上 {args.base_url}——deepagent 起了嗎?(uv run uvicorn app.main:app)")

    if answer_text is None:
        print("\n(本輪沒有 ANSWER——多半以 ERROR 收場,history 不推進)")
        return

    state["history"].append({"role": "user", "text": args.message})
    state["history"].append({"role": "assistant", "text": answer_text})
    _save_state(state_dir, state)

    if args.open and dashboard_html is not None:
        webbrowser.open(dashboard_file.resolve().as_uri())


if __name__ == "__main__":
    main()
