"""FastAPI 路由層：`/chat`（實際流程見 `app/agent/chat_turn.py` 的 `ChatTurn`）、`/repair`
（見 `app/agent/repair_flow.py`）、`/health`。對接 Java `LangGraphAnalysisProvider`。
"""

import logging
import warnings
from collections.abc import AsyncIterable
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Body, FastAPI
from fastapi.responses import JSONResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from app.agent.chat_turn import ChatTurn
from app.agent.repair_flow import run_repair
from app.agent.runtime import load_runtime
from app.agent.tracing import init_langfuse
from app.api.events import ErrorEvent
from app.api.schemas import ChatRequest, HistoryItem, RepairErrorItem, RepairRequest, SourceItem
from app.config import get_settings
from app.utils.logger import configure_logging

# HistoryItem/SourceItem 未在本檔直接使用，僅供測試以 main_module.HistoryItem 取用；
# 列入 __all__ 讓 ruff 視為有意的 re-export，不誤判 F401。
__all__ = ["ChatRequest", "HistoryItem", "RepairErrorItem", "RepairRequest", "SourceItem"]

warnings.filterwarnings("ignore", category=DeprecationWarning)
configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_langfuse(get_settings(), load_runtime())
    yield


app = FastAPI(title="deepagent-service", lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/chat", response_class=EventSourceResponse)
async def chat(request: Annotated[ChatRequest, Body()]) -> AsyncIterable[ServerSentEvent]:
    logger.info(
        "chat request sessionId=%s message_length=%d source_count=%d",
        request.sessionId,
        len(request.message),
        len(request.sources),
    )
    async with ChatTurn(request) as turn:
        async for wire_event in turn.stream():
            yield ServerSentEvent(data=wire_event)
            if isinstance(wire_event, ErrorEvent):
                return
        async for wire_event in turn.finalize():
            yield ServerSentEvent(data=wire_event)
            if isinstance(wire_event, ErrorEvent):
                return


@app.post("/repair")
async def repair(request: Annotated[RepairRequest, Body()]) -> JSONResponse:
    logger.info("repair request sessionId=%s errorCount=%d", request.sessionId, len(request.errors))
    outcome = await run_repair(request)
    if outcome.model_call_failed:
        return JSONResponse(status_code=502, content={"error": "repair model call failed"})
    return JSONResponse(status_code=200, content={"html": outcome.html})
