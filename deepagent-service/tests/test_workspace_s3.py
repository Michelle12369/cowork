"""S3WorkspaceStore:lazy pull(prepare)/turn-end push(persist)。用 moto 起一個 in-process 假
S3(不是 botocore Stubber)——本模組的操作是「list 一個 prefix、依動態數量的 key 逐一下載/上傳」,
Stubber 需要為每一次 API 呼叫手動排隊固定回應,對可變數量的物件列表來說又囉唆又脆弱;moto 直接
behaviour-fake 整個 S3(真的 list/put/get,真的 prefix 語意),roundtrip 測試寫起來就是自然的
「寫入 → 讀回 → 斷言內容」,更貼近我們要驗證的東西(語意正確性,不是「呼叫了幾次 API」)。
"""

import logging
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from app.engine.workspace import LocalWorkspaceStore
from app.engine.workspace_s3 import S3WorkspaceStore, build_workspace_store

_BUCKET = "erd-cowork-test"
_PREFIX = "workspace/"


@pytest.fixture
def s3_client():
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=_BUCKET)
        yield client


def test_roundtrip_persist_then_prepare_on_new_instance(tmp_path: Path, s3_client) -> None:
    store_a = S3WorkspaceStore(tmp_path / "pod-a", _BUCKET, _PREFIX, s3_client)
    workspace_a = store_a.prepare("user-1", "sess-1")
    workspace_a.dashboard_path.write_text("<html>v1</html>", encoding="utf-8")
    (workspace_a.results_dir / "q1.json").write_text('{"rows": []}', encoding="utf-8")
    store_a.persist(workspace_a)

    # 全新 store 實例、全新本地 root(模擬另一個 pod 接手同一個 session)。
    store_b = S3WorkspaceStore(tmp_path / "pod-b", _BUCKET, _PREFIX, s3_client)
    workspace_b = store_b.prepare("user-1", "sess-1")

    assert workspace_b.dashboard_path.read_text(encoding="utf-8") == "<html>v1</html>"
    assert (workspace_b.results_dir / "q1.json").read_text(encoding="utf-8") == '{"rows": []}'


def test_persist_excludes_skills_staging_dir(tmp_path: Path, s3_client) -> None:
    store = S3WorkspaceStore(tmp_path, _BUCKET, _PREFIX, s3_client)
    workspace = store.prepare("user-1", "sess-1")
    (workspace.skills_dir / "builtin").mkdir(parents=True)
    (workspace.skills_dir / "builtin" / "SKILL.md").write_text("---\n", encoding="utf-8")
    workspace.dashboard_path.write_text("<html></html>", encoding="utf-8")

    store.persist(workspace)

    listing = s3_client.list_objects_v2(Bucket=_BUCKET, Prefix=_PREFIX)
    keys = [entry["Key"] for entry in listing.get("Contents", [])]
    assert keys, "expected at least dashboard.html to be pushed"
    assert not any(".skills" in key for key in keys)
    assert any(key.endswith("dashboard.html") for key in keys)


def test_pull_overwrites_stale_local_file(tmp_path: Path, s3_client) -> None:
    store_a = S3WorkspaceStore(tmp_path / "pod-a", _BUCKET, _PREFIX, s3_client)
    workspace_a = store_a.prepare("user-1", "sess-1")
    workspace_a.dashboard_path.write_text("<html>remote-version</html>", encoding="utf-8")
    store_a.persist(workspace_a)

    store_b = S3WorkspaceStore(tmp_path / "pod-b", _BUCKET, _PREFIX, s3_client)
    workspace_b = store_b.prepare("user-1", "sess-1")
    workspace_b.dashboard_path.write_text("stale local content", encoding="utf-8")

    # 同一個 store、同一個本地 root 再 prepare 一次:bucket 內容為準,覆蓋掉剛剛寫入的
    # stale 本地內容。
    workspace_b_again = store_b.prepare("user-1", "sess-1")

    assert (
        workspace_b_again.dashboard_path.read_text(encoding="utf-8")
        == "<html>remote-version</html>"
    )


def test_prepare_pulls_user_skills_dir(tmp_path: Path, s3_client) -> None:
    s3_client.put_object(
        Bucket=_BUCKET,
        Key=f"{_PREFIX}user-1/skills/demo/SKILL.md",
        Body=b"---\nname: demo\n---\n",
    )
    store = S3WorkspaceStore(tmp_path, _BUCKET, _PREFIX, s3_client)
    workspace = store.prepare("user-1", "sess-1")

    user_skills_dir = workspace.root.parents[1] / "skills"
    assert (user_skills_dir / "demo" / "SKILL.md").is_file()
    assert (user_skills_dir / "demo" / "SKILL.md").read_text(
        encoding="utf-8"
    ) == "---\nname: demo\n---\n"


def test_persist_failure_logs_warning_and_does_not_raise(
    tmp_path: Path, s3_client, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    store = S3WorkspaceStore(tmp_path, _BUCKET, _PREFIX, s3_client)
    workspace = store.prepare("user-1", "sess-1")
    workspace.dashboard_path.write_text("<html></html>", encoding="utf-8")

    def _raise_upload_error(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("boom: simulated S3 outage")

    monkeypatch.setattr(s3_client, "upload_file", _raise_upload_error)

    with caplog.at_level(logging.WARNING):
        store.persist(workspace)  # MUST NOT raise -- produced results already shipped to user

    assert any(record.levelname == "WARNING" for record in caplog.records)


def test_build_workspace_store_defaults_to_local(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AGENT_WORKSPACE_BACKEND", raising=False)
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path))

    store = build_workspace_store()

    assert isinstance(store, LocalWorkspaceStore)


def test_build_workspace_store_s3_backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_WORKSPACE_BACKEND", "s3")
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("AGENT_S3_ENDPOINT", "localhost:9000")
    monkeypatch.setenv("AGENT_S3_ACCESS_KEY_ID", "test-access-key")
    monkeypatch.setenv("AGENT_S3_SECRET_ACCESS_KEY", "test-secret-key")
    monkeypatch.setenv("AGENT_WORKSPACE_S3_BUCKET", _BUCKET)
    monkeypatch.setenv("AGENT_WORKSPACE_S3_PREFIX", _PREFIX)

    store = build_workspace_store()

    assert isinstance(store, S3WorkspaceStore)


def test_build_workspace_store_rejects_unknown_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_WORKSPACE_BACKEND", "nope")
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path))

    with pytest.raises(ValueError):
        build_workspace_store()
