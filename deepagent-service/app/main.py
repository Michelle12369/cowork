"""FastAPI 路由層：`/chat`（實際流程見 `app/agent/chat_turn.py` 的 `ChatTurn`）、`/repair`
（見 `app/agent/repair_flow.py`）、`/health`。對接 Java `LangGraphAnalysisProvider`。
"""

import logging
import warnings
from collections.abc import AsyncIterable
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Body, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from app.agent.chat_turn import ChatTurn
from app.agent.connectors.catalog import load_connectors
from app.agent.repair_flow import run_repair
from app.agent.runtime import load_runtime
from app.agent.tracing import init_langfuse
from app.api.auth import RequireBearerToken, UnauthorizedError
from app.api.events import ErrorEvent
from app.api.schemas import ChatRequest, HistoryItem, RepairErrorItem, RepairRequest, SourceItem
from app.config import get_settings
from app.engine.api_snapshot import SnapshotIntegrityError
from utils.logger import configure_logging

# HistoryItem/SourceItem 未在本檔直接使用，僅供測試以 main_module.HistoryItem 取用；
# 列入 __all__ 讓 ruff 視為有意的 re-export，不誤判 F401。
__all__ = ["ChatRequest", "HistoryItem", "RepairErrorItem", "RepairRequest", "SourceItem"]

warnings.filterwarnings("ignore", category=DeprecationWarning)
configure_logging()
logger = logging.getLogger(__name__)

CHAT_INIT_FAILED_CODE = "CHAT_INIT_FAILED"

UNEXPECTED_CHAT_INIT_FAILURE_MESSAGE_TEMPLATE = "對話初始化失敗：{type_name}"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_langfuse(get_settings(), load_runtime())
    yield


app = FastAPI(title="deepagent-service", lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.exception_handler(UnauthorizedError)
async def unauthorized_error_handler(request: Request, exc: UnauthorizedError) -> JSONResponse:
    return JSONResponse(status_code=401, content={"error": "unauthorized"})


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/connectors")
def connectors(_auth: RequireBearerToken) -> list[dict[str, str]]:
    return [
        {"id": connector.connector_id, "name": connector.display_name}
        for connector in load_connectors()
    ]


@app.post("/chat", response_class=EventSourceResponse)
async def chat(
    request: Annotated[ChatRequest, Body()],
    _auth: RequireBearerToken,
    http_request: Request,
) -> AsyncIterable[ServerSentEvent]:
    logger.info(
        "chat request sessionId=%s message_length=%d source_count=%d",
        request.sessionId,
        len(request.message),
        len(request.sources),
    )
    settings = get_settings()
    sso_token = http_request.headers.get(settings.SSO_TOKEN_HEADER)
    sso_url = http_request.headers.get(settings.SSO_URL_HEADER)
    # 手動呼叫 __aenter__（而非 async with）：讓初始化失敗只在這裡轉成乾淨 ErrorEvent；
    # 失敗時 __aenter__ 自己的 except 已完成資源善後（關連線/清 scratch/reset identity）
    # 並重新拋出，不需呼叫 __aexit__。成功後才進 try/finally 交由 __aexit__ 收尾。
    # sso token/url 一律走 header（名稱可配置，見 Settings.SSO_TOKEN_HEADER/SSO_URL_HEADER），
    # NEVER 是 ChatRequest body 欄位。
    turn = ChatTurn(request, sso_token=sso_token, sso_url=sso_url)
    try:
        await turn.__aenter__()
    except (ValueError, SnapshotIntegrityError) as error:
        yield ServerSentEvent(data=ErrorEvent(code=CHAT_INIT_FAILED_CODE, message=str(error)))
        return
    except Exception as error:
        logger.exception("chat turn init failed sessionId=%s", request.sessionId, exc_info=error)
        yield ServerSentEvent(
            data=ErrorEvent(
                code=CHAT_INIT_FAILED_CODE,
                message=UNEXPECTED_CHAT_INIT_FAILURE_MESSAGE_TEMPLATE.format(
                    type_name=type(error).__name__
                ),
            )
        )
        return

    try:
        async for wire_event in turn.stream():
            yield ServerSentEvent(data=wire_event)
            if isinstance(wire_event, ErrorEvent):
                return
        async for wire_event in turn.finalize():
            yield ServerSentEvent(data=wire_event)
            if isinstance(wire_event, ErrorEvent):
                return
    finally:
        await turn.__aexit__(None, None, None)


@app.post("/repair")
async def repair(
    request: Annotated[RepairRequest, Body()],
    _auth: RequireBearerToken,
    http_request: Request,
) -> JSONResponse:
    logger.info("repair request sessionId=%s errorCount=%d", request.sessionId, len(request.errors))
    settings = get_settings()
    sso_token = http_request.headers.get(settings.SSO_TOKEN_HEADER)
    sso_url = http_request.headers.get(settings.SSO_URL_HEADER)
    outcome = await run_repair(request, sso_token=sso_token, sso_url=sso_url)
    if outcome.model_call_failed:
        return JSONResponse(status_code=502, content={"error": "repair model call failed"})
    return JSONResponse(status_code=200, content={"html": outcome.html})
