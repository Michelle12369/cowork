"""解密接縫：repo 內預設 identity；internal 實作存在時整個函式被覆蓋。"""

from pathlib import Path

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
