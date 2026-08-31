"""Connector bearer token 的 secret 參照解析接縫。**internal 環境整檔複寫此檔**(列於
scripts/internal-owned-paths.txt)提供真解析——internal 版把 `secret_ref` 當 Vault 參照，
向 Vault 換出實際 token 值。

repo 版＝環境變數查找(`secret_ref` 即環境變數名，供 dev/測試用純字串 secret)。接縫只交換
`secret_ref` 字串 → secret 值這一件事，呼叫端(`mcp_adapter.py`)不知道也不需要知道背後是
env var 還是 Vault。值本身 NEVER 進 Mongo/wire/log/錯誤訊息——本檔與呼叫端皆只在錯誤訊息中
帶 `secret_ref` 這個參照名，不帶解析出的值。
"""

import os


class SecretResolutionError(Exception):
    """`secret_ref` 解析失敗(缺值/查無此參照)——訊息只含參照名，NEVER 含任何 secret 值。"""


def resolve_secret(secret_ref: str) -> str:
    secret_value = os.environ.get(secret_ref, "")
    if not secret_value:
        raise SecretResolutionError(
            f"secret 參照 '{secret_ref}' 解析失敗——預設實作查環境變數，該變數未設定或為空"
        )
    return secret_value
