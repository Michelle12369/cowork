"""`/chat`、`/repair` 的 inbound bearer 驗證——Java 端固定 token 打進來(compose 用同一個
AGENT_API_BEARER_TOKEN 餵兩邊)。`/health` 豁免,不掛此 dependency。"""

import secrets
from typing import Annotated

from fastapi import Depends, Header

from app.config import get_settings


class UnauthorizedError(Exception):
    """token 缺、格式錯或不符——由 main.py 的 exception handler 轉 401 JSON,不洩細節。"""


async def require_bearer_token(authorization: Annotated[str | None, Header()] = None) -> None:
    expected_token = get_settings().AGENT_API_BEARER_TOKEN
    # 未設定=一律 401(不是放行)——否則空 expected 對上空 Bearer 會 compare_digest 相等而誤放。
    if not expected_token:
        raise UnauthorizedError
    if authorization is None or not authorization.startswith("Bearer "):
        raise UnauthorizedError
    token = authorization.removeprefix("Bearer ")
    # 常數時間比較,避免逐字元比對洩漏 timing side channel。
    if not secrets.compare_digest(token, expected_token):
        raise UnauthorizedError


RequireBearerToken = Annotated[None, Depends(require_bearer_token)]
