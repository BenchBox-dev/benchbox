from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from benchbox.cli.tuning_resolver import (
    TuningMode,
    TuningSource,
    display_tuning_resolution,
    get_tuning_template_paths,
    resolve_template_reference,
    resolve_tuning,
)
from benchbox.core.tuning import modes as tuning_modes
from benchbox.core.tuning.packaged_templates import packaged_template_path

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

REPO_ROOT = Path(__file__).resolve().parents[3]
CURATED_PLATFORMS = ["polars", "pandas", "cudf"]
UNMATCHED_BENCH = "no_such_bench"


@pytest.fixture
def config_manager():
    manager = MagicMock()
    manager.get.return_value = None
    return manager


@pytest.fixture
def repo_cwd(monkeypatch):
    monkeypatch.chdir(REPO_ROOT)
    monkeypatch.delenv("BENCHBOX_TUNING_PATH", raising=False)


@pytest.fixture
def empty_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BENCHBOX_TUNING_PATH", raising=False)
    return tmp_path


def _resolve(platform, bench_name, config_manager, mode=None):
    return resolve_tuning(
        tuning_arg="tuned",
        platform=platform,
        benchmark=bench_name,
        mode=mode,
        config_manager=config_manager,
        console=MagicMock(spec=Console),
    )


class TestDataFrameProfileSearchPath:
    def test_profile_candidate_follows_every_per_benchmark_tier(self, monkeypatch):
        monkeypatch.setenv("BENCHBOX_TUNING_PATH", "/custom/path")

        paths = [p.as_posix() for p in get_tuning_template_paths("polars", "tpch")]

        profile_index = paths.index("examples/tunings/dataframe/polars_optimized.yaml")
        assert profile_index == len(paths) - 1
        assert paths[0] == "/custom/path/polars/tpch_tuned.yaml"
        assert paths.index("examples/tunings/polars/tpch_tuned.yaml") < profile_index
        assert packaged_template_path("polars", "tpch").as_posix() in paths[:profile_index]

    @pytest.mark.parametrize("selector", ["polars-df", "POLARS", "polars-df:local"])
    def test_platform_selector_spellings_share_one_profile(self, selector):
        paths = [p.as_posix() for p in get_tuning_template_paths(selector, "tpch")]

        assert paths[-1] == "examples/tunings/dataframe/polars_optimized.yaml"

    @pytest.mark.parametrize("platform", ["duckdb", "snowflake", "clickhouse"])
    def test_profile_candidate_is_dataframe_only(self, platform):
        dataframe_paths = get_tuning_template_paths("pandas", "tpch")
        sql_paths = get_tuning_template_paths(platform, "tpch")

        assert any("dataframe" in p.parts for p in dataframe_paths)
        assert not any("dataframe" in p.parts for p in sql_paths)
        assert sql_paths[-1] == packaged_template_path(platform, "tpch")


class TestCuratedProfileResolution:
    @pytest.mark.parametrize("platform", CURATED_PLATFORMS)
    def test_tuned_resolves_curated_profile(self, platform, repo_cwd, config_manager):
        resolution = _resolve(platform, "tpch", config_manager)

        assert resolution.mode is TuningMode.TUNED
        assert resolution.source is TuningSource.AUTO_DISCOVERED
        assert resolution.config_file == (REPO_ROOT / f"examples/tunings/dataframe/{platform}_optimized.yaml")
        assert resolution.canonical_mode == tuning_modes.TUNED
        assert resolve_template_reference(resolution.config_file) == (
            f"examples/tunings/dataframe/{platform}_optimized.yaml"
        )

    def test_dataframe_suffix_resolves_same_profile(self, repo_cwd, config_manager):
        resolution = _resolve("polars-df", "tpch", config_manager)

        assert resolution.config_file == REPO_ROOT / "examples/tunings/dataframe/polars_optimized.yaml"
        assert resolution.canonical_mode == tuning_modes.TUNED

    def test_curated_resolution_reports_template_not_fallback(self, repo_cwd, config_manager):
        resolution = _resolve("polars", "tpch", config_manager)
        console = MagicMock(spec=Console)

        display_tuning_resolution(resolution, console)

        printed = " ".join(str(call.args[0]) for call in console.print.call_args_list)
        assert "auto-discovered template" in printed
        assert "no optimized template available" not in printed
        assert not resolution.warnings

    def test_per_benchmark_template_wins_over_profile(self, empty_cwd, config_manager):
        profile_dir = empty_cwd / "examples" / "tunings" / "dataframe"
        profile_dir.mkdir(parents=True)
        (profile_dir / "polars_optimized.yaml").write_text("execution:\n  streaming_mode: false\n")
        per_benchmark = empty_cwd / "examples" / "tunings" / "polars"
        per_benchmark.mkdir(parents=True)
        (per_benchmark / "tpch_tuned.yaml").write_text("execution:\n  streaming_mode: true\n")

        resolution = _resolve("polars", "tpch", config_manager)

        assert resolution.config_file == (per_benchmark / "tpch_tuned.yaml").resolve()
        assert resolution.canonical_mode == tuning_modes.TUNED
        assert resolution.searched_paths.index(Path("examples/tunings/polars/tpch_tuned.yaml")) < (
            resolution.searched_paths.index(Path("examples/tunings/dataframe/polars_optimized.yaml"))
        )

    def test_profile_is_used_when_no_per_benchmark_template_exists(self, empty_cwd, config_manager):
        profile_dir = empty_cwd / "examples" / "tunings" / "dataframe"
        profile_dir.mkdir(parents=True)
        (profile_dir / "polars_optimized.yaml").write_text("execution:\n  streaming_mode: false\n")

        resolution = _resolve("polars", UNMATCHED_BENCH, config_manager)

        assert resolution.config_file == (profile_dir / "polars_optimized.yaml").resolve()
        assert resolution.source is TuningSource.AUTO_DISCOVERED


