"""行為測試 window.mcp() prelude(erd-mcp-runtime 區塊)——不開瀏覽器,直接用 quickjs 跑那段
JS 本體,驗證 skill 教給模型的每一句契約:回傳 undefined、args 經過 JSON round-trip、
handler 只叫一次且不被包 try/catch、未知/晚到的 result id 靜默丟棄、TOOL_ERROR/INVALID_CALL
才轉發到 erd-artifact-error 頻道、沒有自己的 error relay。"""

import json

import pytest

quickjs = pytest.importorskip("quickjs")

from app.engine.results import build_mcp_runtime_script

# 這段是測試用的 host 端替身——window.addEventListener 記錄各 type 的監聽器,
# parent.postMessage 把訊息塞進 posted 陣列供斷言讀取。
_HOST_STUB = """
var window = { __listeners: {} };
window.addEventListener = function (type, fn) {
  if (!window.__listeners[type]) { window.__listeners[type] = []; }
  window.__listeners[type].push(fn);
};
var posted = [];
var parent = { postMessage: function (message, targetOrigin) { posted.push(message); } };
"""


def _extract_script_body(script: str) -> str:
    """從 <script id="..." ...>body</script> 抽出 body 本身,拿去餵給 quickjs eval。"""
    start = script.index(">") + 1
    end = script.rindex("</script>")
    return script[start:end]


def _build_context() -> "quickjs.Context":
    context = quickjs.Context()
    context.eval(_HOST_STUB)
    context.eval(_extract_script_body(build_mcp_runtime_script()))
    return context


def _deliver(context: "quickjs.Context", message_id: str, result: dict) -> None:
    """呼叫存起來的 message 監聽器, 模擬 host bridge(唯一合法來源)送回 erd-mcp-result。"""
    payload = json.dumps({"type": "erd-mcp-result", "id": message_id, "result": result})
    context.eval(f"window.__listeners['message'][0]({{data: {payload}, source: parent}})")


def test_prelude_mcp_returns_undefined_and_posts_call_with_json_round_tripped_args() -> None:
    context = _build_context()
    context.eval("function h(r) {}")
    return_value = context.eval(
        "window.mcp('sales', 'list_orders', {days: 30, skip: undefined, ratio: NaN}, h)"
    )

    assert return_value is None
    posted = json.loads(context.eval("JSON.stringify(posted)"))
    assert len(posted) == 1
    call = posted[0]
    assert call == {
        "type": "erd-mcp-call",
        "id": "1",
        "connector": "sales",
        "tool": "list_orders",
        "args": {"days": 30, "ratio": None},
    }


def test_prelude_handler_called_exactly_once_with_one_argument() -> None:
    context = _build_context()
    context.eval(
        "var handlerCallCount = 0; var handlerArgCount = -1; var handlerLastArg = null;"
        "function h(r) { handlerCallCount++; handlerArgCount = arguments.length; handlerLastArg = r; }"
    )
    context.eval("window.mcp('sales', 'list_orders', {}, h)")
    result_payload = {"data": {"echo": "hi"}}

    _deliver(context, "1", result_payload)
    _deliver(context, "1", result_payload)  # duplicate delivery must be a no-op

    assert context.eval("handlerCallCount") == 1
    assert context.eval("handlerArgCount") == 1
    assert json.loads(context.eval("JSON.stringify(handlerLastArg)")) == result_payload


def test_prelude_ignores_result_from_a_foreign_source() -> None:
    context = _build_context()
    context.eval("var handlerCallCount = 0; function h(r) { handlerCallCount++; }")
    context.eval("window.mcp('sales', 'list_orders', {}, h)")
    payload = json.dumps({"type": "erd-mcp-result", "id": "1", "result": {"data": {}}})

    context.eval(f"window.__listeners['message'][0]({{data: {payload}, source: {{}}}})")

    assert context.eval("handlerCallCount") == 0


def test_prelude_ignores_unknown_result_id() -> None:
    context = _build_context()
    context.eval("var handlerCallCount = 0; function h(r) { handlerCallCount++; }")
    context.eval("window.mcp('sales', 'list_orders', {}, h)")

    _deliver(context, "99", {"data": {}})  # no matching pending call -> dropped, no throw

    assert context.eval("handlerCallCount") == 0


@pytest.mark.parametrize(
    ("code", "expect_forward"),
    [
        ("TOOL_ERROR", True),
        ("INVALID_CALL", True),
        ("RETRYABLE", False),
        ("AUTH", False),
        ("CONNECTOR_UNAVAILABLE", False),
        (None, False),  # a plain success payload never forwards
    ],
)
def test_prelude_forwards_tool_error_and_invalid_call_to_artifact_error_channel_but_not_others(
    code: str | None, expect_forward: bool
) -> None:
    context = _build_context()
    context.eval("function h(r) {}")
    context.eval("window.mcp('sales', 'list_orders', {}, h)")
    result_payload = (
        {"data": {"echo": "hi"}} if code is None else {"error": {"code": code, "message": "x"}}
    )

    _deliver(context, "1", result_payload)

    posted = json.loads(context.eval("JSON.stringify(posted)"))
    error_events = [message for message in posted if message["type"] == "erd-artifact-error"]
    if expect_forward:
        assert len(error_events) == 1
        assert error_events[0]["errors"] == [{"message": f"mcp {code}: x", "line": 0, "col": 0}]
    else:
        assert error_events == []


def test_prelude_does_not_swallow_handler_exceptions() -> None:
    context = _build_context()
    context.eval("function h(r) { throw new Error('handler boom'); }")
    context.eval("window.mcp('sales', 'list_orders', {}, h)")

    with pytest.raises(quickjs.JSException):
        _deliver(context, "1", {"data": {}})

    # the pending entry is deleted before the handler runs, so a second delivery is a no-op,
    # not a second throw.
    _deliver(context, "1", {"data": {}})


def test_prelude_does_not_install_its_own_error_relay() -> None:
    context = _build_context()

    assert context.eval("typeof window.onerror") == "undefined"
    assert sorted(json.loads(context.eval("JSON.stringify(Object.keys(window.__listeners))"))) == [
        "message"
    ]
