"""解密接縫：repo 內預設 identity；internal 實作存在時整個函式被覆蓋。"""

import importlib
from pathlib import Path

import pytest

from app.engine import upload_decrypt


def test_decrypt_upload_default_copies_bytes_verbatim(tmp_path: Path) -> None:
    source = tmp_path / "cipher.bin"
    source.write_bytes(b"\x00\x01payload")
    destination = tmp_path / "plain.bin"
    upload_decrypt.decrypt_upload(source, destination)
    assert destination.read_bytes() == b"\x00\x01payload"
    assert source.exists()  # 接縫不得動來源檔


def test_decrypt_upload_is_overridable_seam() -> None:
    # internal 以同名模組覆蓋;repo 端只驗證預設實作可被替換的形狀(callable 模組屬性)
    assert callable(upload_decrypt.decrypt_upload)


def test_decrypt_upload_broken_impl_dependency_raises_not_silently_passthrough() -> None:
    # impl 模組存在但自身 import chain 壞掉(如缺 internal crypto SDK)MUST 原樣炸出;
    # 絕不能被誤吞成「模組不存在」而靜默退回 identity——密文當明文直通是本接縫的
    # fail-loud 禁區。cleanup 用 try/finally 確保即使斷言失敗也不留下壞檔案毒害其他測試。
    engine_dir = Path(upload_decrypt.__file__).parent
    broken_impl_path = engine_dir / "upload_decrypt_impl.py"
    assert not broken_impl_path.exists(), "測試前置：impl 檔案不應已存在"

    try:
        broken_impl_path.write_text("import definitely_missing_dependency_xyz\n")
        importlib.invalidate_caches()
        with pytest.raises(ModuleNotFoundError, match="definitely_missing_dependency_xyz"):
            importlib.reload(upload_decrypt)
    finally:
        broken_impl_path.unlink(missing_ok=True)
        importlib.invalidate_caches()
        importlib.reload(upload_decrypt)

    assert upload_decrypt.decrypt_upload is upload_decrypt._passthrough_decrypt
