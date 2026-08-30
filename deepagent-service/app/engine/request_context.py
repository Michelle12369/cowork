"""請求身分的 ambient 傳遞（contextvar）。source 解析/解密深處(如 internal 複寫的
upload_decrypt.py)不必逐層穿透簽名即可取得當前請求的 userId/sessionId/ssoToken/ssoUrl。

MUST 與請求同 task 設定與讀取:contextvar 不跨 thread 傳播,若 source 解析被 offload 到
run_in_executor/to_thread,值會斷——require_* 屆時 fail loud(LookupError)而非回空值。

ssoToken/ssoUrl 皆由 Java 端經 X-SSO-Token/X-SSO-Url HTTP header 傳入(main.py 的
/chat、/repair handler),NEVER 走 JSON body。ssoToken NEVER 進 log/prompt/recipe/落盤——
僅供 connector 呼叫時逐請求附加；ssoUrl 敏感度較低但同樣 NEVER 進 log,理由同上。
"""

import contextvars

current_user_id: contextvars.ContextVar[str] = contextvars.ContextVar("current_user_id")
current_session_id: contextvars.ContextVar[str] = contextvars.ContextVar("current_session_id")
# dev/無 SSO 環境值為 None——與「未設定」同義,require_sso_token()/require_sso_url() 皆 fail loud。
current_sso_token: contextvars.ContextVar[str | None] = contextvars.ContextVar("current_sso_token")
current_sso_url: contextvars.ContextVar[str | None] = contextvars.ContextVar("current_sso_url")


def require_user_id() -> str:
    try:
        return current_user_id.get()
    except LookupError as missing:
        raise LookupError(
            "current_user_id 未設定——source 解析必須在 /chat 請求的同一 task 內執行"
        ) from missing


def require_session_id() -> str:
    try:
        return current_session_id.get()
    except LookupError as missing:
        raise LookupError(
            "current_session_id 未設定——source 解析必須在 /chat 請求的同一 task 內執行"
        ) from missing


def require_sso_token() -> str:
    try:
        token = current_sso_token.get()
    except LookupError as missing:
        raise LookupError(
            "current_sso_token 未設定——connector 呼叫必須在 /chat 請求的同一 task 內執行"
        ) from missing
    if token is None:
        # dev/無 SSO 環境本來就不該讓 connector 功能靜默放行,fail loud 而非回傳空字串。
        raise LookupError("current_sso_token 未設定(值為 None)——connector 功能不可用")
    return token


def require_sso_url() -> str:
    try:
        url = current_sso_url.get()
    except LookupError as missing:
        raise LookupError(
            "current_sso_url 未設定——connector 呼叫必須在 /chat 請求的同一 task 內執行"
        ) from missing
    if url is None:
        # 同 require_sso_token():dev/無 SSO 環境不該讓 connector 功能靜默放行。
        raise LookupError("current_sso_url 未設定(值為 None)——connector 功能不可用")
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
