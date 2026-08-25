"""上傳檔解密接縫。repo 內預設＝identity copy(dev/測試;上傳檔本來就是明文)。

internal 環境放置 `app/engine/upload_decrypt_impl.py`(獨佔路徑,見
scripts/internal-owned-paths.txt)提供真解密——模組存在即整個取代預設實作。
內部未備妥實作時,密文經 identity 直通,後續 xlsx 解析會直接 raise(fail loud,
絕不 silent garbage)。憑證與協定由 internal 實作自理,接縫只交換檔案路徑。
"""

import shutil
from pathlib import Path


def _passthrough_decrypt(ciphertext_path: Path, plaintext_path: Path) -> None:
    shutil.copyfile(ciphertext_path, plaintext_path)


try:
    from app.engine.upload_decrypt_impl import decrypt_upload  # type: ignore[no-redef]
except ImportError:
    decrypt_upload = _passthrough_decrypt
