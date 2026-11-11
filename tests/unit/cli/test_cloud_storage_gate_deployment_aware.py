# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import ast
import inspect
from pathlib import Path

import pytest

from benchbox.cli import orchestrator as orchestrator_module
from benchbox.cli.orchestrator import resolved_deployment_mode
from benchbox.core.platform_registry import PlatformRegistry
from benchbox.core.schemas import DatabaseConfig

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _all_platforms() -> list[str]:
    PlatformRegistry._platform_metadata = PlatformRegistry._platform_metadata or (
        PlatformRegistry._build_platform_metadata()
    )
    return sorted(PlatformRegistry._platform_metadata)


ALL_PLATFORMS = _all_platforms()

RUN_PATH_SOURCES = [
    Path(inspect.getfile(orchestrator_module)),
    Path(inspect.getfile(orchestrator_module)).parent / "commands" / "run.py",
]


class TestRunPathGateMatchesDeployment:
    @pytest.mark.parametrize("platform", ALL_PLATFORMS)
    def test_gate_matches_deployment_answer_for_every_platform(self, platform):
        expected = PlatformRegistry.requires_cloud_storage_for_deployment(platform)
        config = DatabaseConfig(type=platform, name=platform)
        actual = PlatformRegistry.requires_cloud_storage_for_deployment(platform, resolved_deployment_mode(config))
        assert actual == expected, platform

    def test_known_divergence_is_still_exactly_the_three_platforms(self):
        diverging = {
            name
            for name in ALL_PLATFORMS
            if PlatformRegistry.requires_cloud_storage(name)
            != PlatformRegistry.requires_cloud_storage_for_deployment(name)
        }
        assert diverging == {"firebolt", "motherduck", "starburst"}, sorted(diverging)

    @pytest.mark.parametrize("platform", ["firebolt", "motherduck", "starburst"])
    def test_locally_deploying_cloud_platforms_are_not_gated_as_cloud(self, platform):
        assert PlatformRegistry.requires_cloud_storage(platform) is True
        assert PlatformRegistry.requires_cloud_storage_for_deployment(platform) is False

    @pytest.mark.parametrize("platform", ["snowflake", "bigquery", "databricks", "redshift", "athena", "synapse"])
    def test_genuinely_remote_platforms_keep_pulling_their_default(self, platform):
        if platform not in ALL_PLATFORMS:
            pytest.skip(f"{platform} is not registered")
        assert PlatformRegistry.requires_cloud_storage_for_deployment(platform) is True


class TestResolvedDeploymentMode:
    def test_explicit_deployment_mode_is_threaded(self):
        config = DatabaseConfig(type="clickhouse", name="ch", options={"deployment_mode": "local"})
        assert resolved_deployment_mode(config) == "local"

    def test_missing_deployment_mode_means_platform_default(self):
        assert resolved_deployment_mode(DatabaseConfig(type="duckdb", name="d")) is None

    def test_empty_deployment_mode_means_platform_default(self):
        config = DatabaseConfig(type="duckdb", name="d", options={"deployment_mode": ""})
        assert resolved_deployment_mode(config) is None

    def test_missing_config_is_tolerated(self):
        assert resolved_deployment_mode(None) is None

    def test_threaded_mode_can_flip_the_gate(self):
        assert PlatformRegistry.requires_cloud_storage_for_deployment("firebolt") is False
        assert PlatformRegistry.requires_cloud_storage_for_deployment("firebolt", "cloud") is True


class TestNoCategoryGatesRemainOnTheRunPath:
    @pytest.mark.parametrize("source", RUN_PATH_SOURCES, ids=lambda p: p.name)
    def test_run_path_never_calls_requires_cloud_storage_directly(self, source):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        offenders = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr == "requires_cloud_storage"
        ]
        assert not offenders, f"{source.name} still gates on requires_cloud_storage at lines {offenders}"


