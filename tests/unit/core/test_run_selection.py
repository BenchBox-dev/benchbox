from __future__ import annotations

import pytest

from benchbox.core.deployment import PlatformSelector, parse_platform_selector
from benchbox.core.deprecation_notices import run_notices
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


def test_legacy_option_selects_the_engine_and_records_a_notice() -> None:
    selection = resolve_run_selection("polars-df", "default", {"streaming": True})

    assert selection.execution_engine == "default"
    assert selection.effective_execution_engine == "streaming"
    assert [notice.option for notice in run_notices()] == ["streaming"]


def test_false_legacy_option_selects_nothing_and_records_nothing() -> None:
    selection = resolve_run_selection("polars-df", "default", {"streaming": False})

    assert selection.effective_execution_engine == "default"
    assert run_notices() == ()


def test_explicit_engine_conflicting_with_a_legacy_option_is_rejected() -> None:
    with pytest.raises(ValueError, match="Conflicting execution engine sources"):
        resolve_run_selection("polars-df", "in-memory", {"streaming": True})


def test_deployment_alias_option_resolves_and_records_a_notice() -> None:
    selection = resolve_run_selection("clickhouse", "default", {"deployment_mode": "server"})

    assert selection.deployment == "server"
    assert selection.selector_deployment is None
    assert [notice.option for notice in run_notices()] == ["deployment_mode"]


def test_selector_conflicting_with_an_alias_option_is_rejected() -> None:
    with pytest.raises(ValueError, match="conflicts with platform option"):
        resolve_run_selection("velox:local", "default", {"deployment": "remote"})


def test_selection_reports_the_manifest_default_deployment() -> None:
    assert resolve_run_selection("duckdb", "default", {}).deployment == "local"
    assert resolve_run_selection("clickhouse", "default", {}).deployment == "local"
