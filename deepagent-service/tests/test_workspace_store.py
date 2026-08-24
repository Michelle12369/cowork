"""WorkspaceStore:generation 快照模型的行為驗證。

用 dict 存物件的 fake S3 client(非 moto)——這個模型的核心是「generation key 命名、zip
單物件原子性、舊 per-file 代 `_complete` 相容、清理保留規則」,都是純粹的 key 命名與呼叫
順序邏輯,用一個記錄 upload/put/delete 呼叫順序的輕量 stub 比啟動假 S3 服務更直接、更容易
斷言順序與時序。
"""

import io
import re
import zipfile
from pathlib import Path
from typing import Any

import pytest

from app.engine.workspace import WorkspacePersistError
from app.engine.workspace_store import WorkspaceStore

_BUCKET = "erd-cowork-test"
_PREFIX = "workspace"
_ZIP_NAME_PATTERN = re.compile(r"^(gen-\d{13}-[0-9a-f]{8})\.zip$")
_DIR_NAME_PATTERN = re.compile(r"^(gen-\d{13}-[0-9a-f]{8})/")


class _FakePaginator:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self._objects = objects

    def paginate(self, Bucket: str, Prefix: str) -> list[dict[str, Any]]:
        contents = [{"Key": key} for key in sorted(self._objects) if key.startswith(Prefix)]
        return [{"Contents": contents}]


class FakeS3Client:
    """dict 存物件(key -> bytes),記錄 upload/put/delete 呼叫順序供斷言。"""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.calls: list[tuple[str, str]] = []  # (op, key)
        self.upload_file_side_effect: Exception | None = None

    def get_paginator(self, operation_name: str) -> _FakePaginator:
        assert operation_name == "list_objects_v2"
        return _FakePaginator(self.objects)

    def download_file(self, Bucket: str, Key: str, Filename: str) -> None:
        self.calls.append(("download", Key))
        Path(Filename).write_bytes(self.objects[Key])

    def upload_file(self, Filename: str, Bucket: str, Key: str) -> None:
        if self.upload_file_side_effect is not None:
            raise self.upload_file_side_effect
        self.calls.append(("upload", Key))
        self.objects[Key] = Path(Filename).read_bytes()

    def put_object(self, *, Bucket: str, Key: str, Body: bytes) -> None:
        self.calls.append(("put", Key))
        self.objects[Key] = Body

    def delete_objects(self, *, Bucket: str, Delete: dict[str, Any]) -> None:
        for entry in Delete["Objects"]:
            key = entry["Key"]
            self.calls.append(("delete", key))
            self.objects.pop(key, None)


def _put_generation(
    client: FakeS3Client,
    session_prefix: str,
    generation: str,
    files: dict[str, bytes],
    complete: bool = True,
) -> None:
    """建一個舊 per-file 代(目錄形 + `_complete` marker)。"""
    for relative_path, content in files.items():
        client.objects[f"{session_prefix}{generation}/{relative_path}"] = content
    if complete:
        client.objects[f"{session_prefix}{generation}/_complete"] = b""


