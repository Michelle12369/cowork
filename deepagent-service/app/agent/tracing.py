"""Langfuse 啟動時顯式初始化。半套 key 是配置錯誤——啟動即失敗，NEVER 靜默半開。"""

import logging
from typing import Any

from langfuse import Langfuse

from app.config import Settings

logger = logging.getLogger(__name__)


def init_langfuse(settings: Settings, runtime: Any) -> None:
    """public+secret 皆空→no-op；皆有→顯式建構（註冊全域 client），mask 經 runtime seam
    取得（getattr——AgentRuntime 是 Protocol，公司側結構實作不保證有此方法）。"""
    public_key = settings.LANGFUSE_PUBLIC_KEY
    secret_key = settings.LANGFUSE_SECRET_KEY
    if not public_key and not secret_key:
        return
    if not (public_key and secret_key):
        raise RuntimeError(
            "LANGFUSE_PUBLIC_KEY 與 LANGFUSE_SECRET_KEY 必須成對設定（半套是配置錯誤）"
        )
    mask_builder = getattr(runtime, "build_langfuse_mask", None)
    mask_function = mask_builder() if mask_builder is not None else None
    Langfuse(
        public_key=public_key,
        secret_key=secret_key,
        host=settings.LANGFUSE_HOST,
        mask=mask_function,
    )
    logger.info(
        "langfuse initialized host=%s maskProvided=%s",
        settings.LANGFUSE_HOST or "(sdk default)",
        mask_function is not None,
    )
