"""tests/test_replay.py"""

import json
from pathlib import Path

from httpx import ASGITransport, AsyncClient

from app import main as main_module
from app.engine import replay as replay_module
from app.engine.api_fetch import ConnectorFetchError
from app.engine.connectors import ConnectorDefinition, ConnectorRegistry
from app.engine.replay import run_replay

INJECTED_HTML = (
    '<html><head><script id="erd-results-data">window.__ERD_RESULTS__={"stale":{}};'
    "</script></head><body>"
    '<div id="c"></div><script id="erd-bind-resolver">stale-resolver</script>'
    "</body></html>"
)


def _definition(**overrides) -> ConnectorDefinition:
    base = {
        "name": "mes_yield",
        "kind": "data",
        "description": "良率",
        "endpoint": "http://api.internal/yield",
        "method": "GET",
        "auth": "",
        "params": {},
        "limits": {"timeout_s": 5, "max_bytes": 1_000_000, "max_rows": 1000},
    }
    base.update(overrides)
    return ConnectorDefinition.model_validate(base)


def _registry(*definitions: ConnectorDefinition) -> ConnectorRegistry:
    return ConnectorRegistry(list(definitions))


def _recipe(
    sql: str = "SELECT tool, yld FROM yield_data",
    expected_columns: tuple[str, ...] | None = ("tool", "yld"),
) -> dict:
    source = {"connector": "mes_yield", "params": {"line_id": "A"}, "alias": "yield_data"}
    if expected_columns is not None:
        source["expectedColumns"] = list(expected_columns)
    return {
        "schemaVersion": 1,
        "sources": [source],
        "queries": {"q1": {"sql": sql, "intent": "各機台良率"}},
    }


def _fake_fetch_rows(rows: list[dict]):
    def _fake(definition, params, transport=None):
        return json.dumps(rows).encode()

    return _fake


# ── happy path ─────────────────────────────────────────────────────────────


def test_run_replay_happyPath_injectsFreshResultsAndStripsOldBlocks(monkeypatch) -> None:
    monkeypatch.setattr(
        replay_module, "execute_fetch", _fake_fetch_rows([{"tool": "A", "yld": 0.9}])
    )

    outcome = run_replay(_recipe(), INJECTED_HTML, _registry(_definition()))

    assert outcome.error_code is None
    assert outcome.html is not None
    assert '"q1"' in outcome.html
    assert '"tool":"A"' in outcome.html.replace(" ", "")
    assert 'id="erd-bind-resolver"' in outcome.html
    assert "data-erd-replay-hide" in outcome.html
    assert "[data-erd-narrative]{display:none}" in outcome.html
    # old injected blocks (stale results/resolver) must be gone, replaced by fresh ones.
    assert "stale-resolver" not in outcome.html
    assert '"stale"' not in outcome.html


def test_run_replay_zeroRows_rendersThrough(monkeypatch) -> None:
    monkeypatch.setattr(
        replay_module, "execute_fetch", _fake_fetch_rows([{"tool": "A", "yld": 0.9}])
    )
    recipe = _recipe(sql="SELECT tool, yld FROM yield_data WHERE 1 = 0")

    outcome = run_replay(recipe, INJECTED_HTML, _registry(_definition()))

    assert outcome.error_code is None
    assert outcome.html is not None
    assert '"rows":[]' in outcome.html.replace(" ", "")


# ── error paths ────────────────────────────────────────────────────────────


def test_run_replay_connectorMissingFromRegistry_returnsSourceGone() -> None:
    outcome = run_replay(_recipe(), INJECTED_HTML, _registry())

    assert outcome.html is None
    assert outcome.error_code == "SOURCE_GONE"
    assert "mes_yield" in outcome.error_message


def test_run_replay_fetchRaises_returnsFetchFailed(monkeypatch) -> None:
    def _raise(definition, params, transport=None):
        raise ConnectorFetchError("connector mes_yield 回應 HTTP 503")

    monkeypatch.setattr(replay_module, "execute_fetch", _raise)

    outcome = run_replay(_recipe(), INJECTED_HTML, _registry(_definition()))

    assert outcome.html is None
    assert outcome.error_code == "FETCH_FAILED"
    assert "mes_yield" in outcome.error_message