def _put_zip_generation(
    client: FakeS3Client,
    session_prefix: str,
    generation: str,
    files: dict[str, bytes],
) -> None:
    """建一個新 zip 代(單一物件,PUT 落地即 complete)。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for relative_path, content in files.items():
            archive.writestr(relative_path, content)
    client.objects[f"{session_prefix}{generation}.zip"] = buffer.getvalue()


def _session_prefix(user_id: str = "user-1", session_id: str = "sess-1") -> str:
    return f"{_PREFIX}/{user_id}/sessions/{session_id}/"


def _generation_names_in(client: FakeS3Client, session_prefix: str) -> set[str]:
    """掃 client.objects,回傳仍存在的 generation 名(zip 代與舊 dir 代混合)。"""
    names: set[str] = set()
    for key in client.objects:
        if not key.startswith(session_prefix):
            continue
        relative_key = key[len(session_prefix) :]
        zip_match = _ZIP_NAME_PATTERN.match(relative_key)
        if zip_match:
            names.add(zip_match.group(1))
            continue
        dir_match = _DIR_NAME_PATTERN.match(relative_key)
        if dir_match:
            names.add(dir_match.group(1))
    return names


# 1. prepare 空 session -> 空 workspace 骨架、無 pull
def test_prepare_empty_session_returns_empty_skeleton_without_pull(tmp_path: Path) -> None:
    client = FakeS3Client()
    store = WorkspaceStore(tmp_path, _BUCKET, _PREFIX, client)

    workspace = store.prepare("user-1", "sess-1")

    assert workspace.root.is_dir()
    assert workspace.queries_dir.is_dir()
    assert workspace.results_dir.is_dir()
    assert list(workspace.results_dir.iterdir()) == []
    assert not any(operation == "download" for operation, _key in client.calls)


# 2. prepare 兩個完整舊代(per-file) -> 只 pull timestamp 較大者
def test_prepare_two_complete_generations_pulls_only_latest(tmp_path: Path) -> None:
    client = FakeS3Client()
    session_prefix = _session_prefix()
    _put_generation(
        client, session_prefix, "gen-1000000000000-aaaaaaaa", {"dashboard.html": b"old"}
    )
    _put_generation(
        client, session_prefix, "gen-1500000000000-bbbbbbbb", {"dashboard.html": b"new"}
    )
    store = WorkspaceStore(tmp_path, _BUCKET, _PREFIX, client)

    workspace = store.prepare("user-1", "sess-1")

    assert workspace.dashboard_path.read_bytes() == b"new"
    downloaded_keys = [key for operation, key in client.calls if operation == "download"]
    assert all("gen-1500000000000-bbbbbbbb" in key for key in downloaded_keys)


# 3. prepare 最新舊代無 _complete -> pull 次新的完整代
def test_prepare_latest_generation_incomplete_pulls_next_complete(tmp_path: Path) -> None:
    client = FakeS3Client()
    session_prefix = _session_prefix()
    _put_generation(
        client, session_prefix, "gen-1000000000000-aaaaaaaa", {"dashboard.html": b"complete-old"}
    )
    _put_generation(
        client,
        session_prefix,
        "gen-1500000000000-bbbbbbbb",
        {"dashboard.html": b"incomplete-new"},
        complete=False,
    )
    store = WorkspaceStore(tmp_path, _BUCKET, _PREFIX, client)

    workspace = store.prepare("user-1", "sess-1")

    assert workspace.dashboard_path.read_bytes() == b"complete-old"


# 4. prepare 的 scratch 路徑在 .turns/ 下且兩次 prepare 互不相同
def test_prepare_scratch_path_under_turns_dir_and_unique_per_call(tmp_path: Path) -> None:
    client = FakeS3Client()
    store = WorkspaceStore(tmp_path, _BUCKET, _PREFIX, client)

    workspace_first = store.prepare("user-1", "sess-1")
    workspace_second = store.prepare("user-1", "sess-1")

    assert ".turns" in workspace_first.root.parts
    assert ".turns" in workspace_second.root.parts
    assert workspace_first.root != workspace_second.root


# 5. persist 只上傳單一 zip 物件(單物件 PUT 天然原子,不再需要 _complete marker)
def test_push_uploads_single_zip_object(tmp_path: Path) -> None:
    client = FakeS3Client()
    store = WorkspaceStore(tmp_path, _BUCKET, _PREFIX, client)
    workspace = store.prepare("user-1", "sess-1")
    workspace.dashboard_path.write_text("<html></html>", encoding="utf-8")
    (workspace.results_dir / "q1.json").write_text("{}", encoding="utf-8")

    store.persist(workspace)

    write_operations = [
        (operation, key) for operation, key in client.calls if operation in ("upload", "put")
    ]
    assert len(write_operations) == 1
    operation, key = write_operations[0]
    assert operation == "upload"
    assert key.endswith(".zip")


# 6. persist 首次 push 失敗 -> 換新 generation 名重試
def test_persist_first_push_fails_retries_with_new_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeS3Client()
    store = WorkspaceStore(tmp_path, _BUCKET, _PREFIX, client)
    workspace = store.prepare("user-1", "sess-1")
    workspace.dashboard_path.write_text("<html></html>", encoding="utf-8")

    real_upload_file = client.upload_file
    attempted_keys: list[str] = []
    call_count = {"value": 0}

    def flaky_upload_file(Filename: str, Bucket: str, Key: str) -> None:
        call_count["value"] += 1
        if Key not in attempted_keys:
            attempted_keys.append(Key)
        if call_count["value"] == 1:
            raise RuntimeError("simulated transient upload failure")
        real_upload_file(Filename=Filename, Bucket=Bucket, Key=Key)

    monkeypatch.setattr(client, "upload_file", flaky_upload_file)

    store.persist(workspace)

    assert len(attempted_keys) == 2
    assert attempted_keys[0] != attempted_keys[1]


# 7. persist 三次全失敗 -> raise WorkspacePersistError
def test_persist_all_attempts_fail_raises_workspace_persist_error(tmp_path: Path) -> None:
    client = FakeS3Client()
    client.upload_file_side_effect = RuntimeError("simulated permanent outage")
    store = WorkspaceStore(tmp_path, _BUCKET, _PREFIX, client)
    workspace = store.prepare("user-1", "sess-1")
    workspace.dashboard_path.write_text("<html></html>", encoding="utf-8")

    with pytest.raises(WorkspacePersistError):
        store.persist(workspace)


# 8. persist 成功 -> 舊代被刪、只留最新 2 個完整代(舊 dir 代與新 zip 代混合清理)
def test_persist_cleans_old_generations_keeps_latest_two(tmp_path: Path) -> None:
    client = FakeS3Client()
    session_prefix = _session_prefix()
    _put_generation(client, session_prefix, "gen-1000000000000-aaaaaaaa", {"dashboard.html": b"1"})
    _put_generation(client, session_prefix, "gen-1500000000000-bbbbbbbb", {"dashboard.html": b"2"})
    store = WorkspaceStore(tmp_path, _BUCKET, _PREFIX, client)
    workspace = store.prepare("user-1", "sess-1")
    workspace.dashboard_path.write_text("<html>3</html>", encoding="utf-8")

    store.persist(workspace)

    remaining_generation_names = _generation_names_in(client, session_prefix)
    assert "gen-1000000000000-aaaaaaaa" not in remaining_generation_names
    assert "gen-1500000000000-bbbbbbbb" in remaining_generation_names
    assert len(remaining_generation_names) == 2


# 9. 未完成且 timestamp 新(< 1h)的舊代不被清(併發保護)
def test_cleanup_does_not_delete_recent_incomplete_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeS3Client()
    session_prefix = _session_prefix()
    fixed_now_ms = 10_000_000_000_000
    recent_incomplete_generation = f"gen-{fixed_now_ms - 1000:013d}-cccccccc"
    _put_generation(
        client,
        session_prefix,
        recent_incomplete_generation,
        {"dashboard.html": b"in-flight"},
        complete=False,
    )
    store = WorkspaceStore(tmp_path, _BUCKET, _PREFIX, client)
    workspace = store.prepare("user-1", "sess-1")
    workspace.dashboard_path.write_text("<html></html>", encoding="utf-8")

    monkeypatch.setattr("app.engine.workspace_store.time.time_ns", lambda: fixed_now_ms * 1_000_000)
    store.persist(workspace)

    remaining_keys = [key for key in client.objects if key.startswith(session_prefix)]
    assert any(recent_incomplete_generation in key for key in remaining_keys)


# 10. 未完成且 timestamp 舊(> 1h)的舊代殘骸被清
def test_cleanup_deletes_stale_incomplete_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeS3Client()
    session_prefix = _session_prefix()
    fixed_now_ms = 10_000_000_000_000
    stale_incomplete_generation = f"gen-{fixed_now_ms - (2 * 60 * 60 * 1000):013d}-dddddddd"
    _put_generation(
        client,
        session_prefix,
        stale_incomplete_generation,
        {"dashboard.html": b"abandoned"},
        complete=False,
    )
    store = WorkspaceStore(tmp_path, _BUCKET, _PREFIX, client)
    workspace = store.prepare("user-1", "sess-1")
    workspace.dashboard_path.write_text("<html></html>", encoding="utf-8")

    monkeypatch.setattr("app.engine.workspace_store.time.time_ns", lambda: fixed_now_ms * 1_000_000)
    store.persist(workspace)

    remaining_keys = [key for key in client.objects if key.startswith(session_prefix)]
    assert not any(stale_incomplete_generation in key for key in remaining_keys)


# 11. persist 排除 .skills/(zip 內容驗證)
def test_persist_excludes_skills_staging_dir(tmp_path: Path) -> None:
    client = FakeS3Client()
    store = WorkspaceStore(tmp_path, _BUCKET, _PREFIX, client)
    workspace = store.prepare("user-1", "sess-1")
    (workspace.skills_dir / "builtin").mkdir(parents=True)
    (workspace.skills_dir / "builtin" / "SKILL.md").write_text("---\n", encoding="utf-8")
    workspace.dashboard_path.write_text("<html></html>", encoding="utf-8")

    store.persist(workspace)

    uploaded_keys = [key for operation, key in client.calls if operation == "upload"]
    assert len(uploaded_keys) == 1
    zip_bytes = client.objects[uploaded_keys[0]]
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        member_names = archive.namelist()
    assert not any(".skills" in name for name in member_names)
    assert "dashboard.html" in member_names


# 12. persist 成功後 scratch 目錄被刪除
def test_persist_success_removes_scratch_dir(tmp_path: Path) -> None:
    client = FakeS3Client()
    store = WorkspaceStore(tmp_path, _BUCKET, _PREFIX, client)
    workspace = store.prepare("user-1", "sess-1")
    workspace.dashboard_path.write_text("<html></html>", encoding="utf-8")
    scratch_base = workspace.root.parents[2]
    assert scratch_base.is_dir()

    store.persist(workspace)

    assert not scratch_base.exists()


# 13. _pull(舊代) 遇到 escape 路徑的 key -> raise ValueError
def test_pull_escaping_key_raises_value_error(tmp_path: Path) -> None:
    client = FakeS3Client()
    session_prefix = _session_prefix()
    client.objects[f"{session_prefix}gen-1000000000000-aaaaaaaa/../../etc/passwd"] = b"evil"
    client.objects[f"{session_prefix}gen-1000000000000-aaaaaaaa/_complete"] = b""
    store = WorkspaceStore(tmp_path, _BUCKET, _PREFIX, client)

    with pytest.raises(ValueError, match="escapes local workspace dir"):
        store.prepare("user-1", "sess-1")


# 14. cleanup_scratch() 直接呼叫 -> scratch 目錄被刪(不 persist 的路徑,如 /repair)
def test_cleanup_scratch_removes_scratch_dir(tmp_path: Path) -> None:
    client = FakeS3Client()
    store = WorkspaceStore(tmp_path, _BUCKET, _PREFIX, client)
    workspace = store.prepare("user-1", "sess-1")
    workspace.dashboard_path.write_text("<html></html>", encoding="utf-8")
    scratch_base = workspace.root.parents[2]
    assert scratch_base.is_dir()

    store.cleanup_scratch()

    assert not scratch_base.exists()
    # persist 從未呼叫 -> 沒有任何物件被推上去,驗證這確實是「不 persist 也能清」的路徑。
    assert not client.objects


# 15. persist 成功後再呼叫 cleanup_scratch() -> 冪等,不拋例外
def test_cleanup_scratch_idempotent_after_persist(tmp_path: Path) -> None:
    client = FakeS3Client()
    store = WorkspaceStore(tmp_path, _BUCKET, _PREFIX, client)
    workspace = store.prepare("user-1", "sess-1")
    workspace.dashboard_path.write_text("<html></html>", encoding="utf-8")

    store.persist(workspace)
    store.cleanup_scratch()  # scratch 已經被 persist() 刪過一次 -> 不該拋例外


# 16. persist 三次全失敗 raise -> scratch 仍在,呼叫 cleanup_scratch() 後才消失
def test_persist_failure_retains_scratch_until_cleanup(tmp_path: Path) -> None:
    client = FakeS3Client()
    client.upload_file_side_effect = RuntimeError("simulated permanent outage")
    store = WorkspaceStore(tmp_path, _BUCKET, _PREFIX, client)
    workspace = store.prepare("user-1", "sess-1")
    workspace.dashboard_path.write_text("<html></html>", encoding="utf-8")
    scratch_base = workspace.root.parents[2]

    with pytest.raises(WorkspacePersistError):
        store.persist(workspace)
    assert scratch_base.is_dir()  # raise 發生在 rmtree 之前 -> scratch 仍在,尚未清

    store.cleanup_scratch()

    assert not scratch_base.exists()


# 17. prefix 帶 key-prefix 段(如 build_workspace_store 組出的 "erd-cowork/workspace")
#     -> persist 的 upload key 以該完整 prefix 開頭(store 內部正規化已處理任意前綴,
#     此測試只驗證透傳,不需改 store 內部)
def test_persist_key_prefix_writes_under_prefixed_generation(tmp_path: Path) -> None:
    client = FakeS3Client()
    prefixed = "erd-cowork/workspace"
    store = WorkspaceStore(tmp_path, _BUCKET, prefixed, client)
    workspace = store.prepare("user-1", "sess-1")
    workspace.dashboard_path.write_text("<html></html>", encoding="utf-8")

    store.persist(workspace)

    uploaded_keys = [key for operation, key in client.calls if operation in ("upload", "put")]
    assert uploaded_keys, "expected at least one write"
    assert all(
        key.startswith("erd-cowork/workspace/user-1/sessions/sess-1/gen-") for key in uploaded_keys
    )


# 18. user skills prefix 有物件 -> 拉到 workspace.root.parents[1]/skills
def test_prepare_pulls_user_skills_to_expected_path(tmp_path: Path) -> None:
    client = FakeS3Client()
    client.objects[f"{_PREFIX}/user-1/skills/demo/SKILL.md"] = b"---\nname: demo\n---\n"
    store = WorkspaceStore(tmp_path, _BUCKET, _PREFIX, client)

    workspace = store.prepare("user-1", "sess-1")

    user_skills_dir = workspace.root.parents[1] / "skills"
    assert (user_skills_dir / "demo" / "SKILL.md").is_file()
    assert (user_skills_dir / "demo" / "SKILL.md").read_text(
        encoding="utf-8"
    ) == "---\nname: demo\n---\n"


# 19. prepare 讀到 zip 代 -> 下載解壓成功
def test_prepare_pulls_latest_zip_generation(tmp_path: Path) -> None:
    client = FakeS3Client()
    session_prefix = _session_prefix()
    _put_zip_generation(
        client,
        session_prefix,
        "gen-1000000000000-aaaaaaaa",
        {"dashboard.html": b"<html>zip</html>", "results/q1.json": b"{}"},
    )
    store = WorkspaceStore(tmp_path, _BUCKET, _PREFIX, client)

    workspace = store.prepare("user-1", "sess-1")

    assert workspace.dashboard_path.read_bytes() == b"<html>zip</html>"
    assert (workspace.results_dir / "q1.json").is_file()


# 20. 混合代取最新:zip 比舊代新 -> 用 zip
def test_prepare_prefers_newer_zip_over_older_legacy_generation(tmp_path: Path) -> None:
    client = FakeS3Client()
    session_prefix = _session_prefix()
    _put_generation(
        client, session_prefix, "gen-1000000000000-aaaaaaaa", {"dashboard.html": b"legacy-old"}
    )
    _put_zip_generation(
        client, session_prefix, "gen-1500000000000-bbbbbbbb", {"dashboard.html": b"zip-new"}
    )
    store = WorkspaceStore(tmp_path, _BUCKET, _PREFIX, client)

    workspace = store.prepare("user-1", "sess-1")

    assert workspace.dashboard_path.read_bytes() == b"zip-new"


# 21. 混合代取最新:舊代比 zip 新 -> 用舊代
def test_prepare_prefers_newer_legacy_over_older_zip_generation(tmp_path: Path) -> None:
    client = FakeS3Client()
    session_prefix = _session_prefix()
    _put_zip_generation(
        client, session_prefix, "gen-1000000000000-aaaaaaaa", {"dashboard.html": b"zip-old"}
    )
    _put_generation(
        client, session_prefix, "gen-1500000000000-bbbbbbbb", {"dashboard.html": b"legacy-new"}
    )
    store = WorkspaceStore(tmp_path, _BUCKET, _PREFIX, client)

    workspace = store.prepare("user-1", "sess-1")

    assert workspace.dashboard_path.read_bytes() == b"legacy-new"


# 22. 半傳失敗不可見:zip upload 全失敗 -> 沒有半個 zip 物件出現,新 prepare 仍拿到上一代
def test_persist_failed_zip_upload_leaves_no_partial_object_visible(tmp_path: Path) -> None:
    client = FakeS3Client()
    session_prefix = _session_prefix()
    _put_generation(
        client, session_prefix, "gen-1000000000000-aaaaaaaa", {"dashboard.html": b"previous"}
    )
    client.upload_file_side_effect = RuntimeError("simulated outage")
    store = WorkspaceStore(tmp_path, _BUCKET, _PREFIX, client)
    workspace = store.prepare("user-1", "sess-1")
    workspace.dashboard_path.write_text("<html>new</html>", encoding="utf-8")

    with pytest.raises(WorkspacePersistError):
        store.persist(workspace)

    assert not any(key.endswith(".zip") for key in client.objects)

    fresh_store = WorkspaceStore(tmp_path, _BUCKET, _PREFIX, client)
    fresh_workspace = fresh_store.prepare("user-1", "sess-1")
    assert fresh_workspace.dashboard_path.read_bytes() == b"previous"


# 23. zip-slip:惡意 entry 名(../evil)在 prepare 解壓時被拒絕
def test_prepare_zip_entry_escaping_path_raises_value_error(tmp_path: Path) -> None:
    client = FakeS3Client()
    session_prefix = _session_prefix()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("../evil.txt", b"malicious")
    client.objects[f"{session_prefix}gen-1000000000000-aaaaaaaa.zip"] = buffer.getvalue()
    store = WorkspaceStore(tmp_path, _BUCKET, _PREFIX, client)

    with pytest.raises(ValueError, match="escapes local workspace dir"):
        store.prepare("user-1", "sess-1")


# 24. download_file:zip 代 -> 回傳該 entry bytes
def test_download_file_zip_generation_returns_entry_bytes(tmp_path: Path) -> None:
    client = FakeS3Client()
    session_prefix = _session_prefix()
    _put_zip_generation(
        client, session_prefix, "gen-1000000000000-aaaaaaaa", {"dashboard.html": b"zip-content"}
    )
    store = WorkspaceStore(tmp_path, _BUCKET, _PREFIX, client)
    store.prepare("user-1", "sess-1")

    assert store.download_file("dashboard.html") == b"zip-content"


# 25. download_file:舊 per-file 代 -> 直接下載該單物件
def test_download_file_legacy_generation_returns_object_bytes(tmp_path: Path) -> None:
    client = FakeS3Client()
    session_prefix = _session_prefix()
    _put_generation(
        client, session_prefix, "gen-1000000000000-aaaaaaaa", {"dashboard.html": b"legacy-content"}
    )
    store = WorkspaceStore(tmp_path, _BUCKET, _PREFIX, client)
    store.prepare("user-1", "sess-1")

    assert store.download_file("dashboard.html") == b"legacy-content"


# 26. download_file:缺檔(zip 內無此 entry) -> None
def test_download_file_missing_entry_in_zip_returns_none(tmp_path: Path) -> None:
    client = FakeS3Client()
    session_prefix = _session_prefix()
    _put_zip_generation(
        client, session_prefix, "gen-1000000000000-aaaaaaaa", {"dashboard.html": b"zip-content"}
    )
    store = WorkspaceStore(tmp_path, _BUCKET, _PREFIX, client)
    store.prepare("user-1", "sess-1")

    assert store.download_file("results/does-not-exist.json") is None


# 27. download_file:整個 session 沒有任何 complete 代 -> None
def test_download_file_no_generation_returns_none(tmp_path: Path) -> None:
    client = FakeS3Client()
    store = WorkspaceStore(tmp_path, _BUCKET, _PREFIX, client)
    store.prepare("user-1", "sess-1")

    assert store.download_file("dashboard.html") is None


# 28. persist 產出的 zip 代可被下一次 prepare 完整讀回(端到端 round trip)
def test_persist_then_prepare_round_trips_via_zip(tmp_path: Path) -> None:
    client = FakeS3Client()
    store = WorkspaceStore(tmp_path, _BUCKET, _PREFIX, client)
    workspace = store.prepare("user-1", "sess-1")
    workspace.dashboard_path.write_text("<html>zip-roundtrip</html>", encoding="utf-8")
    (workspace.results_dir / "q1.json").write_text("{}", encoding="utf-8")

    store.persist(workspace)

    fresh_store = WorkspaceStore(tmp_path, _BUCKET, _PREFIX, client)
    fresh_workspace = fresh_store.prepare("user-1", "sess-1")
    assert (
        fresh_workspace.dashboard_path.read_text(encoding="utf-8") == "<html>zip-roundtrip</html>"
    )
    assert (fresh_workspace.results_dir / "q1.json").is_file()
