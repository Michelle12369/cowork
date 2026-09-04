"""`check_dashboard` tool (app.agent.tools.check) — syntax pass (node --check on every inline
<script>) and contract pass (mcp() call-site scanner) against dashboard.html."""

import shutil
import subprocess

import pytest

from app.agent.connectors.model import Connector, ConnectorTool
from app.agent.tools.check import build_check_tools
from app.engine.replay_manifest import record_landing
from app.engine.workspace import prepare_local_layout

_NODE_MISSING = shutil.which("node") is None

_HEAD_SCRIPTS = (
    '<script src="https://cdn.tailwindcss.com"></script>\n'
    '<script src="https://cdn.jsdelivr.net/npm/echarts@5"></script>\n'
)

_VALID_MCP_CALL = """
function byId(id) { return document.getElementById(id); }
const chart = echarts.init(byId('chart-orders'), 'erd');
window.addEventListener('resize', () => chart.resize());
mcp('sales', 'list_orders', { status: 'open' }, r => {
  if (r.error) {
    console.log(r.error.message);
    return;
  }
  chart.setOption({ series: [{ type: 'bar', data: r.data }] });
});
"""


def _sales_connector() -> Connector:
    return Connector(
        connector_id="sales",
        display_name="Sales",
        tools=(
            ConnectorTool(
                name="list_orders",
                description="list orders",
                input_schema={
                    "type": "object",
                    "properties": {"status": {"type": "string"}},
                    "required": [],
                },
                call=lambda args: [],
            ),
        ),
        skills={},
    )


def _land_default_call(workspace) -> None:
    record_landing(
        workspace,
        connector_id="sales",
        tool_name="list_orders",
        args={"status": "open"},
        land_as="orders",
        observed_columns=["status"],
        input_schema_hash="hash",
        snapshot_sha256="a" * 64,
    )


def _build_dashboard_html(script_body: str) -> str:
    return (
        "<!DOCTYPE html>\n<html>\n<head>\n"
        f"{_HEAD_SCRIPTS}"
        "</head>\n<body>\n"
        '<div id="chart-orders"></div>\n'
        f"<script>\n{script_body}\n</script>\n"
        "</body>\n</html>\n"
    )


def _check_report(workspace, connectors=()) -> str:
    tools = {tool.name: tool for tool in build_check_tools(workspace, connectors)}
    return tools["check_dashboard"].invoke({})


def _finding_lines(report: str) -> list[str]:
    return [line for line in report.splitlines() if line.startswith("- [")]


# -- missing file --------------------------------------------------------------------------


def test_check_dashboard_missing_file_returns_not_found_message(tmp_path) -> None:
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")

    report = _check_report(workspace)

    assert report == "dashboard.html not found — write it first"


# -- happy path -----------------------------------------------------------------------------


@pytest.mark.skipif(_NODE_MISSING, reason="node not installed")
def test_check_dashboard_valid_dashboard_returns_ok(tmp_path) -> None:
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    _land_default_call(workspace)
    workspace.dashboard_path.write_text(_build_dashboard_html(_VALID_MCP_CALL), encoding="utf-8")

    report = _check_report(workspace, (_sales_connector(),))

    assert report == "OK: no findings"


# -- syntax pass -----------------------------------------------------------------------------


@pytest.mark.skipif(_NODE_MISSING, reason="node not installed")
def test_check_dashboard_syntax_error_in_inline_script_reports_syntax_finding(tmp_path) -> None:
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    workspace.dashboard_path.write_text(
        _build_dashboard_html("function broken( {\n  return 1;\n"), encoding="utf-8"
    )

    report = _check_report(workspace, (_sales_connector(),))

    syntax_findings = [line for line in _finding_lines(report) if line.startswith("- [syntax]")]
    assert len(syntax_findings) == 1
    assert "line 0" not in syntax_findings[0]


def test_check_dashboard_node_not_installed_reports_unavailable_finding(tmp_path, monkeypatch) -> None:
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    _land_default_call(workspace)
    workspace.dashboard_path.write_text(_build_dashboard_html(_VALID_MCP_CALL), encoding="utf-8")
    monkeypatch.setattr("app.agent.tools.check.shutil.which", lambda name: None)

    report = _check_report(workspace, (_sales_connector(),))

    assert (
        "- [syntax] line 0: syntax check unavailable (node not installed); contract checks "
        "still ran" in report
    )


def test_check_dashboard_node_check_timeout_reports_timeout_finding(tmp_path, monkeypatch) -> None:
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    _land_default_call(workspace)
    workspace.dashboard_path.write_text(_build_dashboard_html(_VALID_MCP_CALL), encoding="utf-8")

    def _raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="node", timeout=10)

    monkeypatch.setattr("app.agent.tools.check.shutil.which", lambda name: "/usr/bin/node")
    monkeypatch.setattr("app.agent.tools.check.subprocess.run", _raise_timeout)

    report = _check_report(workspace, (_sales_connector(),))

    assert any(
        line.startswith("- [syntax]") and "syntax check timed out" in line
        for line in _finding_lines(report)
    )


# -- contract pass: mcp() call-site checks --------------------------------------------------


def test_check_dashboard_non_literal_connector_or_tool_reports_finding(tmp_path) -> None:
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    _land_default_call(workspace)
    script_body = (
        "const connectorName = 'sales';\n"
        "mcp(connectorName, 'list_orders', { status: 'open' }, r => { if (r.error) return; });\n"
    )
    workspace.dashboard_path.write_text(_build_dashboard_html(script_body), encoding="utf-8")

    report = _check_report(workspace, (_sales_connector(),))

    assert "connector id and tool name must be string literals" in report


