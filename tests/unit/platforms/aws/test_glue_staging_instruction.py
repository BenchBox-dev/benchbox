from __future__ import annotations

import re
import shutil
import tempfile
from unittest.mock import MagicMock, Mock, patch

import pytest

from benchbox.cli.orchestrator import BenchmarkOrchestrator
from benchbox.core.exceptions import ConfigurationError
from benchbox.core.schemas import BenchmarkConfig

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

JOB_ROLE = "arn:aws:iam::123456789012:role/GlueRole"


class TestGlueStagingInstruction:
    def test_missing_staging_names_the_run_output_option(self):
        from benchbox.platforms.aws import AWSGlueAdapter

        with pytest.raises(ConfigurationError) as excinfo:
            AWSGlueAdapter(job_role=JOB_ROLE)
        message = str(excinfo.value)

        assert "--staging-root" not in message
        assert "BENCHBOX_S3_STAGING_DIR" not in message
        assert re.search(r"--output s3://\S+", message)

    def test_environment_variable_does_not_supply_staging(self, monkeypatch):
        from benchbox.platforms.aws import AWSGlueAdapter

        monkeypatch.setenv("BENCHBOX_S3_STAGING_DIR", "s3://bucket/path")
        with pytest.raises(ConfigurationError):
            AWSGlueAdapter(job_role=JOB_ROLE)

    def test_output_path_supplies_staging_root_to_the_adapter(self):
        from benchbox.platforms.aws import AWSGlueAdapter

        temp_dir = tempfile.mkdtemp()
        try:
            orchestrator = BenchmarkOrchestrator(base_dir=temp_dir)
            orchestrator.custom_output_dir = "s3://my-bucket/benchbox-data"
            benchmark = Mock()
            benchmark.get_data_source_benchmark = Mock(return_value=None)
            config = BenchmarkConfig(name="tpch", display_name="TPC-H", scale_factor=0.01)
            platform_cfg = {"job_role": JOB_ROLE}

            orchestrator._resolve_custom_output_root(config, benchmark, platform_cfg)

            with patch("benchbox.platforms.aws.glue_adapter.CloudSparkStaging") as staging:
                staging.from_uri.return_value = MagicMock()
                adapter = AWSGlueAdapter(**platform_cfg)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

        assert adapter.s3_bucket == "my-bucket"
        assert adapter.s3_prefix == "benchbox-data"

    def test_external_instruction_matches_the_output_route(self):
        from benchbox.platforms.aws import AWSGlueAdapter

        assert AWSGlueAdapter.external_staging_instruction == "Pass --output s3://bucket/path."
