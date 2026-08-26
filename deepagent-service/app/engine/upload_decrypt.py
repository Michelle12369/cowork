"""上傳檔解密。**internal 環境整檔複寫此檔**(列於 scripts/internal-owned-paths.txt)提供
真解密——internal 版自 request_context.require_user_id() 取 userId 當解密 API payload。

repo 版＝identity copy(dev/測試;上傳檔本來就是明文),但仍呼叫 require_user_id() 一次作為
「contextvar 在解密點確實可取」的活測試:若未來 source 解析被 offload 到 thread 打斷傳遞,
dev/CI 會當場 fail 而非等到 internal 才爆。憑證與協定由 internal 版自理。
"""

import shutil
from pathlib import Path

from app.engine.request_context import require_user_id


def decrypt_upload(ciphertext_path: Path, plaintext_path: Path) -> None:
    require_user_id()  # 活測試:確保 contextvar 在此點可取(值本身 identity 路徑不需要)
    shutil.copyfile(ciphertext_path, plaintext_path)
