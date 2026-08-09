"""logging_config 的單元測試:filter 注入、LOG_LEVEL 生效。"""

import logging

from app.config import get_settings
from app.logging_config import SessionIdFilter, configure_logging, current_session_id


def _make_record() -> logging.LogRecord:
    return logging.LogRecord("app.test", logging.INFO, __file__, 1, "hello", None, None)


def test_filter_without_context_sets_dash():
    record = _make_record()
    assert SessionIdFilter().filter(record) is True
    assert record.session_id == "-"


def test_filter_with_context_sets_session_id():
    token = current_session_id.set("session-123")
    try:
        record = _make_record()
        SessionIdFilter().filter(record)
        assert record.session_id == "session-123"
    finally:
        current_session_id.reset(token)


def test_configure_logging_respects_log_level_env(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    configure_logging(get_settings())
    assert logging.getLogger().level == logging.DEBUG


def test_configure_logging_default_level_is_info():
    configure_logging(get_settings())
    assert logging.getLogger().level == logging.INFO


def test_configure_logging_blank_log_level_falls_back_to_info(monkeypatch):
    # properties 檔／env 若給空字串（如範本手誤留白），".upper()" 會傳空字串進 dictConfig
    # root level 直接 ValueError: Unknown level ''，啟動即崩潰——第二道防線：空/空白值 fallback INFO。
    monkeypatch.setenv("LOG_LEVEL", "")
    configure_logging(get_settings())
    assert logging.getLogger().level == logging.INFO
