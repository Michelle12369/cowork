"""集中 logging 設定:dictConfig 一次定義 formatter/filter/handler,uvicorn 三支 logger 一併
納管(統一格式、關 propagate 杜絕雙重輸出)。只在應用進入點(main.py)呼叫 configure_logging()
一次;其餘模組一律只 logging.getLogger(__name__),不自設 handler/level。sessionId 用 contextvar
注入每一行(async 流程隨 task 自動傳播),端點進入時 set。"""

import logging
import logging.config
from contextvars import ContextVar

from app.config import Settings

current_session_id: ContextVar[str | None] = ContextVar("current_session_id", default=None)

LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] [session=%(session_id)s] %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


class SessionIdFilter(logging.Filter):
    """把 contextvar 的 sessionId 掛上每筆 record;無值(啟動期、健康檢查)顯示 '-'。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.session_id = current_session_id.get() or "-"
        return True


def configure_logging(settings: Settings) -> None:
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "filters": {"session_id": {"()": SessionIdFilter}},
            "formatters": {
                "standard": {"format": LOG_FORMAT, "datefmt": LOG_DATE_FORMAT},
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stderr",
                    "formatter": "standard",
                    "filters": ["session_id"],
                },
            },
            "loggers": {
                "uvicorn": {"handlers": ["console"], "level": "INFO", "propagate": False},
                "uvicorn.error": {"handlers": ["console"], "level": "INFO", "propagate": False},
                "uvicorn.access": {"handlers": ["console"], "level": "INFO", "propagate": False},
            },
            "root": {
                "handlers": ["console"],
                "level": settings.LOG_LEVEL.strip().upper() or "INFO",
            },
        }
    )
