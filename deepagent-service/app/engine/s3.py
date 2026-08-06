"""boto3 S3 client 建構。engine 層——stdlib + boto3,禁止 LLM 框架。"""

from typing import Any

from app.config import get_settings


def build_s3_client() -> Any:
    """以 Settings 顯式建構(不依賴 boto3 的 env 探測——one.properties 為 internal 單一設定來源)。

    每次呼叫現讀 settings,不做 module 單例——理由同 workspace.resolve_workspace_root()。
    """
    import boto3
    from botocore.config import Config

    settings = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=settings.S3_ENDPOINT or None,
        aws_access_key_id=settings.S3_ACCESS_KEY,
        aws_secret_access_key=settings.S3_SECRET_KEY,
        # S3-compatible 物件儲存不使用 region,SDK 必填故用 aws-global。
        region_name="aws-global",
        # MinIO/內部物件儲存需要 path-style(virtual-hosted 對非 AWS endpoint 解析失敗)
        config=Config(
            s3={"addressing_style": "path"},
            # botocore 1.36+ 預設送 CRC32 flexible checksum,internal S3-compatible 儲存不認且
            # DeleteObjects/PutObject 強制要 Content-MD5;when_required 讓 botocore 只在 API 強制時
            # 算 checksum 並退回 Content-MD5,停送 internal 不接受的 CRC32。
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )
