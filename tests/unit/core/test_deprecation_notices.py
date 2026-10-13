from __future__ import annotations

import pytest

from benchbox.core.deprecation_notices import ALIAS_REMOVED_IN, alias_removal_release, begin_run, warn_deprecated_alias
from benchbox.core.execution_engine import DEPRECATED_OPTION_ALIASES, legacy_option_engine
from tests.utilities.deprecation_capture import captured_deprecations

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_alias_warns_once_per_run_and_again_after_reset(caplog: pytest.LogCaptureFixture) -> None:
    with captured_deprecations(caplog) as first:
        warn_deprecated_alias("snowflake", "warehouse", "'compute_resource'")
        warn_deprecated_alias("snowflake", "warehouse", "'compute_resource'")
    begin_run()
    with captured_deprecations(caplog) as second:
        warn_deprecated_alias("snowflake", "warehouse", "'compute_resource'")

    assert first == [
        "Platform option 'warehouse' for 'snowflake' is deprecated and will be removed in BenchBox 0.6.0; "
        "use 'compute_resource' instead."
    ]
    assert second == first


def test_setting_groups_spellings_of_one_deprecated_setting(caplog: pytest.LogCaptureFixture) -> None:
    with captured_deprecations(caplog) as seen:
        warn_deprecated_alias("clickhouse", "mode", "'--platform clickhouse-local'", setting="deployment")
        warn_deprecated_alias("clickhouse", "deployment_mode", "'--platform clickhouse-local'", setting="deployment")

    assert len(seen) == 1
    assert "'mode'" in seen[0]


def test_flag_spelling_is_labelled_as_an_option(caplog: pytest.LogCaptureFixture) -> None:
    with captured_deprecations(caplog) as seen:
        warn_deprecated_alias("polars", "--polars-streaming", "'--execution-engine streaming'")

    assert seen[0].startswith("Option '--polars-streaming' for 'polars' is deprecated")


def test_removal_release_comes_from_the_alias_table() -> None:
    assert alias_removal_release("clickhouse", "mode") == DEPRECATED_OPTION_ALIASES[("clickhouse", "mode")].removed_in
    assert alias_removal_release("redshift", "workgroup_name") == ALIAS_REMOVED_IN


@pytest.mark.parametrize(
    ("platform", "option", "enabled", "engine"),
    [
        ("polars-df", "streaming", True, "streaming"),
        ("polars", "streaming", False, None),
        ("dask", "use_distributed", True, "distributed"),
        ("dask", "use_distributed", False, "threaded"),
        ("pg-duckdb", "force_execution", True, "duckdb"),
        ("pg-duckdb", "force_execution", False, "postgres"),
        ("duckdb", "threads", True, None),
    ],
)
def test_legacy_option_engine_maps_and_warns(
    platform: str, option: str, enabled: bool, engine: str | None, caplog: pytest.LogCaptureFixture
) -> None:
    with captured_deprecations(caplog) as seen:
        assert legacy_option_engine(platform, option, enabled) == engine

    assert len(seen) == (0 if engine is None else 1)
