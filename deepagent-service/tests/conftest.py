import pytest

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