class TestPlatformsWithoutCuratedProfile:
    @pytest.mark.parametrize(
        ("platform", "profile_name"),
        [
            ("dask", "dask_optimized.yaml"),
            ("dask-df", "dask_optimized.yaml"),
            ("datafusion", "datafusion_optimized.yaml"),
        ],
    )
    def test_missing_profile_continues_to_fallback(self, platform, profile_name, repo_cwd, config_manager):
        assert (REPO_ROOT / "examples/tunings/dataframe/dask_distributed.yaml").exists()

        resolution = _resolve(platform, "tpch", config_manager)

        assert resolution.searched_paths[-1] == Path("examples/tunings/dataframe") / profile_name
        assert not resolution.searched_paths[-1].exists()
        assert resolution.source is TuningSource.FALLBACK
        assert resolution.config_file is None
        assert resolution.canonical_mode == tuning_modes.TUNED_FALLBACK


class TestFallbackAndCuratedFacetsStayDistinct:
    def test_fallback_run_does_not_match_curated_run(self, repo_cwd, config_manager):
        curated = _resolve("polars", "tpch", config_manager)
        fallback = _resolve("dask", "tpch", config_manager)

        assert fallback.canonical_mode != curated.canonical_mode
        assert fallback.canonical_mode == tuning_modes.TUNED_FALLBACK

    def test_curated_run_does_not_match_fallback_run(self, repo_cwd, config_manager):
        fallback = _resolve("dask", "tpch", config_manager)
        curated = _resolve("polars", "tpch", config_manager)

        assert curated.canonical_mode != fallback.canonical_mode
        assert curated.canonical_mode == tuning_modes.TUNED

    def test_same_platform_fallback_and_curated_differ(self, monkeypatch, tmp_path, config_manager):
        monkeypatch.delenv("BENCHBOX_TUNING_PATH", raising=False)
        monkeypatch.chdir(tmp_path)
        fallback = _resolve("polars", "tpch", config_manager)
        monkeypatch.chdir(REPO_ROOT)
        curated = _resolve("polars", "tpch", config_manager)

        assert fallback.source is TuningSource.FALLBACK
        assert curated.source is TuningSource.AUTO_DISCOVERED
        assert fallback.canonical_mode == tuning_modes.TUNED_FALLBACK
        assert curated.canonical_mode == tuning_modes.TUNED
        assert fallback.canonical_mode != curated.canonical_mode


class TestFallbackTextComesFromRegistry:
    def test_fallback_text_differs_per_platform(self, empty_cwd, config_manager):
        messages = {
            platform: _resolve(platform, UNMATCHED_BENCH, config_manager).info_messages[-1]
            for platform in ("duckdb", "polars", "clickhouse")
        }

        assert messages == {
            "duckdb": "Tuning: using basic constraints (no optimized template available)",
            "polars": "Tuning: using engine runtime defaults (streaming) (no optimized template available)",
            "clickhouse": "Tuning: using OLAP session pack (no optimized template available)",
        }

    @pytest.mark.parametrize(
        ("platform", "expected"),
        [
            ("polars", "engine runtime defaults (streaming)"),
            ("polars-df", "engine runtime defaults (streaming)"),
            ("clickhouse", "OLAP session pack"),
            ("clickhouse-local", "OLAP session pack"),
            ("dask", "engine runtime defaults"),
        ],
    )
    def test_fallback_message_names_what_the_platform_applies(self, platform, expected, empty_cwd, config_manager):
        resolution = _resolve(platform, UNMATCHED_BENCH, config_manager)

        assert resolution.source is TuningSource.FALLBACK
        assert resolution.info_messages[-1] == f"Tuning: using {expected} (no optimized template available)"
        assert resolution.source_description == f"Fallback to {expected} (no template found)"

    @pytest.mark.parametrize("platform", ["polars", "clickhouse"])
    def test_non_constraint_platforms_never_say_basic_constraints(self, platform, empty_cwd, config_manager):
        resolution = _resolve(platform, UNMATCHED_BENCH, config_manager)

        assert "basic constraints" not in " ".join(resolution.info_messages)
        assert "basic constraints" not in resolution.source_description

    @pytest.mark.parametrize(
        ("platform", "expected"),
        [("polars", "engine runtime defaults (streaming)"), ("clickhouse", "OLAP session pack")],
    )
    def test_fallback_without_benchmark_still_uses_platform_text(self, platform, expected, config_manager):
        resolution = _resolve(platform, None, config_manager)

        assert resolution.info_messages[-1] == f"Tuning: using {expected}"


