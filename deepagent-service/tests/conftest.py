import os

import pytest

import app.agent.tracing as tracing_module
from app.agent import session_state
from app.config import get_settings


@pytest.fixture(autouse=True)
def _isolate_one_properties():
    # ONE_PROPERTIES_PATH 預設是 CWD 下的 one-local.properties——pytest 的 CWD 正是
    # deepagent-service/，開發者本機的真實 one-local.properties 會污染測試（斷言預設值的測試
    # 讀到本機值）。一律指到不存在的路徑保持 hermetic；要測檔案行為的測試自行 setenv 覆寫。
    # 刻意不用 monkeypatch fixture：autouse fixture 依賴共享的 monkeypatch 會把它的
    # teardown 排到所有 autouse fixture 之後，測試內 setenv 的值（如 AGENT_RUNTIME=internal）
    # 會在 _reset_session_state 的 teardown 重建 runtime 時仍然生效而炸掉——手動 save/restore。
    # 本 fixture MUST 排在 conftest 最前，讓 _reset_session_state 的 setup 也在隔離下執行。
    saved_path = os.environ.get("ONE_PROPERTIES_PATH")
    os.environ["ONE_PROPERTIES_PATH"] = "/nonexistent/one.properties.test-isolation"
    yield
    if saved_path is None:
        os.environ.pop("ONE_PROPERTIES_PATH", None)
    else:
        os.environ["ONE_PROPERTIES_PATH"] = saved_path


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


@pytest.fixture(autouse=True)
def _isolate_final_critic():
    # ERD_FINAL_CRITIC 的 Settings 預設是 "true"——既有 e2e 腳本測試（scripted_flow 系列）
    # 沒有為 critic 準備額外的回合，沿用預設會讓那些測試意外多跑一次 critic 模型呼叫、
    # 多發一個 STEP 事件，事件序列跟著跑掉。一律預設關閉，要測 critic 行為的測試自行
    # monkeypatch.setenv("ERD_FINAL_CRITIC", "true") 開啟。手動 save/restore（不用
    # monkeypatch fixture）比照 `_isolate_one_properties` 的理由：teardown 順序要可控。
    saved_value = os.environ.get("ERD_FINAL_CRITIC")
    os.environ["ERD_FINAL_CRITIC"] = "false"
    yield
    if saved_value is None:
        os.environ.pop("ERD_FINAL_CRITIC", None)
    else:
        os.environ["ERD_FINAL_CRITIC"] = saved_value
