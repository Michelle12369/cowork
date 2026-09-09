import contextvars

current_user_id: contextvars.ContextVar[str] = contextvars.ContextVar("current_user_id")
current_session_id: contextvars.ContextVar[str] = contextvars.ContextVar("current_session_id")
current_sso_token: contextvars.ContextVar[str | None] = contextvars.ContextVar("current_sso_token")
current_sso_url: contextvars.ContextVar[str | None] = contextvars.ContextVar("current_sso_url")


def require_user_id() -> str:
    try:
        return current_user_id.get()
    except LookupError as missing:
        raise LookupError(
            "current_user_id is not set -- source resolution must run in the same task as the "
            "/chat request"
        ) from missing


def require_session_id() -> str:
    try:
        return current_session_id.get()
    except LookupError as missing:
        raise LookupError(
            "current_session_id is not set -- source resolution must run in the same task as "
            "the /chat request"
        ) from missing


def require_sso_token() -> str:
    try:
        token = current_sso_token.get()
    except LookupError as missing:
        raise LookupError(
            "current_sso_token is not set -- connector calls must run in the same task as the "
            "/chat request"
        ) from missing
    if token is None:
        raise LookupError(
            "current_sso_token is not set (value is None) -- connector features are unavailable"
        )
    return token


def require_sso_url() -> str:
    try:
        url = current_sso_url.get()
    except LookupError as missing:
        raise LookupError(
            "current_sso_url is not set -- connector calls must run in the same task as the "
            "/chat request"
        ) from missing
    if url is None:
        raise LookupError(
            "current_sso_url is not set (value is None) -- connector features are unavailable"
        )
    return url


def set_request_identity(
    user_id: str,
    session_id: str,
    sso_token: str | None = None,
    sso_url: str | None = None,
) -> tuple[contextvars.Token, contextvars.Token, contextvars.Token, contextvars.Token]:
    return (
        current_user_id.set(user_id),
        current_session_id.set(session_id),
        current_sso_token.set(sso_token),
        current_sso_url.set(sso_url),
    )


def reset_request_identity(
    tokens: tuple[contextvars.Token, contextvars.Token, contextvars.Token, contextvars.Token],
) -> None:
    user_token, session_token, sso_token, sso_url_token = tokens
    current_user_id.reset(user_token)
    current_session_id.reset(session_token)
    current_sso_token.reset(sso_token)
    current_sso_url.reset(sso_url_token)