class TestClickHouseTemplateDirectories:
    @pytest.mark.parametrize("platform", ["clickhouse", "clickhouse-local", "clickhouse-server", "clickhouse-cloud"])
    def test_every_variant_searches_the_shared_clickhouse_directory(self, platform):
        from benchbox.cli.tuning_resolver import get_tuning_template_paths

        paths = [path.as_posix() for path in get_tuning_template_paths(platform, "tpch")]

        assert "examples/tunings/clickhouse/tpch_tuned.yaml" in paths

    def test_a_variant_specific_directory_is_searched_first(self, monkeypatch, tmp_path):
        from benchbox.cli.tuning_resolver import get_tuning_template_paths

        monkeypatch.setenv("BENCHBOX_TUNING_PATH", str(tmp_path))

        paths = get_tuning_template_paths("clickhouse-cloud", "tpch")

        assert paths.index(tmp_path / "clickhouse-cloud" / "tpch_tuned.yaml") < paths.index(
            tmp_path / "clickhouse" / "tpch_tuned.yaml"
        )
        assert paths.index(tmp_path / "clickhouse" / "tpch_tuned.yaml") < paths.index(
            Path("examples/tunings/clickhouse-cloud/tpch_tuned.yaml")
        )

    def test_a_variant_specific_override_wins_over_the_curated_template(self, config_manager, monkeypatch, tmp_path):
        from benchbox.cli.tuning_resolver import TuningSource

        override = tmp_path / "clickhouse-cloud" / "tpch_tuned.yaml"
        override.parent.mkdir()
        override.write_text("primary_keys:\n  enabled: false\n")
        monkeypatch.chdir(REPO_ROOT)
        monkeypatch.setenv("BENCHBOX_TUNING_PATH", str(tmp_path))

        resolution = _resolve("clickhouse-cloud", "tpch", config_manager)

        assert resolution.config_file == override.resolve()
        assert resolution.source == TuningSource.AUTO_DISCOVERED

    @pytest.mark.parametrize("platform", ["clickhouse", "clickhouse-local", "clickhouse-cloud"])
    def test_the_packaged_template_is_recorded_as_packaged_for_every_variant(self, platform, config_manager, empty_cwd):
        from benchbox.cli.tuning_resolver import TuningSource

        resolution = _resolve(platform, "tpch", config_manager)

        assert resolution.source == TuningSource.PACKAGED_RESOURCE
        assert resolution.config_file.name == "tpch_tuned.yaml"
        assert resolution.config_file.parent.name == "clickhouse"


def _write_datafusion_profile(root: Path) -> Path:
    profile_dir = root / "examples" / "tunings" / "dataframe"
    profile_dir.mkdir(parents=True)
    profile = profile_dir / "datafusion_optimized.yaml"
    profile.write_text("execution:\n  streaming_mode: false\n")
    return profile


class TestSqlModeSkipsDataFrameProfile:
    def test_sql_search_paths_exclude_the_profile(self, empty_cwd):
        _write_datafusion_profile(empty_cwd)

        sql_paths = [p.as_posix() for p in get_tuning_template_paths("datafusion", "tpch", mode="sql")]

        assert not any("dataframe" in part for path in sql_paths for part in Path(path).parts)

    @pytest.mark.parametrize("mode", ["dataframe", None])
    def test_dataframe_and_unknown_modes_keep_the_profile(self, empty_cwd, mode):
        _write_datafusion_profile(empty_cwd)

        paths = [p.as_posix() for p in get_tuning_template_paths("datafusion", "tpch", mode=mode)]

        assert paths[-1] == "examples/tunings/dataframe/datafusion_optimized.yaml"

    def test_sql_tuned_run_never_resolves_the_profile(self, empty_cwd, config_manager):
        from benchbox.cli.tuning_resolver import TuningSource

        _write_datafusion_profile(empty_cwd)

        resolution = _resolve("datafusion", "tpch", config_manager, mode="sql")

        assert resolution.searched_paths is not None
        assert not any("dataframe" in p.parts for p in resolution.searched_paths)
        if resolution.config_file is not None:
            assert "dataframe" not in resolution.config_file.parts
        assert resolution.source is TuningSource.FALLBACK

    def test_dataframe_tuned_run_still_resolves_the_profile(self, empty_cwd, config_manager):
        from benchbox.cli.tuning_resolver import TuningSource

        profile = _write_datafusion_profile(empty_cwd)

        resolution = _resolve("datafusion", "tpch", config_manager, mode="dataframe")

        assert resolution.config_file == profile.resolve()
        assert resolution.source is TuningSource.AUTO_DISCOVERED

    def test_repo_ships_no_datafusion_profile(self):
        assert not (REPO_ROOT / "examples/tunings/dataframe/datafusion_optimized.yaml").exists()


