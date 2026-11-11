# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import tempfile
from pathlib import Path

import pytest

from benchbox.utils.cloud_storage import (
    CloudStagingPath,
    DatabricksPath,
    create_path_handler,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


REMOTE_TARGETS = [
    "s3://bucket/benchbox",
    "gs://bucket/benchbox",
    "az://container/benchbox",
    "abfss://container@account.dfs.core.windows.net/benchbox",
    "@~/benchbox",
]


class TestRewrapPreservesCloudTarget:
    @pytest.mark.parametrize("target", REMOTE_TARGETS)
    def test_rewrap_returns_the_same_object(self, target):
        staging = CloudStagingPath("/tmp/cache", target)
        assert create_path_handler(staging) is staging

    @pytest.mark.parametrize("target", REMOTE_TARGETS)
    def test_rewrap_keeps_cloud_target(self, target):
        staging = CloudStagingPath("/tmp/cache", target)
        assert create_path_handler(staging).cloud_target == target

    def test_rewrap_is_idempotent(self):
        staging = CloudStagingPath("/tmp/cache", "s3://bucket/p")
        assert create_path_handler(create_path_handler(staging)) is staging

    def test_rewrap_does_not_downgrade_to_a_plain_path(self):
        staging = CloudStagingPath("/tmp/cache", "s3://bucket/p")
        result = create_path_handler(staging)
        assert not isinstance(result, Path)
        assert isinstance(result, CloudStagingPath)

    def test_databricks_passthrough_is_unchanged(self):
        databricks = DatabricksPath("/tmp/cache", "dbfs:/Volumes/c/s/v")
        assert create_path_handler(databricks) is databricks


class TestStagingPathIsAFaithfulPathProxy:
    REQUIRED_ATTRS = ["exists", "glob", "is_dir", "joinpath", "mkdir", "parent", "resolve", "stat", "suffix"]

    @pytest.mark.parametrize("attr", REQUIRED_ATTRS)
    def test_required_attribute_is_present(self, attr):
        assert hasattr(CloudStagingPath("/tmp/cache", "s3://b/p"), attr), attr

    def test_joinpath_matches_the_local_path(self):
        staging = CloudStagingPath("/tmp/cache", "s3://b/p")
        assert staging.joinpath("_datagen_manifest.json") == Path("/tmp/cache/_datagen_manifest.json")

    def test_joinpath_accepts_multiple_components(self):
        staging = CloudStagingPath("/tmp/cache", "s3://b/p")
        assert staging.joinpath("a", "b") == Path("/tmp/cache/a/b")

    def test_joinpath_agrees_with_truediv(self):
        staging = CloudStagingPath("/tmp/cache", "s3://b/p")
        assert staging.joinpath("x") == staging / "x"

    def test_manifest_validation_is_not_silently_skipped(self):
        assert hasattr(CloudStagingPath("/tmp/cache", "s3://b/p"), "joinpath")

    def test_stat_reads_the_local_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging = CloudStagingPath(tmp, "s3://b/p")
            assert staging.stat().st_mode == Path(tmp).stat().st_mode

    def test_suffix_matches_the_local_path(self):
        assert CloudStagingPath("/tmp/a/b.parquet", "s3://b/p").suffix == ".parquet"

    def test_local_delegation_still_works_after_rewrap(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging = create_path_handler(CloudStagingPath(Path(tmp) / "out", "s3://b/p"))
            staging.mkdir()
            assert staging.exists()
            assert staging.is_dir()


