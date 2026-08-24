"""Langfuse 啟動時顯式初始化。半套 key 是配置錯誤——啟動即失敗，NEVER 靜默半開。"""

import logging
from typing import Any

from langfuse import Langfuse

from app.config import Settings

logger = logging.getLogger(__name__)

# init_langfuse 每次呼叫都會重設；_build_callbacks 靠它決定要不要建 CallbackHandler，
# 不能再看 Settings 的 key 是否有值——runtime 完整接管建構時 client 可能完全不經那兩個 key。
_tracing_enabled: bool = False


def is_tracing_enabled() -> bool:
    return _tracing_enabled


def init_langfuse(settings: Settings, runtime: Any) -> None:
    """runtime 若提供 build_langfuse，完整交給它接管建構（自家 host/auth/mask/wrapper），
    回傳 None 即 tracing 關閉；否則走 OSS 預設路徑（public+secret 皆空→no-op；皆有→顯式
    建構 mask=None；半套是配置錯誤）。"""
    global _tracing_enabled
    # 進入函式先歸零：RuntimeError 分支（半套 key）以下都是 raise 之前的路徑，若不在最前面
    # 重置，上一次呼叫留下的 True 會在這次 fail-loud 之後繼續殘留，讓 is_tracing_enabled()
    # 對外回報「還在追蹤」這個假象。
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
            "LANGFUSE_PUBLIC_KEY 與 LANGFUSE_SECRET_KEY 必須成對設定（半套是配置錯誤）"
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
