"""Langfuse 在啟動時明確初始化一次. 只設定一半的 key 算設定錯誤, 要讓啟動直接失敗, 不要悄悄跑在半開的狀態."""

import logging
from typing import Any

from langfuse import Langfuse

from app.config import Settings

logger = logging.getLogger(__name__)

# init_langfuse 每次呼叫都會重設這個旗標. 要不要建立 CallbackHandler 一律看這個旗標,
# 不能再看 Settings 裡的 key 有沒有值, 因為 runtime 完整接管建構時 client 可能完全不經過那兩個 key.
_tracing_enabled: bool = False


def is_tracing_enabled() -> bool:
    return _tracing_enabled


def init_langfuse(settings: Settings, runtime: Any) -> None:
    """如果 runtime 有提供 build_langfuse, 就整個交給它接管建構過程(自家的 host, auth, 遮罩,
    wrapper), 回傳 None 就代表 tracing 關閉. 否則走 OSS 內建路徑: public 和 secret 兩個 key 都
    空就什麼都不做, 兩個都有就用 mask=None 明確建構, 只設定一個算是設定錯誤."""
    global _tracing_enabled
    # 一進函式就先把旗標歸零. 下面半套 key 的設定錯誤會直接 raise, 如果不在最前面重置,
    # 上一次呼叫留下的 True 會在這次失敗之後繼續殘留, 讓 is_tracing_enabled() 對外謊報還在追蹤中.
    _tracing_enabled = False

    builder = getattr(runtime, "build_langfuse", None)
    if builder is not None:
        client = builder()
        _tracing_enabled = client is not None
        logger.info("langfuse initialized source=runtime enabled=%s", _tracing_enabled)
        return

    public_key = settings.LANGFUSE_PUBLIC_KEY
    secret_key = settings.LANGFUSE_SECRET_KEY
    if not public_key and not secret_key:
        return
    if not (public_key and secret_key):
        raise RuntimeError(
            "LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY must be set together (partial configuration is invalid)"
        )
    Langfuse(
        public_key=public_key,
        secret_key=secret_key,
        host=settings.LANGFUSE_HOST,
        mask=None,
    )
    _tracing_enabled = True
    logger.info(
        "langfuse initialized source=default host=%s",
        settings.LANGFUSE_HOST or "(sdk default)",
    )
