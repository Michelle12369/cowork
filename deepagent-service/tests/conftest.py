import pytest

import app.agent.tracing as tracing_module
from app.agent import session_state
from app.config import get_settings


@pytest.fixture(autouse=True)
def _reset_session_state():
    session_state.reset_for_tests()
    yield
    session_state.reset_for_tests()


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    # get_settings() 是 process 級 lru_cache 單例——測試常在測試本體內 monkeypatch.setenv 後
    # 才呼叫受測程式碼，若不清快取，同一個 worker 內先跑過的測試會讓這裡讀到舊值。清在
    # setup（test_config.py 自己的 fixture 已有等價邏輯，這裡放到全域讓其餘測試檔不必逐一補）。
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _reset_tracing_enabled():
    # _tracing_enabled 是 module 級全域旗標，只有呼叫 init_langfuse() 才會更新——測試檔之間
    # 若一個先跑過「enabled 分支」，殘留的 True 會漏到下一個沒呼叫 init_langfuse 的測試
    # （例如 test_chat.py 的 _build_callbacks gate 測試），使結果依執行順序而異。前後都重置。
    tracing_module._tracing_enabled = False
    yield
    tracing_module._tracing_enabled = False
