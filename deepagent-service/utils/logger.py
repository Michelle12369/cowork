"""集中式 logging 設定——main.py 啟動時呼叫一次,全服務共用同一格式。"""

import logging
import sys

_LOG_FORMAT = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def configure_logging(level: int = logging.INFO) -> None:
    """root logger 設定固定格式輸出到 stdout;重複呼叫冪等(force 重設既有 handler,
    避免 uvicorn 先掛的 handler 造成雙重輸出)。"""
    logging.basicConfig(
        level=level,
        format=_LOG_FORMAT,
        datefmt=_DATE_FORMAT,
        stream=sys.stdout,
        force=True,
    )
