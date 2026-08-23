"""tests/test_recipe.py"""

from app.engine.api_fetch import record_fetch, snapshot_fingerprint
from app.engine.recipe import build_recipe
from app.engine.results import record_query
from app.engine.workspace import prepare_local_layout


def _make_workspace(tmp_path):
    return prepare_local_layout(tmp_path, "user-1", "sess-1")


def test_build_recipe_noFetchRecords_returnsNone(tmp_path):
    workspace = _make_workspace(tmp_path)

    assert build_recipe(workspace, "<html></html>") is None


def test_build_recipe_htmlReferencesOnlyQ1_queriesContainsOnlyQ1(tmp_path):
    workspace = _make_workspace(tmp_path)
    record_fetch(workspace, snapshot_fingerprint("mes_yield", {"line_id": "A"}), "yield_data", "mes_yield", {"line_id": "A"}, ["line_id", "yield"])
    record_query(workspace, "q1", "SELECT 1", "intent-1", ["n"], [[1]], truncated=False)
    record_query(workspace, "q2", "SELECT 2", "intent-2", ["n"], [[2]], truncated=False)
    html = '<script>window.__ERD_RESULTS__["q1"]</script>'

    recipe = build_recipe(workspace, html)

    assert set(recipe["queries"]) == {"q1"}
    assert recipe["queries"]["q1"] == {"sql": "SELECT 1", "intent": "intent-1"}


def test_build_recipe_lastWinsPerAlias(tmp_path):
    workspace = _make_workspace(tmp_path)
    record_fetch(workspace, snapshot_fingerprint("mes_yield", {"line_id": "A"}), "yield_data", "mes_yield", {"line_id": "A"}, ["line_id"])
    record_fetch(workspace, snapshot_fingerprint("mes_yield", {"line_id": "B"}), "yield_data", "mes_yield", {"line_id": "B"}, ["line_id"])

    recipe = build_recipe(workspace, "<html></html>")

    assert recipe["sources"] == [
        {
            "connector": "mes_yield",
            "params": {"line_id": "B"},
            "alias": "yield_data",
            "expectedColumns": ["line_id"],
        }
    ]


def test_build_recipe_expectedColumnsCarried(tmp_path):
    workspace = _make_workspace(tmp_path)
    record_fetch(workspace, snapshot_fingerprint("mes_yield", {"line_id": "A"}), "yield_data", "mes_yield", {"line_id": "A"}, ["line_id", "yield"])

    recipe = build_recipe(workspace, "<html></html>")

    assert recipe["sources"][0]["expectedColumns"] == ["line_id", "yield"]
    assert recipe["schemaVersion"] == 1


def test_build_recipe_missingColumnsRecord_omitsExpectedColumnsKey(tmp_path):
    workspace = _make_workspace(tmp_path)
    record_fetch(workspace, snapshot_fingerprint("mes_yield", {"line_id": "A"}), "yield_data", "mes_yield", {"line_id": "A"}, ["line_id"])
    # 舊格式記錄(補 columns 前寫入)沒有 columns 鍵——直接改寫落檔模擬。
    records = [{"alias": "legacy_data", "connector": "mes_yield", "params": {"line_id": "C"}}]
    import json

    workspace.fetches_path.write_text(json.dumps(records), encoding="utf-8")

    recipe = build_recipe(workspace, "<html></html>")

    assert recipe["sources"] == [
        {"connector": "mes_yield", "params": {"line_id": "C"}, "alias": "legacy_data"}
    ]
    assert "expectedColumns" not in recipe["sources"][0]


def test_build_recipe_missingQueryRecord_skipsThatQueryId(tmp_path):
    workspace = _make_workspace(tmp_path)
    record_fetch(workspace, snapshot_fingerprint("mes_yield", {"line_id": "A"}), "yield_data", "mes_yield", {"line_id": "A"}, ["line_id"])
    record_query(workspace, "q1", "SELECT 1", "intent-1", ["n"], [[1]], truncated=False)
    html = '<script>window.__ERD_RESULTS__["q1"]window.__ERD_RESULTS__["q2"]</script>'

    recipe = build_recipe(workspace, html)

    assert set(recipe["queries"]) == {"q1"}