def test_check_dashboard_args_not_object_literal_reports_finding(tmp_path) -> None:
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    _land_default_call(workspace)
    script_body = (
        "const requestArgs = { status: 'open' };\n"
        "mcp('sales', 'list_orders', requestArgs, r => { if (r.error) return; });\n"
    )
    workspace.dashboard_path.write_text(_build_dashboard_html(script_body), encoding="utf-8")

    report = _check_report(workspace, (_sales_connector(),))

    assert "args must be an object literal at the call site" in report


def test_check_dashboard_unknown_connector_id_reports_finding(tmp_path) -> None:
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    _land_default_call(workspace)
    script_body = (
        "mcp('unknown', 'list_orders', { status: 'open' }, r => { if (r.error) return; });\n"
    )
    workspace.dashboard_path.write_text(_build_dashboard_html(script_body), encoding="utf-8")

    report = _check_report(workspace, (_sales_connector(),))

    assert "connector 'unknown' is not available this session" in report
    assert "available connectors: sales" in report


def test_check_dashboard_unknown_tool_name_reports_finding(tmp_path) -> None:
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    _land_default_call(workspace)
    script_body = "mcp('sales', 'bogus_tool', { status: 'open' }, r => { if (r.error) return; });\n"
    workspace.dashboard_path.write_text(_build_dashboard_html(script_body), encoding="utf-8")

    report = _check_report(workspace, (_sales_connector(),))

    assert "tool 'bogus_tool' is not a tool of connector 'sales'" in report
    assert "available tools: list_orders" in report


def test_check_dashboard_tool_never_landed_reports_finding(tmp_path) -> None:
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    # No record_landing call this time -- the tool was called in the mcp() call site below but
    # never actually run/landed this session.
    script_body = (
        "mcp('sales', 'list_orders', { status: 'open' }, r => { if (r.error) return; });\n"
    )
    workspace.dashboard_path.write_text(_build_dashboard_html(script_body), encoding="utf-8")

    report = _check_report(workspace, (_sales_connector(),))

    assert "tool was never called (landed) in this session — call it first" in report


def test_check_dashboard_arg_key_set_mismatch_reports_observed_key_sets(tmp_path) -> None:
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    _land_default_call(workspace)  # landed with keys {status}
    script_body = "mcp('sales', 'list_orders', { status: 'open', extra: 1 }, r => { if (r.error) return; });\n"
    workspace.dashboard_path.write_text(_build_dashboard_html(script_body), encoding="utf-8")

    report = _check_report(workspace, (_sales_connector(),))

    assert "do not match any landed call" in report
    assert "observed key sets: {status}" in report


# -- contract pass: forbidden tokens ---------------------------------------------------------


@pytest.mark.parametrize(
    "forbidden_snippet",
    [
        "fetch('/x');",
        "new XMLHttpRequest();",
        "new WebSocket('wss://x');",
        "new EventSource('/x');",
        "window.parent.postMessage('x');",
        "window.top.location;",
        "postMessage('x', '*');",
        "localStorage.getItem('x');",
        "sessionStorage.getItem('x');",
        "document.cookie;",
        "if (typeof mcp === 'function') {}",
        "function mcp(a, b) {}",
        "window.mcp = function() {};",
        "const mcp = function() {};",
        "let mcp = null;",
        "var mcp = null;",
        "console.log(window.__ERD_RESULTS__);",
    ],
)
def test_check_dashboard_forbidden_token_reports_finding(tmp_path, forbidden_snippet: str) -> None:
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    _land_default_call(workspace)
    script_body = f"{_VALID_MCP_CALL}\n{forbidden_snippet}\n"
    workspace.dashboard_path.write_text(_build_dashboard_html(script_body), encoding="utf-8")

    report = _check_report(workspace, (_sales_connector(),))

    assert any(
        line.startswith("- [contract]") and "forbidden token" in line
        for line in _finding_lines(report)
    )


# -- contract pass: HTML/CDN/theme rules -----------------------------------------------------


def test_check_dashboard_disallowed_script_src_reports_finding(tmp_path) -> None:
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    _land_default_call(workspace)
    html = _build_dashboard_html(_VALID_MCP_CALL).replace(
        "</head>", '<script src="https://evil.example.com/x.js"></script>\n</head>'
    )
    workspace.dashboard_path.write_text(html, encoding="utf-8")

    report = _check_report(workspace, (_sales_connector(),))

    assert "disallowed script src 'https://evil.example.com/x.js'" in report


def test_check_dashboard_echarts_init_wrong_theme_reports_finding(tmp_path) -> None:
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    _land_default_call(workspace)
    script_body = _VALID_MCP_CALL.replace("'erd'", "'dark'")
    workspace.dashboard_path.write_text(_build_dashboard_html(script_body), encoding="utf-8")

    report = _check_report(workspace, (_sales_connector(),))

    assert "echarts.init() second argument must be 'erd'" in report


def test_check_dashboard_no_error_handler_reports_finding(tmp_path) -> None:
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    _land_default_call(workspace)
    script_body = (
        "const chart = echarts.init(byId('chart-orders'), 'erd');\n"
        "mcp('sales', 'list_orders', { status: 'open' }, r => {\n"
        "  chart.setOption({ series: [{ type: 'bar', data: r.data }] });\n"
        "});\n"
    )
    workspace.dashboard_path.write_text(_build_dashboard_html(script_body), encoding="utf-8")

    report = _check_report(workspace, (_sales_connector(),))

    assert "no handler checks r.error" in report


def test_check_dashboard_no_mcp_call_reports_finding(tmp_path) -> None:
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    workspace.dashboard_path.write_text(
        _build_dashboard_html("console.log('no data calls here');"), encoding="utf-8"
    )

    report = _check_report(workspace, (_sales_connector(),))

    assert "dashboard has no mcp() call — in connector mode data must come from mcp()" in report
