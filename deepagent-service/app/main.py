"""FastAPI 路由層：`/chat`（實際流程見 `app/agent/chat_turn.py` 的 `ChatTurn`）、`/repair`
（見 `app/agent/repair_flow.py`）、`/health`。對接 Java `LangGraphAnalysisProvider`。
"""

import contextlib
import logging
from collections.abc import AsyncIterable
from typing import Annotated

from fastapi import Body, FastAPI
from fastapi.responses import JSONResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent

from app.agent.chat_turn import ChatTurn
from app.agent.repair_flow import run_repair
from app.api.events import ErrorEvent
from app.api.schemas import ChatRequest, HistoryItem, RepairErrorItem, RepairRequest, SourceItem

# HistoryItem/SourceItem 未在本檔直接使用，僅供測試以 main_module.HistoryItem 取用；
# 列入 __all__ 讓 ruff 視為有意的 re-export，不誤判 F401。
__all__ = ["ChatRequest", "HistoryItem", "RepairErrorItem", "RepairRequest", "SourceItem"]

logger = logging.getLogger(__name__)

app = FastAPI(title="deepagent-service")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/chat", response_class=EventSourceResponse)
async def chat(request: Annotated[ChatRequest, Body()]) -> AsyncIterable[ServerSentEvent]:
    # NEVER log prompt/資料內容 -- 只記摘要。
    logger.info(
        "chat request sessionId=%s message_length=%d source_count=%d",
        request.sessionId,
        len(request.message),
        len(request.sources),
    )
    async with ChatTurn(request) as turn:
        # 早退（ErrorEvent）時 MUST 明確關閉這兩個 generator，不能靠 GC 順便收——不然
        # `stream_agent_turn` 的 pump task 會繼續跑，見 F1。
        async with contextlib.aclosing(turn.stream()) as stream_events:
            async for wire_event in stream_events:
                yield ServerSentEvent(data=wire_event)
                if isinstance(wire_event, ErrorEvent):
                    return
        async with contextlib.aclosing(turn.finalize()) as finalize_events:
            async for wire_event in finalize_events:
                yield ServerSentEvent(data=wire_event)
                if isinstance(wire_event, ErrorEvent):
                    return


# -- POST /repair: browser-error-driven single-call HTML fix --------------------------------
#
# 前端 RepairOfferCard 在 langgraph-analysis provider 下的落點。工作流程邏輯見
# app/agent/repair_flow.py;此端點只做 request 摘要記錄與 RepairOutcome → HTTP 回應的對應。


@app.post("/repair")
async def repair(request: Annotated[RepairRequest, Body()]) -> JSONResponse:
    # NEVER log html content -- only a summary, same rule as /chat above.
    logger.info("repair request sessionId=%s errorCount=%d", request.sessionId, len(request.errors))
    outcome = await run_repair(request)
    if outcome.model_call_failed:
        return JSONResponse(status_code=502, content={"error": "repair model call failed"})
    if outcome.guard_errors:
        return JSONResponse(status_code=422, content={"errors": outcome.guard_errors})
    return JSONResponse(status_code=200, content={"html": outcome.html})
