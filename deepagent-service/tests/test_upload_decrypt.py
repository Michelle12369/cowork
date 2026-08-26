"""解密接縫:repo 內預設 identity copy(全檔複寫由 internal 版接手)。
唯一行為契約:identity 拷貝正確、以及呼叫前 contextvar 身分必須已設定(活測試)。
"""

from pathlib import Path

import pytest

from app.engine import upload_decrypt
from app.engine.request_context import reset_request_identity, set_request_identity


def test_decrypt_upload_copies_bytes_verbatim_within_identity_context(tmp_path: Path) -> None:
    source = tmp_path / "cipher.bin"
    source.write_bytes(b"\x00\x01payload")
    destination = tmp_path / "plain.bin"
    tokens = set_request_identity("user-1", "session-1")
    try:
        upload_decrypt.decrypt_upload(source, destination)
    finally:
        reset_request_identity(tokens)
    assert destination.read_bytes() == b"\x00\x01payload"
    assert source.exists()  # 接縫不得動來源檔


def test_decrypt_upload_without_identity_context_raises_lookup_error(tmp_path: Path) -> None:
    # 活測試核心:contextvar 未設定時 MUST fail loud,證明解密點確實依賴 request_context。
    source = tmp_path / "cipher.bin"
    source.write_bytes(b"payload")
    destination = tmp_path / "plain.bin"
    with pytest.raises(LookupError, match="current_user_id"):
        upload_decrypt.decrypt_upload(source, destination)
