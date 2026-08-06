"""app/engine/s3.py 的 boto3 client 建構——顯式參數斷言,不依賴 boto3 env 探測。"""

from typing import Any

import pytest

from app.config import get_settings
from app.engine.s3 import build_s3_client


@pytest.fixture(autouse=True)
def _isolate_one_properties(monkeypatch, tmp_path):
    monkeypatch.setenv("ONE_PROPERTIES_PATH", str(tmp_path / "absent.properties"))


def _capture_boto3_client(monkeypatch) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def fake_client(service_name: str, **kwargs: Any) -> str:
        captured["service_name"] = service_name
        captured.update(kwargs)
        return "fake-s3-client"

    monkeypatch.setattr("boto3.client", fake_client)
    return captured


def test_build_s3_client_passes_explicit_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("S3_ENDPOINT", "https://minio.internal:9000")
    monkeypatch.setenv("S3_ACCESS_KEY", "test-access-key")
    monkeypatch.setenv("S3_SECRET_KEY", "test-secret-key")
    get_settings.cache_clear()

    captured = _capture_boto3_client(monkeypatch)

    client = build_s3_client()

    assert client == "fake-s3-client"
    assert captured["service_name"] == "s3"
    assert captured["endpoint_url"] == "https://minio.internal:9000"
    assert captured["aws_access_key_id"] == "test-access-key"
    assert captured["aws_secret_access_key"] == "test-secret-key"
    # region 不是設定項——SDK 必填但寫死,不隨 settings 變動
    assert captured["region_name"] == "aws-global"
    assert captured["config"].s3["addressing_style"] == "path"


def test_build_s3_client_empty_endpoint_becomes_none(monkeypatch, tmp_path):
    monkeypatch.setenv("S3_ENDPOINT", "")
    get_settings.cache_clear()

    captured = _capture_boto3_client(monkeypatch)

    build_s3_client()

    assert captured["endpoint_url"] is None


def test_build_s3_client_reads_settings_fresh_each_call(monkeypatch, tmp_path):
    # 不做 module 單例——每次呼叫都現讀 settings,反映最新的 env/one.properties 值
    captured = _capture_boto3_client(monkeypatch)

    monkeypatch.setenv("S3_ENDPOINT", "https://minio-a.internal:9000")
    get_settings.cache_clear()
    build_s3_client()
    assert captured["endpoint_url"] == "https://minio-a.internal:9000"

    monkeypatch.setenv("S3_ENDPOINT", "https://minio-b.internal:9000")
    get_settings.cache_clear()
    build_s3_client()
    assert captured["endpoint_url"] == "https://minio-b.internal:9000"
