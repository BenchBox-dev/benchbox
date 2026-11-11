from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from benchbox.platforms.databricks import DatabricksAdapter
from benchbox.utils.datagen_manifest import MANIFEST_FILENAME

databricks_env = all(os.getenv(k) for k in ["DATABRICKS_HOST", "DATABRICKS_HTTP_PATH", "DATABRICKS_TOKEN"]) and bool(
    os.getenv("RUN_DATABRICKS_TESTS")
)


pytestmark = [
    pytest.mark.integration,
    pytest.mark.live_integration,
    pytest.mark.live_databricks,
    pytest.mark.skipif(
        not databricks_env, reason="Databricks env not configured; set RUN_DATABRICKS_TESTS=1 to enable"
    ),
]


def _mk_adapter(tmp_path: Path) -> DatabricksAdapter:
    return DatabricksAdapter(
        server_hostname=os.environ["DATABRICKS_HOST"].replace("https://", ""),
        http_path=os.environ["DATABRICKS_HTTP_PATH"],
        access_token=os.environ["DATABRICKS_TOKEN"],
        catalog="workspace",
        schema="benchbox_it",
        staging_root=f"dbfs:/Volumes/workspace/benchbox_it/{tmp_path.name}",
    )


def _mk_manifest(tmp_path: Path) -> Path:
    data_dir = tmp_path
    (data_dir / "customer.tbl").write_bytes(b"x" * 10)
    (data_dir / "orders.tbl.1.zst").write_bytes(b"x" * 5)
    (data_dir / "orders.tbl.2.zst").write_bytes(b"x" * 5)
    manifest = {
        "benchmark": "tpch",
        "scale_factor": 1.0,
        "compression": {"enabled": False, "type": None, "level": None},
        "tables": {
            "customer": [
                {"path": "customer.tbl", "size_bytes": 10, "row_count": 150000},
            ],
            "orders": [
                {"path": "orders.tbl.1.zst", "size_bytes": 5, "row_count": 100},
                {"path": "orders.tbl.2.zst", "size_bytes": 5, "row_count": 100},
            ],
        },
    }
    mpath = data_dir / MANIFEST_FILENAME
    mpath.write_text(json.dumps(manifest), encoding="utf-8")
    return mpath


def test_manifest_upload_and_prevalidation(tmp_path: Path):
    adapter = _mk_adapter(tmp_path)

    _mk_manifest(tmp_path)
    data_files = {"customer": tmp_path / "customer.tbl", "orders": tmp_path / "orders.tbl.1.zst"}

    uc_root = adapter.staging_root
    result = adapter._upload_to_uc_volume(data_files, uc_root, tmp_path, force_upload=True)
    assert isinstance(result, dict) and result

    result2 = adapter._upload_to_uc_volume(data_files, uc_root, tmp_path, force_upload=False)
    assert isinstance(result2, dict) and result2
    for uri in result2.values():
        assert isinstance(uri, str) and uri.startswith("dbfs:/Volumes/")


def test_force_upload_bypasses_validation(tmp_path: Path):

    adapter = _mk_adapter(tmp_path)

    _mk_manifest(tmp_path)
    data_files = {"customer": tmp_path / "customer.tbl", "orders": tmp_path / "orders.tbl.1.zst"}

    uc_root = adapter.staging_root

    result1 = adapter._upload_to_uc_volume(data_files, uc_root, tmp_path, force_upload=True)
    assert isinstance(result1, dict) and result1

    (tmp_path / "customer.tbl").write_bytes(b"y" * 10)
    _mk_manifest(tmp_path)

    result2 = adapter._upload_to_uc_volume(data_files, uc_root, tmp_path, force_upload=True)
    assert isinstance(result2, dict) and result2
    for uri in result2.values():
        assert isinstance(uri, str) and uri.startswith("dbfs:/Volumes/")


def test_validation_failure_triggers_reupload(tmp_path: Path):

    adapter = _mk_adapter(tmp_path)

    _mk_manifest(tmp_path)
    data_files = {"customer": tmp_path / "customer.tbl", "orders": tmp_path / "orders.tbl.1.zst"}

    uc_root = adapter.staging_root

    result1 = adapter._upload_to_uc_volume(data_files, uc_root, tmp_path, force_upload=True)
    assert isinstance(result1, dict) and result1

    workspace = adapter._get_workspace_client()
    remote_manifest_path = f"{uc_root.replace('dbfs:', '/Volumes')}/{MANIFEST_FILENAME}"

    from io import BytesIO

    workspace.files.upload(remote_manifest_path, BytesIO(b"corrupted json {{{"), overwrite=True)

    result2 = adapter._upload_to_uc_volume(data_files, uc_root, tmp_path, force_upload=False)
    assert isinstance(result2, dict) and result2
    for uri in result2.values():
        assert isinstance(uri, str) and uri.startswith("dbfs:/Volumes/")


def test_missing_manifest_graceful_handling(tmp_path: Path):

    adapter = _mk_adapter(tmp_path)

    data_dir = tmp_path
    (data_dir / "customer.tbl").write_bytes(b"x" * 10)
    data_files = {"customer": tmp_path / "customer.tbl"}

    uc_root = adapter.staging_root

    result = adapter._upload_to_uc_volume(data_files, uc_root, tmp_path, force_upload=False)
    assert isinstance(result, dict) and result
    for uri in result.values():
        assert isinstance(uri, str) and uri.startswith("dbfs:/Volumes/")


def test_manifest_download_and_verify(tmp_path: Path):

    adapter = _mk_adapter(tmp_path)

    manifest_path = _mk_manifest(tmp_path)
    data_files = {"customer": tmp_path / "customer.tbl", "orders": tmp_path / "orders.tbl.1.zst"}

    uc_root = adapter.staging_root

    result = adapter._upload_to_uc_volume(data_files, uc_root, tmp_path, force_upload=True)
    assert isinstance(result, dict) and result

    workspace = adapter._get_workspace_client()
    remote_manifest_path = f"{uc_root.replace('dbfs:', '/Volumes')}/{MANIFEST_FILENAME}"

    from io import BytesIO

    download_stream = BytesIO()
    workspace.files.download(remote_manifest_path).contents.read_into(download_stream)
    download_stream.seek(0)
    remote_manifest = json.loads(download_stream.read().decode("utf-8"))

    local_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert remote_manifest["benchmark"] == local_manifest["benchmark"]
    assert remote_manifest["scale_factor"] == local_manifest["scale_factor"]
    assert remote_manifest["tables"] == local_manifest["tables"]
    assert remote_manifest["compression"] == local_manifest["compression"]


