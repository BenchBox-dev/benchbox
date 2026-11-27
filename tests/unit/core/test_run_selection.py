from __future__ import annotations

import pytest

from benchbox.core.deployment import PlatformSelector, parse_platform_selector
from benchbox.core.run_selection import resolve_run_selection

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("duckdb", PlatformSelector("duckdb", None)),
        ("polars-df", PlatformSelector("polars-df", None)),
        ("ClickHouse:Server", PlatformSelector("clickhouse-server", None)),
        ("clickhouse:local", PlatformSelector("clickhouse-local", None)),
        ("velox:remote", PlatformSelector("velox", "remote")),
        ("timescaledb:cloud", PlatformSelector("timescaledb", "cloud")),
        ("databend:self-hosted", PlatformSelector("databend", "self-hosted")),
    ],
)
def test_selector_parses_declared_modes(raw: str, expected: PlatformSelector) -> None:
    assert parse_platform_selector(raw) == expected


@pytest.mark.parametrize("raw", ["duckdb:cluster", "snowflake:foo", "velox:docker", "clickhouse-server:cloud"])
def test_selector_rejects_undeclared_modes(raw: str) -> None:
    with pytest.raises(ValueError, match="deployment mode"):
        parse_platform_selector(raw)


@pytest.mark.parametrize(
    ("platform", "option", "replacement"),
    [
        ("polars-df", "streaming", "'--execution-engine streaming' (execution_engine='streaming')"),
        ("clickhouse", "deployment_mode", "the platform selector '--platform clickhouse:"),
        ("velox:local", "deployment", "the platform selector '--platform velox:local|remote'"),
        ("snowflake", "warehouse", "'compute_resource'"),
    ],
)
def test_removed_option_is_rejected_with_its_replacement(platform: str, option: str, replacement: str) -> None:
    with pytest.raises(ValueError, match="was removed in BenchBox 0.6.0") as raised:
        resolve_run_selection(platform, "default", {option: "x"})

    assert f"Platform option '{option}'" in str(raised.value)
    assert replacement in str(raised.value)


def test_selection_reports_the_manifest_default_deployment() -> None:
    assert resolve_run_selection("duckdb", "default", {}).deployment == "local"
    assert resolve_run_selection("clickhouse", "default", {}).deployment == "local"


def test_selection_reports_the_selected_engine() -> None:
    assert resolve_run_selection("polars-df", "streaming", {}).execution_engine == "streaming"
