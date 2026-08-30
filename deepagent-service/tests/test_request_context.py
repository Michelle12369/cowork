"""request_context contextvar:set/reset round-trip、未設定時 fail loud。"""

import pytest

from app.engine.request_context import (
    require_session_id,
    require_sso_token,
    require_sso_url,
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


def test_require_sso_token_unset_raises_lookup_error() -> None:
    with pytest.raises(LookupError, match="sso_token"):
        require_sso_token()


def test_set_request_identity_with_token_makes_value_readable() -> None:
    tokens = set_request_identity("user-1", "session-1", "sso-token-1")
    try:
        assert require_sso_token() == "sso-token-1"
    finally:
        reset_request_identity(tokens)


def test_reset_request_identity_restores_sso_token_unset_state() -> None:
    tokens = set_request_identity("user-1", "session-1", "sso-token-1")
    reset_request_identity(tokens)
    with pytest.raises(LookupError, match="sso_token"):
        require_sso_token()


def test_set_request_identity_without_token_makes_sso_token_raise() -> None:
    # None 視同未設——dev/無 SSO 環境下 connector 功能必須 fail loud,不能靜默放行。
    tokens = set_request_identity("user-1", "session-1")
    try:
        with pytest.raises(LookupError, match="sso_token"):
            require_sso_token()
    finally:
        reset_request_identity(tokens)


def test_require_sso_url_unset_raises_lookup_error() -> None:
    with pytest.raises(LookupError, match="sso_url"):
        require_sso_url()


def test_set_request_identity_with_url_makes_value_readable() -> None:
    tokens = set_request_identity("user-1", "session-1", "sso-token-1", "https://sso.example/auth")
    try:
        assert require_sso_token() == "sso-token-1"
        assert require_sso_url() == "https://sso.example/auth"
    finally:
        reset_request_identity(tokens)


def test_reset_request_identity_restores_sso_url_unset_state() -> None:
    tokens = set_request_identity("user-1", "session-1", "sso-token-1", "https://sso.example/auth")
    reset_request_identity(tokens)
    with pytest.raises(LookupError, match="sso_token"):
        require_sso_token()
    with pytest.raises(LookupError, match="sso_url"):
        require_sso_url()


def test_set_request_identity_without_url_makes_sso_url_raise() -> None:
    # None 視同未設——同 require_sso_token() 的 fail-loud 規則。
    tokens = set_request_identity("user-1", "session-1", "sso-token-1")
    try:
        with pytest.raises(LookupError, match="sso_url"):
            require_sso_url()
    finally:
        reset_request_identity(tokens)
