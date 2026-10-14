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


def test_notices_are_recorded_once_per_run_and_reset_by_begin_run() -> None:
    from benchbox.core.deprecation_notices import run_notices

    warn_deprecated_alias("snowflake", "warehouse", "'compute_resource'")
    warn_deprecated_alias("snowflake", "warehouse", "'compute_resource'")

    recorded = run_notices()
    assert [(n.platform, n.option, n.replacement, n.removed_in) for n in recorded] == [
        ("snowflake", "warehouse", "'compute_resource'", "0.6.0")
    ]
    assert recorded[0].message == (
        "Platform option 'warehouse' for 'snowflake' is deprecated and will be removed in BenchBox 0.6.0; "
        "use 'compute_resource' instead."
    )
    begin_run()
    assert run_notices() == ()


def test_run_scope_isolates_notices_and_leaves_the_outer_run_untouched() -> None:
    from benchbox.core.deprecation_notices import run_notices, run_scope

    warn_deprecated_alias("snowflake", "warehouse", "'compute_resource'")
    with run_scope():
        assert run_notices() == ()
        warn_deprecated_alias("snowflake", "warehouse", "'compute_resource'")
        assert len(run_notices()) == 1
    assert len(run_notices()) == 1


def test_concurrent_runs_keep_separate_notices() -> None:
    import contextvars
    import threading

    from benchbox.core.deprecation_notices import run_notices

    seen: dict[str, tuple[str, ...]] = {}
    barrier = threading.Barrier(2)

    def run(name: str, alias: str) -> None:
        begin_run()
        barrier.wait()
        warn_deprecated_alias("clickhouse", alias, "'--platform clickhouse-local'", setting=alias)
        barrier.wait()
        seen[name] = tuple(notice.option for notice in run_notices())

    threads = [
        threading.Thread(target=contextvars.copy_context().run, args=(run, "a", "mode")),
        threading.Thread(target=contextvars.copy_context().run, args=(run, "b", "deployment_mode")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert seen == {"a": ("mode",), "b": ("deployment_mode",)}


def test_quiet_scope_records_without_logging(caplog: pytest.LogCaptureFixture) -> None:
    from benchbox.core.deprecation_notices import run_notices, run_scope

    with captured_deprecations(caplog) as seen:
        with run_scope(log=False):
            warn_deprecated_alias("snowflake", "warehouse", "'compute_resource'")
            assert len(run_notices()) == 1

    assert seen == []
