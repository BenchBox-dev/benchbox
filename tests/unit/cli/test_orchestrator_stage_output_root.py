# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import shutil
import tempfile
from pathlib import Path
from unittest.mock import Mock

import pytest

from benchbox.cli.orchestrator import BenchmarkOrchestrator
from benchbox.core.schemas import BenchmarkConfig
from benchbox.utils.cloud_storage import CloudStagingPath

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


CREDENTIAL_SHAPED_DEFAULTS = [
    "@~/benchbox",
    "@~/data",
    "@~/staged",
    "@my_stage/benchbox",
    "@my_db.my_schema.my_stage/benchbox",
    "s3://my-bucket/benchbox-data",
    "azure://container/benchbox-data",
    "gcs://my-bucket/benchbox-data",
]


class TestStageOutputRootRouting:
    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        self.orchestrator = BenchmarkOrchestrator(base_dir=self.temp_dir)
        self.config = BenchmarkConfig(name="tpch", display_name="TPC-H", scale_factor=0.01)
        self.benchmark = Mock()
        self.benchmark.get_data_source_benchmark = Mock(return_value=None)

    def teardown_method(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_stage_path_routes_through_cloud_staging_path(self):
        self.orchestrator.custom_output_dir = "@~/benchbox"
        platform_cfg = {}

        output_root = self.orchestrator._resolve_custom_output_root(self.config, self.benchmark, platform_cfg)

        assert isinstance(output_root, CloudStagingPath)
        assert output_root.cloud_target == "@~/benchbox"
        assert Path(str(output_root)).is_absolute()

    def test_stage_path_populates_staging_root(self):
        self.orchestrator.custom_output_dir = "@~/benchbox"
        platform_cfg = {}

        self.orchestrator._resolve_custom_output_root(self.config, self.benchmark, platform_cfg)

        assert platform_cfg["staging_root"] == "@~/benchbox"

    def test_stage_path_staging_matches_s3_routing(self):
        platform_cfg_stage = {}
        self.orchestrator.custom_output_dir = "@~/benchbox"
        stage_root = self.orchestrator._resolve_custom_output_root(self.config, self.benchmark, platform_cfg_stage)

        platform_cfg_s3 = {}
        self.orchestrator.custom_output_dir = "s3://bucket/benchbox"
        s3_root = self.orchestrator._resolve_custom_output_root(self.config, self.benchmark, platform_cfg_s3)

        assert type(stage_root) is type(s3_root)
        assert Path(str(stage_root)) == Path(str(s3_root))
        assert platform_cfg_stage["staging_root"] == "@~/benchbox"
        assert platform_cfg_s3["staging_root"] == "s3://bucket/benchbox"

    @pytest.mark.parametrize("default_output", CREDENTIAL_SHAPED_DEFAULTS)
    def test_no_credential_default_resolves_to_a_relative_local_path(self, default_output):
        self.orchestrator.custom_output_dir = default_output
        platform_cfg = {}

        output_root = self.orchestrator._resolve_custom_output_root(self.config, self.benchmark, platform_cfg)

        assert not isinstance(output_root, str), f"{default_output!r} returned a raw local string"
        assert Path(str(output_root)).is_absolute(), f"{default_output!r} resolved to a relative path"

    @pytest.mark.parametrize("default_output", CREDENTIAL_SHAPED_DEFAULTS)
    def test_construction_output_dir_defers_for_remote_defaults(self, default_output):
        self.orchestrator.custom_output_dir = default_output

        resolved = self.orchestrator._resolve_construction_output_dir(self.config, Mock(DATA_SOURCE_BENCHMARK=None))

        assert resolved is None, f"{default_output!r} was resolved locally at construction time"

    def test_local_custom_output_dir_is_still_returned_verbatim(self):
        local_dir = str(Path(self.temp_dir) / "explicit-output")
        self.orchestrator.custom_output_dir = local_dir
        platform_cfg = {}

        output_root = self.orchestrator._resolve_custom_output_root(self.config, self.benchmark, platform_cfg)

        assert output_root == local_dir
        assert "staging_root" not in platform_cfg

    def test_local_custom_output_dir_resolves_at_construction_time(self):
        local_dir = str(Path(self.temp_dir) / "explicit-output")
        self.orchestrator.custom_output_dir = local_dir

        resolved = self.orchestrator._resolve_construction_output_dir(self.config, Mock(DATA_SOURCE_BENCHMARK=None))

        assert resolved == local_dir


class TestCloudStagingDataSourceCache:
    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        self.orchestrator = BenchmarkOrchestrator(base_dir=self.temp_dir)
        self.orchestrator.custom_output_dir = "@~/benchbox"

    def teardown_method(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _cache_name(self, name, benchmark):
        config = BenchmarkConfig(name=name, display_name=name, scale_factor=1.0)
        root = self.orchestrator._resolve_custom_output_root(config, benchmark, {})
        return Path(str(root)).name

    def test_benchmark_that_generates_its_own_output_keeps_its_cache(self):
        benchmark = Mock(spec=["get_data_source_benchmark", "GENERATES_OWN_OUTPUT"])
        benchmark.get_data_source_benchmark = Mock(return_value="tpcds")
        benchmark.GENERATES_OWN_OUTPUT = True

        assert self._cache_name("tpcds_obt", benchmark) == "tpcds_obt_sf1"

    def test_data_sharing_benchmark_still_reads_the_source_cache(self):
        benchmark = Mock(spec=["get_data_source_benchmark"])
        benchmark.get_data_source_benchmark = Mock(return_value="tpch")

        assert self._cache_name("read_primitives", benchmark) == "tpch_sf1"