def test_run_replay_expectedColumnMissing_returnsSourceSchemaChanged(monkeypatch) -> None:
    # fetched payload no longer has "yld" -- expectedColumns from the recipe is not a subset.
    monkeypatch.setattr(replay_module, "execute_fetch", _fake_fetch_rows([{"tool": "A"}]))
    recipe = _recipe(sql="SELECT tool FROM yield_data")

    outcome = run_replay(recipe, INJECTED_HTML, _registry(_definition()))

    assert outcome.html is None
    assert outcome.error_code == "SOURCE_SCHEMA_CHANGED"
    assert "yield_data" in outcome.error_message
    assert "yld" in outcome.error_message


def test_run_replay_brokenSql_returnsSourceSchemaChanged(monkeypatch) -> None:
    monkeypatch.setattr(
        replay_module, "execute_fetch", _fake_fetch_rows([{"tool": "A", "yld": 0.9}])
    )
    recipe = _recipe(sql="SELECT nonexistent_column FROM yield_data", expected_columns=None)

    outcome = run_replay(recipe, INJECTED_HTML, _registry(_definition()))

    assert outcome.html is None
    assert outcome.error_code == "SOURCE_SCHEMA_CHANGED"
    assert "q1" in outcome.error_message


def test_run_replay_malformedRecipe_returnsInvalidRecipe() -> None:
    outcome = run_replay({"schemaVersion": 1}, INJECTED_HTML, _registry())

    assert outcome.html is None
    assert outcome.error_code == "INVALID_RECIPE"


def test_run_replay_legacySourceWithoutExpectedColumns_skipsSchemaCheck(monkeypatch) -> None:
    # Legacy recipe (pre-expectedColumns) -- current live columns differ wildly from what a
    # subset check might have compared against, but absence of the key means: don't check.
    monkeypatch.setattr(
        replay_module, "execute_fetch", _fake_fetch_rows([{"tool": "A", "yld": 0.9}])
    )
    recipe = _recipe(expected_columns=None)

    outcome = run_replay(recipe, INJECTED_HTML, _registry(_definition()))

    assert outcome.error_code is None
    assert outcome.html is not None


# ── tempdir hygiene ────────────────────────────────────────────────────────


def test_run_replay_tempdirCleanedUpAfterCall(monkeypatch) -> None:
    import tempfile

    created_dirs: list[str] = []

    class _RecordingTempDir(tempfile.TemporaryDirectory):
        def __enter__(self):
            path = super().__enter__()
            created_dirs.append(path)
            return path

    monkeypatch.setattr(replay_module.tempfile, "TemporaryDirectory", _RecordingTempDir)
    monkeypatch.setattr(
        replay_module, "execute_fetch", _fake_fetch_rows([{"tool": "A", "yld": 0.9}])
    )

    outcome = run_replay(_recipe(), INJECTED_HTML, _registry(_definition()))

    assert outcome.error_code is None
    assert len(created_dirs) == 1
    assert not Path(created_dirs[0]).exists()


# ── endpoint contract ──────────────────────────────────────────────────────


async def _post_replay(recipe: dict, html: str = INJECTED_HTML) -> tuple[int, dict]:
    payload = {"recipe": recipe, "html": html}
    transport = ASGITransport(app=main_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/replay", json=payload)
    return response.status_code, response.json()


async def test_endpoint_replay_success_returnsHtmlNoError(monkeypatch) -> None:
    monkeypatch.setattr(
        main_module, "load_connector_registry", lambda path: _registry(_definition())
    )
    monkeypatch.setattr(
        replay_module, "execute_fetch", _fake_fetch_rows([{"tool": "A", "yld": 0.9}])
    )

    status_code, body = await _post_replay(_recipe())

    assert status_code == 200
    assert body.get("error") is None
    assert body["html"] is not None
    assert '"q1"' in body["html"]


async def test_endpoint_replay_sourceGone_returnsErrorContractNoHtml(monkeypatch) -> None:
    monkeypatch.setattr(main_module, "load_connector_registry", lambda path: _registry())

    status_code, body = await _post_replay(_recipe())

    assert status_code == 200
    assert body.get("html") is None
    assert body["error"]["code"] == "SOURCE_GONE"
    assert "mes_yield" in body["error"]["message"]
