"""request_context contextvar:set/reset round-trip、未設定時 fail loud。"""

import pytest

from app.engine.request_context import (
    require_session_id,
    require_user_id,
    reset_request_identity,
    set_request_identity,
)


def test_require_user_id_unset_raises_lookup_error() -> None:
    with pytest.raises(LookupError, match="current_user_id"):
        require_user_id()


def test_require_session_id_unset_raises_lookup_error() -> None:
    with pytest.raises(LookupError, match="current_session_id"):
        require_session_id()


def test_set_request_identity_makes_values_readable() -> None:
    tokens = set_request_identity("user-1", "session-1")
    try:
        assert require_user_id() == "user-1"
        assert require_session_id() == "session-1"
    finally:
        reset_request_identity(tokens)


def test_reset_request_identity_restores_unset_state() -> None:
    tokens = set_request_identity("user-1", "session-1")
    reset_request_identity(tokens)
    with pytest.raises(LookupError, match="current_user_id"):
        require_user_id()
    with pytest.raises(LookupError, match="current_session_id"):
        require_session_id()
