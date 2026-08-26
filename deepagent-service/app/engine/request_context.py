"""請求身分的 ambient 傳遞（contextvar）。source 解析/解密深處(如 internal 複寫的
upload_decrypt.py)不必逐層穿透簽名即可取得當前請求的 userId/sessionId。

MUST 與請求同 task 設定與讀取:contextvar 不跨 thread 傳播,若 source 解析被 offload 到
run_in_executor/to_thread,值會斷——require_* 屆時 fail loud(LookupError)而非回空值。
"""

import contextvars

current_user_id: contextvars.ContextVar[str] = contextvars.ContextVar("current_user_id")
current_session_id: contextvars.ContextVar[str] = contextvars.ContextVar("current_session_id")


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


def set_request_identity(
    user_id: str, session_id: str
) -> tuple[contextvars.Token, contextvars.Token]:
    return current_user_id.set(user_id), current_session_id.set(session_id)


def reset_request_identity(tokens: tuple[contextvars.Token, contextvars.Token]) -> None:
    user_token, session_token = tokens
    current_user_id.reset(user_token)
    current_session_id.reset(session_token)
