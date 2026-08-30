"""`ChatTurn` connector 模式整合測試(spec §5,Task 6)——connector 掛載/劇本 staging/
prompt 段/remount/互斥防禦。單一 turn 的內部狀態(`_agent`/`_workspace`/`_run_input`)直接測
`ChatTurn.__aenter__`(不經 `/chat` SSE 層,斷言更直接);跨 turn remount 需要真的
persist,改走 `/chat` e2e 兩輪(比照 `tests/test_chat.py` 既有的兩輪測試手法)。
"""

import json
import zipfile
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage

from app import main as main_module
from app.agent import chat_turn
from app.agent.chat_turn import ChatTurn
from app.api.schemas import ChatRequest, SourceItem
from app.engine.api_snapshot import SnapshotIntegrityError
from app.engine.recipe import load_landings
from app.engine.workspace_store import build_workspace_store
from tests.conftest import TEST_BEARER_TOKEN
from tests.fake_model import ScriptedChatModel


def _connector_request(**overrides) -> ChatRequest:
    payload = {
        "sessionId": "sess-connector",
        "userId": "user-1",
        "message": "幫我看 Fab A 的品質資料",
        "history": [],
        "sources": [],
        "selectedConnectors": ["demo_quality"],
    }
    payload.update(overrides)
    return ChatRequest(**payload)


@pytest.fixture()
def connector_turn_env(tmp_path, monkeypatch):
    """單一 turn 的 attribute 檢查用——workspace 隔離＋不會真的呼叫模型(不驅動
    `turn.stream()`,`build_model()` 只在 `build_agent` 建圖時被引用一次)。"""
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "ws"))
    monkeypatch.setattr(chat_turn, "build_model", lambda: ScriptedChatModel([]))
    return tmp_path


async def test_selected_connectors_wires_connector_tools_into_agent(connector_turn_env) -> None:
    request = _connector_request()
    async with ChatTurn(request) as turn:
        tool_names = set(turn._agent.nodes["tools"].bound.tools_by_name)

    assert {"demo_quality_get_quality", "demo_quality_list_fabs"} <= tool_names
    # extra_tools 是併入既有 data tools,不是取代。
    assert {"get_schema", "run_sql", "preview_data"} <= tool_names


async def test_selected_connectors_stages_connector_skill_markdown(connector_turn_env) -> None:
    request = _connector_request()
    async with ChatTurn(request) as turn:
        skill_path = turn._workspace.skills_dir / "connectors" / "demo_quality" / "SKILL.md"
        content = skill_path.read_text(encoding="utf-8")

    assert "name: demo_quality" in content
    # frontmatter 是代 staging 補上的最小包裝,劇本正文原樣保留。
    assert "demo_quality 操作劇本" in content
    assert "get_quality(fab, week)" in content


async def test_selected_connectors_prompt_note_has_naming_bridge_and_land_as_guidance(
    connector_turn_env,
) -> None:
    request = _connector_request()
    async with ChatTurn(request) as turn:
        seeded_message = turn._run_input["messages"][-1].content

    assert "demo_quality" in seeded_message
    assert "前綴掛載" in seeded_message
    assert "land_as" in seeded_message


async def test_single_connector_selected_has_no_join_guardrail_line(connector_turn_env) -> None:
    request = _connector_request()
    async with ChatTurn(request) as turn:
        seeded_message = turn._run_input["messages"][-1].content

    assert "join key" not in seeded_message


async def test_sources_and_selected_connectors_both_nonempty_raises(connector_turn_env) -> None:
    tmp_path = connector_turn_env
    csv_path = tmp_path / "uploads" / "sess-1" / "orders.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.write_text("system\nCRM\n", encoding="utf-8")
    request = _connector_request(
        sources=[{"alias": "orders", "path": str(csv_path), "fileType": "csv"}]
    )

    with pytest.raises(ValueError, match="selectedConnectors"):
        async with ChatTurn(request):
            pass


async def test_empty_selected_connectors_uses_file_mode_unaffected(connector_turn_env) -> None:
    tmp_path = connector_turn_env
    csv_path = tmp_path / "uploads" / "sess-1" / "orders.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.write_text("system\nCRM\n", encoding="utf-8")
    request = ChatRequest(
        sessionId="sess-1",
        userId="user-1",
        message="哪個系統最多?",
        history=[],
        sources=[SourceItem(alias="orders", path=str(csv_path), fileType="csv")],
    )

    async with ChatTurn(request) as turn:
        tool_names = set(turn._agent.nodes["tools"].bound.tools_by_name)
        external_access = turn._connection.execute(
            "SELECT current_setting('enable_external_access')"
        ).fetchone()[0]
        connectors_skill_dir = turn._workspace.skills_dir / "connectors"
        seeded_message = turn._run_input["messages"][-1].content

    assert not any(name.startswith("demo_quality_") for name in tool_names)
    assert external_access is False
    assert not connectors_skill_dir.exists()
    assert "System note" not in seeded_message


