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
    '<div id="c"></div>'
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
    # old injected blocks (stale results) must be gone, replaced by fresh ones.
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


def test_run_replay_duplicateColumnNames_dedupedNoSilentLoss(monkeypatch) -> None:
    """recipe SQL 產重複輸出欄名(如 `SELECT ... AS a, ... AS a`)——共用 results.py 的
    build_result_record 去重加後綴,不得靜默丟欄(dict(zip) 手組的舊路徑會丟)。"""
    monkeypatch.setattr(
        replay_module, "execute_fetch", _fake_fetch_rows([{"tool": "A", "yld": 0.9}])
    )
    recipe = _recipe(sql="SELECT tool AS a, yld AS a FROM yield_data", expected_columns=None)

    outcome = run_replay(recipe, INJECTED_HTML, _registry(_definition()))

    assert outcome.error_code is None
    compact = outcome.html.replace(" ", "")
    assert '"columns":["a","a_2"]' in compact
    assert '"a":"A"' in compact
    assert '"a_2":0.9' in compact


# ── path traversal ─────────────────────────────────────────────────────────


def test_run_replay_aliasWithDotDot_returnsInvalidRecipe_neverFetches(monkeypatch) -> None:
    def _must_not_be_called(*args, **kwargs):
        raise AssertionError("execute_fetch must not run for a malicious alias")

    monkeypatch.setattr(replay_module, "execute_fetch", _must_not_be_called)
    recipe = _recipe()
    recipe["sources"][0]["alias"] = "../evil"

    outcome = run_replay(recipe, INJECTED_HTML, _registry(_definition()))

    assert outcome.html is None
    assert outcome.error_code == "INVALID_RECIPE"


def test_run_replay_aliasAbsolutePath_returnsInvalidRecipe_neverFetches(monkeypatch) -> None:
    def _must_not_be_called(*args, **kwargs):
        raise AssertionError("execute_fetch must not run for a malicious alias")

    monkeypatch.setattr(replay_module, "execute_fetch", _must_not_be_called)
    recipe = _recipe()
    recipe["sources"][0]["alias"] = "/etc/x"

    outcome = run_replay(recipe, INJECTED_HTML, _registry(_definition()))

    assert outcome.html is None
    assert outcome.error_code == "INVALID_RECIPE"


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


def test_run_replay_unexpectedInternalError_failsClosedAndCleansTempdir(monkeypatch) -> None:
    """任何未預期例外(此處模擬 HTML 後處理步驟炸裂)一律 fail-closed 成通用訊息,不洩內部
    細節;tmpdir 仍照常清掉(with-block 在例外傳到外層 except 之前就已正常退出)。"""
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

    def _boom(html: str) -> str:
        raise RuntimeError("internal secret detail: db password xyz")

    monkeypatch.setattr(replay_module, "strip_injected_blocks", _boom)

    outcome = run_replay(_recipe(), INJECTED_HTML, _registry(_definition()))

    assert outcome.html is None
    assert outcome.error_code == "REPLAY_INTERNAL"
    assert "xyz" not in (outcome.error_message or "")
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
        main_module, "load_registry_from_settings", lambda: _registry(_definition())
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
    monkeypatch.setattr(main_module, "load_registry_from_settings", lambda: _registry())

    status_code, body = await _post_replay(_recipe())

    assert status_code == 200
    assert body.get("html") is None
    assert body["error"]["code"] == "SOURCE_GONE"
    assert "mes_yield" in body["error"]["message"]
