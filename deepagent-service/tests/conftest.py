import os
import shutil
from pathlib import Path

import pytest

import app.agent.tracing as tracing_module
from app.agent import session_state
from app.config import get_settings

# /chat、/repair 現在強制驗證 inbound bearer——測試灌固定值,呼叫端統一帶
# `Authorization: Bearer {TEST_BEARER_TOKEN}`;其他測試檔可 `from tests.conftest import TEST_BEARER_TOKEN`。
TEST_BEARER_TOKEN = "test-bearer-token"


@pytest.fixture(autouse=True)
def _set_agent_api_bearer_token(monkeypatch):
    # 驗證「token 未設定→lifespan 炸」的測試自行在測試本體 delenv 覆寫這個 autouse 預設值。
    monkeypatch.setenv("AGENT_API_BEARER_TOKEN", TEST_BEARER_TOKEN)
    yield


@pytest.fixture(autouse=True)
def _isolate_one_properties():
    # ONE_PROPERTIES_PATH 預設是 CWD 下的 one-local.properties——pytest 的 CWD 正是
    # deepagent-service/,開發者本機的真實 one-local.properties 會污染測試(斷言預設值的測試
    # 讀到本機值)。一律指到不存在的路徑保持 hermetic;要測檔案行為的測試自行 setenv 覆寫。
    # 刻意不用 monkeypatch fixture:autouse fixture 依賴共享的 monkeypatch 會把它的
    # teardown 排到所有 autouse fixture 之後,測試內 setenv 的值(如 AGENT_RUNTIME=internal)
    # 會在 _reset_session_state 的 teardown 重建 runtime 時仍然生效而炸掉——手動 save/restore。
    # 本 fixture MUST 排在 conftest 最前,讓 _reset_session_state 的 setup 也在隔離下執行。
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
    # 才呼叫受測程式碼,若不清快取,同一個 worker 內先跑過的測試會讓這裡讀到舊值。清在
    # setup(test_config.py 自己的 fixture 已有等價邏輯,這裡放到全域讓其餘測試檔不必逐一補)。
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _reset_tracing_enabled():
    # _tracing_enabled 是 module 級全域旗標,只有呼叫 init_langfuse() 才會更新——測試檔之間
    # 若一個先跑過「enabled 分支」,殘留的 True 會漏到下一個沒呼叫 init_langfuse 的測試
    # (例如 test_chat.py 的 _build_callbacks gate 測試),使結果依執行順序而異。前後都重置。
    tracing_module._tracing_enabled = False
    yield
    tracing_module._tracing_enabled = False


@pytest.fixture()
def stub_decrypt_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """解密由 internal 整檔複寫、真 impl 吃密文,管線測試 stub 成 identity 以只測轉檔+cache+duck。
    非 autouse——僅供驅動 xlsx 經 resolve_source_path 的管線測試選用,不可影響
    test_upload_decrypt.py(該檔測的正是 decrypt_upload 本身的 identity 契約)。"""

    def _identity_decrypt(ciphertext_path: Path, plaintext_path: Path) -> None:
        shutil.copyfile(ciphertext_path, plaintext_path)

    monkeypatch.setattr("app.engine.source_cache.decrypt_upload", _identity_decrypt)