# -- 跨 turn remount(需要真的 persist,走 /chat e2e)---------------------------------------


def _connector_chat_payload(**overrides) -> dict:
    payload = {
        "sessionId": "sess-connector-remount",
        "userId": "user-1",
        "message": "幫我看 Fab A 上週的品質數據",
        "history": [],
        "sources": [],
        "selectedConnectors": ["demo_quality"],
    }
    payload.update(overrides)
    return payload


def _sse_events(raw_body: str) -> list[dict]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in raw_body.splitlines()
        if line.startswith("data: ")
    ]


def _land_then_answer_script() -> list[AIMessage]:
    return [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "demo_quality_get_quality",
                    "id": "call-land",
                    "args": {
                        "fab": "FAB_A",
                        "week": "2026-W32",
                        "land_as": "quality_fab_a",
                    },
                }
            ],
        ),
        AIMessage(content="已取得並落表。"),
    ]


async def test_second_turn_remounts_previously_landed_table(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "ws"))
    scripted = ScriptedChatModel(
        [
            *_land_then_answer_script(),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "run_sql",
                        "id": "call-count",
                        "args": {
                            "sql": "SELECT COUNT(*) AS row_count FROM quality_fab_a",
                            "intent": "驗證 remount 後資料可查",
                        },
                    }
                ],
            ),
            AIMessage(content="共 9 列。"),
        ]
    )
    monkeypatch.setattr(chat_turn, "build_model", lambda: scripted)

    transport = ASGITransport(app=main_module.app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {TEST_BEARER_TOKEN}"},
    ) as client:
        first_response = await client.post("/chat", json=_connector_chat_payload())
        assert first_response.status_code == 200
        second_response = await client.post(
            "/chat", json=_connector_chat_payload(message="幫我算列數")
        )
        assert second_response.status_code == 200

    second_turn_events = _sse_events(second_response.text)
    table_events = [event for event in second_turn_events if event["type"] == "TABLE"]
    assert table_events
    # envelope payload {"data": [...9 列...], "errorCode": ""} 寬鬆落表成單列表(spec §4-2,
    # 見 test_connector_wrapper.py 的同款斷言)——data 欄整包變成 LIST 欄不拆封,故 remount
    # 後這條 turn 2 的 run_sql COUNT(*) 查到的是 1 列,不是 9。這裡驗證的重點是「remount
    # 真的把 turn 1 落的表接回來、turn 2 能直接查」,不是驗證寬鬆落表的列數語意。
    assert table_events[0]["rows"] == [[1]]

    workspace = build_workspace_store().prepare("user-1", "sess-connector-remount")
    landings = load_landings(workspace)
    assert any(landing["land_as"] == "quality_fab_a" for landing in landings)


def _tamper_zip_entry(zip_path: Path, entry_name: str, new_content: bytes) -> None:
    """重寫一顆已持久化的 generation zip 裡的單一 entry——模擬 `run_sql` 透過鎖門後仍開放
    寫入的 `allowed_directories` 白名單目錄覆寫/竄改已落表 snapshot 檔案(見
    `app.engine.duck.open_locked_connection` docstring 的完整性守則),藉此驗證
    `remount_snapshots` 的雜湊門禁真的擋下遭竄改的資料。"""
    with zipfile.ZipFile(zip_path) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    entries[entry_name] = new_content
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)


async def test_second_turn_tampered_snapshot_raises_snapshot_integrity_error(
    tmp_path, monkeypatch
) -> None:
    workspace_root = tmp_path / "ws"
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(workspace_root))
    monkeypatch.setattr(
        chat_turn, "build_model", lambda: ScriptedChatModel(_land_then_answer_script())
    )

    transport = ASGITransport(app=main_module.app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {TEST_BEARER_TOKEN}"},
    ) as client:
        first_response = await client.post("/chat", json=_connector_chat_payload())
        assert first_response.status_code == 200

        session_dir = (
            workspace_root / "workspace" / "user-1" / "sessions" / "sess-connector-remount"
        )
        zip_candidates = list(session_dir.glob("gen-*.zip"))
        assert len(zip_candidates) == 1
        _tamper_zip_entry(
            zip_candidates[0], "api_snapshots/quality_fab_a.json", b'{"tampered": true}'
        )

        with pytest.raises(BaseException) as exception_info:
            await client.post("/chat", json=_connector_chat_payload(message="幫我算列數"))

    assert exception_info.group_contains(SnapshotIntegrityError)
