"""quickjs 選配相依的可用性探測——本 package 唯一的擁有者。

消費端 MUST 以模組屬性存取（`js_runtime.QUICKJS_AVAILABLE`），NEVER 用
`from .js_runtime import QUICKJS_AVAILABLE`——後者在 import 期就把值快照下來，
測試的 monkeypatch 會靜默失效。
"""

try:
    import quickjs

    QUICKJS_AVAILABLE = True
except ImportError:  # pragma: no cover
    quickjs = None
    QUICKJS_AVAILABLE = False
