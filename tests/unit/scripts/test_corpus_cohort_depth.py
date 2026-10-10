from __future__ import annotations

import datetime as dt
import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from _project.scripts.explorer_pipeline import transformer as explorer_transformer
from _project.scripts.explorer_pipeline.models import (
    _PHASE_ALIASES,
    APPLIED_TUNING_STATUSES,
    RANKING_METRIC_BY_FAMILY,
    RANKING_METRIC_BY_FAMILY_PHASE,
    THROUGHPUT_PHASE,
    UNOFFICIAL_COMPLIANCE_CLASSES,
    canonical_phase,
    ranking_exclusion_reason,
)
from _project.scripts.explorer_pipeline.transformer import BundleTransformer
from benchbox.core.results.status import NON_CLEAN_TRANSLATION_STATUSES, NON_CLEAN_VALIDATION_STATUSES
from benchbox.core.tuning.modes import MODES

pytestmark = [pytest.mark.unit, pytest.mark.fast]

_EXPLORER_FIXTURES = pytest.importorskip(
    "tests.unit.scripts.explorer_pipeline.conftest", reason="Explorer pipeline test fixtures are not in this checkout"
)
MINIMAL_BUNDLE = _EXPLORER_FIXTURES.MINIMAL_BUNDLE
throughput_bundle = _EXPLORER_FIXTURES.throughput_bundle

REPO_ROOT = Path(__file__).resolve().parents[3]
VALIDATOR = REPO_ROOT / "results-data" / "validate_corpus.py"
BUNDLES = REPO_ROOT / "results-data" / "bundles"


def _load_validator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("validate_corpus", VALIDATOR)
    assert spec and spec.loader, f"cannot load {VALIDATOR}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rankable_body() -> dict:
    return {
        "summary": {
            "queries": {"total": 2, "passed": 2, "failed": 0},
            "validation": "passed",
            "tpc_metrics": {"power_at_size": 1.0},
        },
        "queries": [
            {"id": "Q1", "ms": 10.0, "run_type": "measurement", "status": "SUCCESS"},
            {"id": "Q2", "ms": 12.0, "run_type": "measurement", "status": "SUCCESS"},
        ],
    }


def _write_bundle(
    directory: Path,
    name: str,
    *,
    benchmark: str,
    scale: float,
    platform: str,
    platform_version: str | None = None,
    execution_version: str | None = None,
    run_timestamp: str = "2026-08-01T12:00:00",
    test_type: str | None = "power",
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    platform_payload = {"name": platform}
    if platform_version is not None:
        platform_payload["version"] = platform_version
    payload = {
        "benchmark": {"id": benchmark, "scale_factor": scale},
        "platform": platform_payload,
        "run": {"timestamp": run_timestamp},
        **_rankable_body(),
    }
    if test_type is not None:
        payload["benchmark"]["test_type"] = test_type
    if execution_version is not None:
        payload["execution"] = {"driver_version_resolved": execution_version}
    (directory / name).write_text(json.dumps(payload), encoding="utf-8")


def test_every_committed_cohort_meets_the_platform_floor() -> None:
    validator = _load_validator()
    cohorts = validator.cohort_platforms(validator.discover_bundles(BUNDLES))

    assert cohorts, "no cohorts found - this gate would be vacuous"
    shallow = validator.shallow_cohorts(cohorts)
    assert not shallow, (
        f"{len(shallow)} cohort(s) below {validator.MINIMUM_PLATFORMS_PER_COHORT} identities; a one-identity "
        "cohort is not a comparison. See results-data/SEED_CORPUS_SPEC.md:\n  "
        + "\n  ".join(
            f"{benchmark} SF={scale}: {sorted(platforms)}" for (benchmark, scale), platforms in shallow.items()
        )
    )


def test_the_gate_detects_a_one_platform_cohort(tmp_path: Path) -> None:
    validator = _load_validator()
    _write_bundle(tmp_path, "a.json", benchmark="tpcds", scale=10.0, platform="DuckDB")

    shallow = validator.shallow_cohorts(validator.cohort_platforms(validator.discover_bundles(tmp_path)))

    assert shallow == {("tpcds", "10.0"): {"DuckDB"}}


def _write_phase_bundle(
    directory: Path,
    name: str,
    *,
    platform: str,
    power: str,
    throughput: str,
    streams: int = 3,
    test_type: str | None = None,
    benchmark_id: str = "tpch",
    power_score: float | None = 1.0,
    throughput_score: float | None = 1.0,
) -> None:
    benchmark: dict = {"id": benchmark_id, "scale_factor": 1.0}
    if test_type is not None:
        benchmark["test_type"] = test_type
    throughput_phase: dict = {"status": throughput}
    if throughput != "NOT_RUN":
        throughput_phase["stream_results"] = [{"stream_id": index, "success": True} for index in range(streams)]
    tpc_metrics = {}
    if power_score is not None:
        tpc_metrics["power_at_size"] = power_score
    if throughput_score is not None and throughput != "NOT_RUN":
        tpc_metrics["throughput_at_size"] = throughput_score
    payload = {
        "benchmark": benchmark,
        "platform": {"name": platform},
        "run": {"timestamp": "2026-08-01T12:00:00", "streams": streams},
        "phases": {"power_test": {"status": power}, "throughput_test": throughput_phase},
        "summary": {
            "queries": {"total": 2, "passed": 2, "failed": 0},
            "validation": "passed",
            "tpc_metrics": tpc_metrics,
        },
        "queries": [
            {"id": "Q1", "ms": 10.0, "run_type": "measurement", "status": "SUCCESS"},
            {"id": "Q2", "ms": 12.0, "run_type": "measurement", "status": "SUCCESS"},
        ],
    }
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(json.dumps(payload), encoding="utf-8")


def _throughput_cohort(validator: ModuleType, directory: Path) -> dict:
    return {
        key: platforms
        for key, platforms in validator.cohort_platforms(validator.discover_bundles(directory)).items()
        if "#throughput" in key[1]
    }


@pytest.mark.parametrize("test_type", [None, "throughput", "Throughput"])
def test_throughput_bundles_form_their_own_cohort(tmp_path: Path, test_type: str | None) -> None:
    validator = _load_validator()
    for platform in ("DuckDB", "DataFusion", "Spark"):
        _write_phase_bundle(
            tmp_path, f"{platform}-power.json", platform=platform, power="COMPLETED", throughput="NOT_RUN"
        )
    _write_phase_bundle(
        tmp_path, "duckdb-tp.json", platform="DuckDB", power="NOT_RUN", throughput="COMPLETED", test_type=test_type
    )

    shallow = validator.shallow_cohorts(validator.cohort_platforms(validator.discover_bundles(tmp_path)))

    assert shallow == {("tpch", "1.0#throughput#3streams"): {"DuckDB"}}
    assert validator.main(tmp_path) == 1


def test_a_one_engine_throughput_cohort_fails_without_any_power_bundles(tmp_path: Path) -> None:
    validator = _load_validator()
    _write_phase_bundle(
        tmp_path, "duckdb.json", platform="DuckDB", power="NOT_RUN", throughput="COMPLETED", test_type="throughput"
    )

    assert validator.shallow_cohorts(validator.cohort_platforms(validator.discover_bundles(tmp_path))) == {
        ("tpch", "1.0#throughput#3streams"): {"DuckDB"}
    }
    assert validator.main(tmp_path) == 1


def test_three_throughput_engines_pass_the_gate(tmp_path: Path) -> None:
    validator = _load_validator()
    for platform in ("DuckDB", "Spark", "Doris"):
        _write_phase_bundle(
            tmp_path,
            f"{platform}.json",
            platform=platform,
            power="NOT_RUN",
            throughput="COMPLETED",
            test_type="throughput",
            power_score=None,
        )

    assert validator.shallow_cohorts(validator.cohort_platforms(validator.discover_bundles(tmp_path))) == {}
    assert validator.main(tmp_path) == 0


def test_throughput_stream_counts_do_not_pad_each_other(tmp_path: Path) -> None:
    validator = _load_validator()
    for platform, streams in (("DuckDB", 2), ("Spark", 3), ("Doris", 3)):
        _write_phase_bundle(
            tmp_path,
            f"{platform}.json",
            platform=platform,
            power="NOT_RUN",
            throughput="COMPLETED",
            streams=streams,
            test_type="throughput",
        )

    assert _throughput_cohort(validator, tmp_path) == {
        ("tpch", "1.0#throughput#2streams"): {"DuckDB"},
        ("tpch", "1.0#throughput#3streams"): {"Spark", "Doris"},
    }


@pytest.mark.parametrize("count_source", ["run", "config"])
def test_corpus_validator_rejects_configured_stream_count_mismatch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], count_source: str
) -> None:
    validator = _load_validator()
    _write_phase_bundle(
        tmp_path,
        "bad-stream-count.json",
        platform="DuckDB",
        power="NOT_RUN",
        throughput="COMPLETED",
        test_type="throughput",
    )
    path = tmp_path / "bad-stream-count.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if count_source == "run":
        payload["run"]["streams"] = 4
    else:
        payload["run"].pop("streams")
        payload["config"] = {"streams": 4}
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert validator.main(tmp_path) == 1
    output = capsys.readouterr().out
    assert "bad-stream-count.json" in output
    assert "configured=4" in output
    assert "stream_results=3" in output


def test_corpus_validator_skips_stream_count_check_without_a_configured_count(tmp_path: Path) -> None:
    validator = _load_validator()
    _write_phase_bundle(
        tmp_path,
        "older.json",
        platform="DuckDB",
        power="NOT_RUN",
        throughput="COMPLETED",
        test_type="throughput",
    )
    path = tmp_path / "older.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["run"].pop("streams")
    payload["config"] = {}
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert validator._throughput_stream_count_mismatch(payload) is None
    validator.cohort_platforms([path])


def test_not_run_power_phase_does_not_make_a_throughput_bundle_power(tmp_path: Path) -> None:
    validator = _load_validator()
    _write_phase_bundle(tmp_path, "tp.json", platform="DuckDB", power="NOT_RUN", throughput="COMPLETED")

    assert set(validator.cohort_platforms(validator.discover_bundles(tmp_path))) == {
        ("tpch", "1.0#throughput#3streams")
    }


def test_phase_names_are_case_normalized_and_standard_is_power(tmp_path: Path) -> None:
    validator = _load_validator()
    _write_bundle(tmp_path, "upper.json", benchmark="tpch", scale=1.0, platform="DuckDB", test_type="POWER")
    _write_bundle(tmp_path, "standard.json", benchmark="tpch", scale=1.0, platform="Spark", test_type="Standard")
    _write_bundle(tmp_path, "combined.json", benchmark="tpch", scale=1.0, platform="Doris", test_type="combined")
    _write_bundle(tmp_path, "none.json", benchmark="tpch", scale=1.0, platform="Polars", test_type=None)

    assert validator.cohort_platforms(validator.discover_bundles(tmp_path)) == {
        ("tpch", "1.0"): {"DuckDB", "Spark"},
        ("tpch", "1.0#combined"): {"Doris"},
        ("tpch", "1.0#unknown"): {"Polars"},
    }


def _rankable_bundle(directory: Path, name: str, platform: str, **overrides: object) -> None:
    payload: dict = {
        "benchmark": {"id": "tpch", "scale_factor": 1.0, "test_type": "power", "compliance_class": "official"},
        "platform": {"name": platform},
        "run": {"timestamp": "2026-08-01T12:00:00"},
        **_rankable_body(),
    }
    payload.update(overrides)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(json.dumps(payload), encoding="utf-8")


UNRANKABLE_OVERRIDES = {
    "failed-queries": {"summary": {"queries": {"total": 2, "passed": 1, "failed": 1}, "validation": "passed"}},
    "partial-validation": {"summary": {"queries": {"total": 2, "passed": 2, "failed": 0}, "validation": "partial"}},
    "failed-validation": {"summary": {"queries": {"total": 2, "passed": 2, "failed": 0}, "validation": "FAILED"}},
    "unofficial": {
        "benchmark": {
            "id": "tpch",
            "scale_factor": 1.0,
            "test_type": "power",
            "compliance_class": "unofficial_subscale",
        }
    },
    "translation-fallback": {
        "summary": {"queries": {"total": 2, "passed": 2, "failed": 0}, "validation": "passed"},
        "execution": {"translation": "fallback"},
    },
}


@pytest.mark.parametrize("reason", sorted(UNRANKABLE_OVERRIDES))
def test_an_unrankable_bundle_does_not_count_toward_depth(tmp_path: Path, reason: str) -> None:
    validator = _load_validator()
    _rankable_bundle(tmp_path, "a.json", "DuckDB")
    _rankable_bundle(tmp_path, "b.json", "DataFusion")
    _rankable_bundle(tmp_path, "c.json", "Spark", **UNRANKABLE_OVERRIDES[reason])

    cohorts = validator.cohort_platforms(validator.discover_bundles(tmp_path))

    assert cohorts == {("tpch", "1.0"): {"DuckDB", "DataFusion"}}
    assert validator.shallow_cohorts(cohorts) == cohorts
    assert validator.main(tmp_path) == 1


def test_throughput_bundles_without_a_primary_metric_leave_the_cohort_unranked(tmp_path: Path) -> None:
    validator = _load_validator()
    for platform in ("DuckDB", "Spark", "Doris"):
        _write_phase_bundle(
            tmp_path,
            f"{platform}.json",
            platform=platform,
            power="NOT_RUN",
            throughput="COMPLETED",
            test_type="throughput",
            throughput_score=None,
        )

    cohorts = validator.cohort_platforms(validator.discover_bundles(tmp_path))

    assert cohorts == {("tpch", "1.0#throughput#3streams"): set()}
    assert validator.unranked_cohorts(cohorts) == [("tpch", "1.0#throughput#3streams")]


def test_throughput_bundles_on_a_geomean_benchmark_are_rankable(tmp_path: Path) -> None:
    validator = _load_validator()
    for platform in ("DuckDB", "Spark", "Doris"):
        _write_phase_bundle(
            tmp_path,
            f"{platform}.json",
            platform=platform,
            power="NOT_RUN",
            throughput="COMPLETED",
            test_type="throughput",
            benchmark_id="ssb",
            power_score=None,
            throughput_score=None,
        )

    assert validator.main(tmp_path) == 0
    assert validator.cohort_platforms(validator.discover_bundles(tmp_path)) == {
        ("ssb", "1.0#throughput#3streams"): {"DuckDB", "Spark", "Doris"}
    }


def test_a_failed_throughput_bundle_cannot_complete_a_throughput_cohort(tmp_path: Path) -> None:
    validator = _load_validator()
    for platform in ("DuckDB", "Spark"):
        _write_phase_bundle(
            tmp_path,
            f"{platform}.json",
            platform=platform,
            power="NOT_RUN",
            throughput="COMPLETED",
            test_type="throughput",
        )
    sqlite = json.loads((tmp_path / "Spark.json").read_text(encoding="utf-8"))
    sqlite["platform"]["name"] = "SQLite"
    sqlite["phases"]["throughput_test"]["status"] = "FAILED"
    sqlite["summary"] = {"queries": {"total": 66, "passed": 21, "failed": 45}, "validation": "partial"}
    (tmp_path / "SQLite.json").write_text(json.dumps(sqlite), encoding="utf-8")

    assert _throughput_cohort(validator, tmp_path) == {("tpch", "1.0#throughput#3streams"): {"DuckDB", "Spark"}}
    assert validator.main(tmp_path) == 1


def test_a_cohort_with_only_unrankable_bundles_is_reported_unranked_and_passes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    validator = _load_validator()
    for platform in ("BigQuery", "Databricks", "Snowflake"):
        _rankable_bundle(tmp_path, f"{platform}.json", platform, **UNRANKABLE_OVERRIDES["unofficial"])
    for platform in ("DuckDB", "DataFusion", "Spark"):
        _write_bundle(tmp_path, f"{platform}.json", benchmark="tpcds", scale=1.0, platform=platform)

    cohorts = validator.cohort_platforms(validator.discover_bundles(tmp_path))

    assert cohorts[("tpch", "1.0")] == set()
    assert validator.shallow_cohorts(cohorts) == {}
    assert validator.unranked_cohorts(cohorts) == [("tpch", "1.0")]
    assert validator.main(tmp_path) == 0
    captured = capsys.readouterr().out
    assert "tpch SF=1.0: 0 identities ([]) [UNRANKED" in captured
    assert "UNRANKED: 1 cohort(s)" in captured
    assert "All 1 ranked cohort(s) meet" in captured


@pytest.mark.parametrize("rankable", [1, 2])
def test_a_cohort_with_one_or_two_rankable_identities_still_fails(tmp_path: Path, rankable: int) -> None:
    validator = _load_validator()
    for index, platform in enumerate(("DuckDB", "DataFusion")[:rankable]):
        _rankable_bundle(tmp_path, f"ok{index}.json", platform)
    for platform in ("BigQuery", "Snowflake"):
        _rankable_bundle(tmp_path, f"{platform}.json", platform, **UNRANKABLE_OVERRIDES["unofficial"])

    shallow = validator.shallow_cohorts(validator.cohort_platforms(validator.discover_bundles(tmp_path)))

    assert len(shallow[("tpch", "1.0")]) == rankable
    assert validator.main(tmp_path) == 1


def test_the_gate_accepts_a_full_cohort(tmp_path: Path) -> None:
    validator = _load_validator()
    for platform in ("DuckDB", "DataFusion", "Spark"):
        _write_bundle(tmp_path, f"{platform}.json", benchmark="tpcds", scale=10.0, platform=platform)

    assert validator.shallow_cohorts(validator.cohort_platforms(validator.discover_bundles(tmp_path))) == {}


def test_the_gate_accepts_a_version_matrix_as_distinct_identities(tmp_path: Path) -> None:
    validator = _load_validator()
    matrix_dir = tmp_path / "duckdb-version-matrix"
    for index, version in enumerate(("1.0.0", "1.5.5", "1.6.0.dev365")):
        _write_bundle(
            matrix_dir,
            f"duckdb-{index}.json",
            benchmark="tpch",
            scale=10.0,
            platform="DuckDB",
            platform_version=version,
        )

    assert validator.shallow_cohorts(validator.cohort_platforms(validator.discover_bundles(tmp_path))) == {}


def test_versions_do_not_pad_an_ordinary_cross_platform_cohort(tmp_path: Path) -> None:
    validator = _load_validator()
    for index, version in enumerate(("1.0", "2.0", "3.0")):
        _write_bundle(
            tmp_path,
            f"datafusion-{index}.json",
            benchmark="tpch",
            scale=10.0,
            platform="DataFusion",
            platform_version=version,
        )

    cohorts = validator.cohort_platforms(validator.discover_bundles(tmp_path))
    assert cohorts == {("tpch", "10.0"): {"DataFusion"}}
    assert validator.shallow_cohorts(cohorts) == cohorts


def test_same_platform_version_does_not_pad_a_cohort(tmp_path: Path) -> None:
    validator = _load_validator()
    matrix_dir = tmp_path / "duckdb-version-matrix"
    for index in range(3):
        _write_bundle(
            matrix_dir,
            f"duckdb-{index}.json",
            benchmark="tpch",
            scale=10.0,
            platform="DuckDB",
            platform_version="1.5.5",
        )

    cohorts = validator.cohort_platforms(validator.discover_bundles(tmp_path))
    assert cohorts == {("tpch", "10.0"): {"DuckDB v1.5.5"}}
    assert validator.shallow_cohorts(cohorts) == cohorts


def test_duckdb_package_version_overrides_internal_engine_version(tmp_path: Path) -> None:
    validator = _load_validator()
    matrix_dir = tmp_path / "duckdb-version-matrix"
    _write_bundle(
        matrix_dir,
        "duckdb-dev.json",
        benchmark="tpch",
        scale=10.0,
        platform="DuckDB",
        platform_version="2.0.0-alpha38615",
        execution_version="1.6.0.dev365",
    )

    assert validator.cohort_platforms(validator.discover_bundles(tmp_path)) == {
        ("tpch", "10.0"): {"DuckDB v1.6.0.dev365"}
    }


def test_companion_files_are_not_counted_as_bundles(tmp_path: Path) -> None:
    validator = _load_validator()
    _write_bundle(tmp_path, "a.json", benchmark="tpch", scale=1.0, platform="DuckDB")
    for companion in ("a.manifest.json", "a.plans.json", "a.tuning.json", "a.applied.json"):
        (tmp_path / companion).write_text("{}", encoding="utf-8")
    (tmp_path / "submission-manifest.json").write_text("{}", encoding="utf-8")

    assert [path.name for path in validator.discover_bundles(tmp_path)] == ["a.json"]


def test_an_unreadable_bundle_fails_closed(tmp_path: Path) -> None:
    validator = _load_validator()
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(validator.CorpusReadError):
        validator.cohort_platforms(validator.discover_bundles(tmp_path))


@pytest.mark.parametrize(
    ("platforms", "expected_exit"),
    [(("DuckDB",), 1), (("DuckDB", "DataFusion", "Spark"), 0)],
)
def test_the_entry_point_returns_the_right_exit_code(
    tmp_path: Path, platforms: tuple[str, ...], expected_exit: int
) -> None:
    validator = _load_validator()
    for platform in platforms:
        _write_bundle(tmp_path, f"{platform}.json", benchmark="tpcds", scale=10.0, platform=platform)

    assert validator.main(tmp_path) == expected_exit


def test_recency_report_uses_bundle_timestamps(tmp_path: Path) -> None:
    validator = _load_validator()
    as_of = dt.date(2026, 9, 4)
    _write_bundle(
        tmp_path,
        "old.json",
        benchmark="tpch",
        scale=1.0,
        platform="DuckDB",
        run_timestamp="2026-05-02T10:00:00",
    )
    _write_bundle(
        tmp_path,
        "new.json",
        benchmark="tpch",
        scale=1.0,
        platform="DataFusion",
        run_timestamp="2026-08-26T10:00:00",
    )
    _write_bundle(
        tmp_path,
        "other.json",
        benchmark="tpcds",
        scale=10.0,
        platform="Spark",
        run_timestamp="2026-07-01T10:00:00",
    )

    overall, per_cohort, warnings = validator.cohort_recency(validator.discover_bundles(tmp_path), as_of=as_of)

    assert warnings == []
    assert overall is not None
    assert overall.oldest == dt.date(2026, 5, 2)
    assert overall.newest == dt.date(2026, 8, 26)
    assert overall.oldest_age_days == 125
    assert overall.newest_age_days == 9
    assert overall.bundle_count == 3
    assert per_cohort[("tpch", "1.0")].oldest_age_days == 125
    assert per_cohort[("tpch", "1.0")].newest_age_days == 9
    assert per_cohort[("tpcds", "10.0")].oldest_age_days == 65


@pytest.mark.parametrize(
    ("timestamp", "expected"),
    [
        ("2026-09-05", dt.date(2026, 9, 5)),
        ("2026-09-04T23:59:59-12:00", dt.date(2026, 9, 5)),
        ("2026-09-05T00:15:00+14:00", dt.date(2026, 9, 4)),
        ("2026-09-05T12:00:00Z", dt.date(2026, 9, 5)),
        ("2026-09-05T12:00:00", dt.date(2026, 9, 5)),
    ],
)
def test_run_timestamp_contract_uses_utc_calendar_days(timestamp: str, expected: dt.date) -> None:
    validator = _load_validator()
    assert validator.parse_run_date({"run": {"timestamp": timestamp}}) == expected


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-09-05Tnot-a-time",
        "2026-09-05T12:00",
        "2026-09-05T12:00:00Z trailing",
        "2026-02-30",
        "2026-09-05 12:00:00",
        "2026-09-05T12:00:00+24:00",
    ],
)
def test_run_timestamp_contract_rejects_malformed_or_trailing_text(timestamp: str) -> None:
    validator = _load_validator()
    with pytest.raises(validator.CorpusReadError, match="unparseable run.timestamp"):
        validator.parse_run_date({"run": {"timestamp": timestamp}})


def test_recency_defaults_to_the_utc_current_day(monkeypatch: pytest.MonkeyPatch) -> None:
    validator = _load_validator()
    monkeypatch.setattr(validator, "utc_today", lambda: dt.date(2026, 9, 5))
    assert validator.age_days(dt.date(2026, 9, 4)) == 1


def test_age_does_not_fail_a_deep_enough_cohort(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    validator = _load_validator()
    as_of = dt.date(2026, 9, 4)
    for platform in ("DuckDB", "DataFusion", "Spark"):
        _write_bundle(
            tmp_path,
            f"{platform}.json",
            benchmark="tpch",
            scale=1.0,
            platform=platform,
            run_timestamp="2026-01-01T00:00:00",
        )

    assert validator.main(tmp_path, as_of=as_of) == 0
    captured = capsys.readouterr().out
    assert "Recency" in captured
    assert "oldest=2026-01-01 (246 days)" in captured
    assert "informational only" in captured
    assert "does not affect ranking eligibility" in captured


def test_missing_run_timestamp_is_omitted_from_recency(tmp_path: Path) -> None:
    validator = _load_validator()
    as_of = dt.date(2026, 9, 4)
    _write_bundle(
        tmp_path,
        "ok.json",
        benchmark="tpch",
        scale=1.0,
        platform="DuckDB",
        run_timestamp="2026-05-02T10:00:00",
    )
    bare = {
        "benchmark": {"id": "tpch", "scale_factor": 1.0, "test_type": "power"},
        "platform": {"name": "DataFusion"},
    }
    (tmp_path / "bare.json").write_text(json.dumps(bare), encoding="utf-8")

    overall, per_cohort, warnings = validator.cohort_recency(validator.discover_bundles(tmp_path), as_of=as_of)

    assert len(warnings) == 1
    assert "run.timestamp" in warnings[0]
    assert warnings[0].startswith("WARN")
    assert overall is not None
    assert overall.bundle_count == 1
    assert overall.oldest == dt.date(2026, 5, 2)
    assert per_cohort[("tpch", "1.0")].bundle_count == 1


def test_recency_names_missing_parseable_timestamps_when_bundles_exist(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    validator = _load_validator()
    for platform in ("DuckDB", "DataFusion", "Spark"):
        _write_bundle(
            tmp_path,
            f"{platform}.json",
            benchmark="tpch",
            scale=1.0,
            platform=platform,
            run_timestamp="2026-09-05Tnot-a-time",
        )

    assert validator.main(tmp_path, as_of=dt.date(2026, 9, 5)) == 0
    captured = capsys.readouterr().out
    assert "Overall: no parseable run timestamps" in captured
    assert "Overall: no bundles" not in captured


def test_timestamp_less_bundle_does_not_fail_depth_exit(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    validator = _load_validator()
    as_of = dt.date(2026, 9, 4)
    for platform in ("DuckDB", "DataFusion", "Spark"):
        _write_bundle(
            tmp_path,
            f"{platform}.json",
            benchmark="tpch",
            scale=1.0,
            platform=platform,
            run_timestamp="2026-08-01T00:00:00",
        )
    bare = {
        "benchmark": {"id": "tpch", "scale_factor": 1.0, "test_type": "power"},
        "platform": {"name": "ClickHouse"},
    }
    (tmp_path / "bare.json").write_text(json.dumps(bare), encoding="utf-8")

    assert validator.main(tmp_path, as_of=as_of) == 0
    captured = capsys.readouterr().out
    assert "WARN" in captured
    assert "run.timestamp" in captured
    assert "Recency" in captured
    assert "oldest=2026-08-01" in captured
    assert "3 bundles" in captured
    assert "All 1 ranked cohort(s) meet" in captured


def test_override_companions_are_not_read_as_bundles(tmp_path: Path) -> None:
    validator = _load_validator()
    (tmp_path / "x_sf1_duckdb_sql_20261001_000000_aaaa.json").write_text("{}")
    (tmp_path / "x_sf1_duckdb_sql_20261001_000000_aaaa.override.json").write_text("{}")
    (tmp_path / "x_sf1_duckdb_sql_20261001_000000_aaaa.manifest.json").write_text("{}")

    names = [path.name for path in validator.discover_bundles(tmp_path)]

    assert names == ["x_sf1_duckdb_sql_20261001_000000_aaaa.json"]


def _explorer_view(path: Path) -> tuple[str, str | None]:
    entry = BundleTransformer().to_manifest_entry(path)
    return canonical_phase(entry.test_type), ranking_exclusion_reason(entry)


def _explorer_pipeline_view(path: Path) -> tuple[str, str | None]:
    entry = BundleTransformer().to_manifest_entry(path)
    return canonical_phase(entry.test_type), entry.ranking_exclusion_reason


def _parity_variants() -> dict[str, dict]:
    def variant(**changes: object) -> dict:
        import copy

        data = copy.deepcopy(MINIMAL_BUNDLE)
        for key, value in changes.items():
            if value is None:
                data.pop(key, None)
            else:
                data[key] = value
        return data

    def with_benchmark(**changes: object) -> dict:
        data = variant()
        data["benchmark"].update(changes)
        return data

    def without_test_type(phases: dict) -> dict:
        data = variant(phases=phases)
        del data["benchmark"]["test_type"]
        return data

    clean_queries = {"total": 2, "passed": 2, "failed": 0}

    def query(query_id: str, ms: object = 100.0, run_type: str | None = "measurement", **extra: object) -> dict:
        row: dict = {"id": query_id, "ms": ms, "status": "SUCCESS"}
        if run_type is not None:
            row["run_type"] = run_type
        row.update(extra)
        return row

    def with_queries(rows: list, total: int, **changes: object) -> dict:
        data = variant(queries=rows, **changes)
        data["summary"] = {**data["summary"], "queries": {"total": total, "passed": total, "failed": 0}}
        return data

    def with_tpc_metrics(metrics: dict, benchmark_id: str = "tpch", test_type: str = "power") -> dict:
        data = with_benchmark(id=benchmark_id, test_type=test_type)
        data["summary"] = {**data["summary"], "tpc_metrics": metrics}
        return data

    def with_sections(**sections: dict) -> dict:
        data = variant()
        for name, content in sections.items():
            data[name] = content
        return data

    def throughput_variant(**changes: object) -> dict:
        data = throughput_bundle(**{k: v for k, v in changes.items() if k != "summary"})
        if "summary" in changes:
            data["summary"] = {**data["summary"], **changes["summary"]}
        return data

    def throughput_phase_variant(**phase_changes: object) -> dict:
        data = throughput_variant(throughput_at_size=500.0, streams=3)
        phase = data["phases"]["throughput_test"]
        phase.update(phase_changes)
        return data

    def timed_out_stream() -> dict:
        data = throughput_variant(throughput_at_size=500.0, streams=3)
        phase = data["phases"]["throughput_test"]
        phase["status"] = "FAILED"
        phase["stream_results"] = phase["stream_results"][:2]
        phase["errors"] = ["Stream 3 timed out after 600s"]
        return data

    return {
        "throughput-timed-out-stream-with-score": timed_out_stream(),
        "throughput-phase-failed-with-score": throughput_phase_variant(status="FAILED"),
        "throughput-stream-unsuccessful-with-score": throughput_phase_variant(
            stream_results=[
                {"stream_id": 1, "success": True},
                {"stream_id": 2, "success": True},
                {"stream_id": 3, "success": False},
            ]
        ),
        "throughput-stream-success-not-boolean": throughput_phase_variant(
            stream_results=[{"stream_id": 1, "success": "true"}]
        ),
        "throughput-phase-errors-with-score": throughput_phase_variant(errors=["stream 2 reported an error"]),
        "throughput-outstanding-work-with-score": throughput_phase_variant(
            outstanding_work={"stream_ids": [3], "cleanup_state": "outstanding"}
        ),
        "throughput-empty-stream-results-with-score": throughput_phase_variant(stream_results=[]),
        "throughput-phase-block-missing-with-score": with_tpc_metrics(
            {"throughput_at_size": 500.0}, test_type="throughput"
        ),
        "throughput-clean-empty-errors": throughput_phase_variant(errors=[], outstanding_work=None),
        "throughput-without-power-score": with_tpc_metrics({"throughput_at_size": 3741.0}, test_type="throughput"),
        "throughput-clean": throughput_variant(),
        "throughput-clean-tpcds": throughput_variant(benchmark="tpcds"),
        "throughput-clean-two-streams": throughput_variant(streams=2),
        "throughput-power-score-only": throughput_variant(throughput_at_size=None, power_at_size=1234.0),
        "throughput-score-and-power-score": throughput_variant(power_at_size=1234.0),
        "throughput-no-score": throughput_variant(throughput_at_size=None),
        "throughput-zero-score": throughput_variant(throughput_at_size=0.0),
        "throughput-negative-score": throughput_variant(throughput_at_size=-1.0),
        "throughput-nan-score": throughput_variant(throughput_at_size=float("nan")),
        "throughput-unparseable-score": throughput_variant(throughput_at_size="abc"),
        "throughput-failed-queries": throughput_variant(
            summary={"queries": {"total": 66, "passed": 21, "failed": 45}, "validation": "partial"}
        ),
        "throughput-unofficial": throughput_variant(compliance_class="unofficial_subscale"),
        "throughput-geomean-benchmark": throughput_variant(benchmark="ssb", throughput_at_size=None),
        "power-with-throughput-score-only": with_tpc_metrics({"throughput_at_size": 3741.0}),
        "combined-phase-ranks-on-power-score": with_tpc_metrics({"power_at_size": 10.0}, test_type="combined"),
        "combined-phase-with-throughput-score-only": with_tpc_metrics(
            {"throughput_at_size": 3741.0}, test_type="combined"
        ),
        "geomean-benchmark-without-power-score": with_tpc_metrics({}, benchmark_id="ssb"),
        "tpch-without-power-score": with_tpc_metrics({}),
        "power-score-zero": with_tpc_metrics({"power_at_size": 0}),
        "power-score-unparseable-then-qphh": with_tpc_metrics({"power_at_size": "abc", "qphh_at_size": 5.0}),
        "power-score-nan": with_tpc_metrics({"power_at_size": float("nan")}),
        "tpcds-qphds": with_tpc_metrics({"qphds_at_size": 12.0}, benchmark_id="tpcds"),
        "custom-tuning-unverified": with_sections(config={"tuning_mode": "custom"}),
        "custom-tuning-applied": with_sections(
            config={"tuning_mode": "custom"},
            platform={"name": "duckdb", "tuning": {"validation_status": "applied_verified"}},
        ),
        "custom-tuning-noop": with_sections(
            config={"tuning_mode": "custom"}, platform={"name": "duckdb", "tuning": {"validation_status": "noop"}}
        ),
        "custom-tuning-from-execution": with_sections(execution={"tuning_mode": "custom"}),
        "legacy-tuning-mode": with_sections(config={"tuning_mode": "balanced"}),
        "single-valid-query": with_queries([query("Q1")], 2),
        "zero-timings": with_queries([query("Q1", 0.0), query("Q2", 0)], 2),
        "negative-timing": with_queries([query("Q1", -5.0), query("Q2")], 2),
        "no-queries": with_queries([], 2),
        "warmup-only": with_queries([query("Q1", 5.0, "warmup"), query("Q2", 5.0, "warmup")], 2),
        "legacy-unlabelled-runs": with_queries([query("Q1", 5.0, None), query("Q2", 7.0, None)], 2),
        "pseudo-rows-ignored": with_queries(
            [query("Q1"), query("Q2"), query("summary", 0.0, "summary"), query("meta", 0.0, "metadata")], 2
        ),
        "query-id-key": with_queries([{"query_id": "Q1", "ms": 5.0}, {"query_id": "Q2", "ms": 6.0}], 2),
        "execution-time-fallback": with_queries(
            [{"id": "Q1", "execution_time_ms": 5.0}, {"id": "Q2", "execution_time_ms": 6.0}], 2
        ),
        "unparseable-ms": with_queries([query("Q1", "abc"), query("Q2")], 2),
        "null-status": with_queries([query("Q1", status=None), query("Q2")], 2),
        "repeated-samples": with_queries([query(q) for q in ("Q1", "Q2") for _ in range(4)], 8),
        "coverage-below-half": with_queries([query("Q1"), query("Q2")], 22),
        "dataframe-skip-summary": with_queries(
            [query("Q1", dataframe_skip_summary={"executed_total": 2, "skipped_total": 20}), query("Q2")], 2
        ),
        "dataframe-skip-summary-full": with_queries(
            [query("Q1", dataframe_skip_summary={"executed_total": 2, "skipped_total": 0}), query("Q2")], 2
        ),
        "non-dict-query-row": with_queries(["not-a-row", query("Q2")], 2),
        "clean": variant(),
        "failed-count": variant(summary={"queries": {"total": 2, "passed": 1, "failed": 1}, "validation": "passed"}),
        "passed-short": variant(summary={"queries": {"total": 3, "passed": 2}, "validation": "passed"}),
        "partial": variant(summary={"queries": clean_queries, "validation": "partial"}),
        "dict-status": variant(summary={"queries": clean_queries, "validation": {"status": "FAILED"}}),
        "no-validation": variant(summary={"queries": clean_queries}),
        "unvalidated-failure": variant(summary={"queries": {"total": 2, "passed": 1, "failed": 1}}),
        "translation-fallback": variant(
            summary={"queries": clean_queries, "validation": "passed"}, execution={"translation": "fallback"}
        ),
        "translation-fallback-no-validation": variant(
            summary={"queries": clean_queries}, execution={"translation": "failed"}
        ),
        "unofficial": with_benchmark(compliance_class="unofficial_nonstandard"),
        "official": with_benchmark(compliance_class="official"),
        "phase-upper": with_benchmark(test_type="POWER"),
        "phase-standard": with_benchmark(test_type="standard"),
        "phase-throughput": with_benchmark(test_type="throughput"),
        "phase-from-power": without_test_type({"power_test": {"status": "COMPLETED"}}),
        "phase-from-throughput": without_test_type({"throughput_test": {"status": "COMPLETED"}}),
        "phase-not-run-power": without_test_type(
            {"power_test": {"status": "NOT_RUN"}, "throughput_test": {"status": "COMPLETED"}}
        ),
        "phase-all-not-run": without_test_type(
            {"power_test": {"status": "NOT_RUN"}, "throughput_test": {"status": "NOT_RUN"}}
        ),
        "phase-empty": without_test_type({"power_test": {}}),
        "phase-none": without_test_type({}),
    }


@pytest.mark.parametrize("name", sorted(_parity_variants()))
def test_validator_agrees_with_the_explorer_on_phase_and_rankability(tmp_path: Path, name: str) -> None:
    validator = _load_validator()
    path = tmp_path / "bundle.json"
    payload = _parity_variants()[name]
    path.write_text(json.dumps(payload), encoding="utf-8")

    explorer_phase, explorer_reason = _explorer_pipeline_view(path)

    assert validator.bundle_phase(payload) == explorer_phase
    assert validator.exclusion_reason(payload) == explorer_reason
    assert validator.bundle_rankable(payload) is (explorer_reason is None)


@pytest.mark.parametrize("name", sorted(_parity_variants()))
def test_validator_agrees_with_the_explorer_on_the_throughput_stream_count(tmp_path: Path, name: str) -> None:
    validator = _load_validator()
    path = tmp_path / "bundle.json"
    payload = _parity_variants()[name]
    path.write_text(json.dumps(payload), encoding="utf-8")

    entry = BundleTransformer().to_manifest_entry(path)
    validator_streams = validator._stream_count(payload) if validator.bundle_phase(payload) == "throughput" else None

    assert entry.stream_count == validator_streams
    if entry.stream_count is not None:
        assert validator.cohort_phase_suffix(payload) == f"#throughput#{entry.stream_count}streams"


def _load_inventory_generator() -> ModuleType:
    path = REPO_ROOT / "scripts" / "generate_corpus_inventory.py"
    spec = importlib.util.spec_from_file_location("generate_corpus_inventory", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name", sorted(_parity_variants()))
def test_inventory_validator_and_explorer_agree_on_the_cohort_phase(tmp_path: Path, name: str) -> None:
    validator = _load_validator()
    inventory = _load_inventory_generator()
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    payload = _parity_variants()[name]
    (bundles / "bundle.json").write_text(json.dumps(payload), encoding="utf-8")

    benchmark_id, scale = validator._cohort_key(payload)
    explorer_phase, _ = _explorer_view(bundles / "bundle.json")
    cohorts = inventory.generate_inventory(bundles)["cohorts"]

    assert list(cohorts) == [f"{benchmark_id}@sf{scale}"]
    assert validator.bundle_phase(payload) == explorer_phase
    assert (scale.split("#")[1] if "#" in scale else "power") == explorer_phase


def test_validator_agrees_with_the_explorer_on_every_committed_bundle() -> None:
    validator = _load_validator()
    bundles = validator.discover_bundles(BUNDLES)
    assert bundles

    disagreements = []
    for path in bundles:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if (validator.bundle_phase(payload), validator.exclusion_reason(payload)) != _explorer_view(path):
            disagreements.append(path.name)

    assert not disagreements


def test_every_committed_bundle_declares_a_phase() -> None:
    validator = _load_validator()
    undeclared = [
        path.name
        for path in validator.discover_bundles(BUNDLES)
        if validator.bundle_phase(json.loads(path.read_text(encoding="utf-8"))) == "unknown"
    ]

    assert not undeclared


def test_copied_constants_match_their_explorer_sources() -> None:
    validator = _load_validator()

    assert {
        name for name, config in RANKING_METRIC_BY_FAMILY.items() if config.primary_metric == "power_score"
    } == validator.POWER_SCORE_BENCHMARKS
    assert {
        family
        for (family, phase), config in RANKING_METRIC_BY_FAMILY_PHASE.items()
        if phase == THROUGHPUT_PHASE and config.primary_metric == "throughput_at_size"
    } == validator.THROUGHPUT_SCORE_BENCHMARKS
    assert {phase for _, phase in RANKING_METRIC_BY_FAMILY_PHASE} == {validator.THROUGHPUT_PHASE}
    assert THROUGHPUT_PHASE == validator.THROUGHPUT_PHASE
    assert set(MODES) == validator.CANONICAL_TUNING_MODES
    assert set(APPLIED_TUNING_STATUSES) == validator.APPLIED_TUNING_STATUSES
    assert validator.KNOWN_LOGICAL_QUERY_COUNTS == explorer_transformer._KNOWN_LOGICAL_QUERY_COUNTS
    assert validator.PASS_STATUSES == explorer_transformer._PASS_STATUSES
    assert validator.EXECUTION_RUN_TYPES == explorer_transformer._ALLOWED_EXECUTION_RUN_TYPES
    assert validator.NON_CLEAN_VALIDATION_STATUSES == NON_CLEAN_VALIDATION_STATUSES
    assert validator.NON_CLEAN_TRANSLATION_STATUSES == NON_CLEAN_TRANSLATION_STATUSES
    assert validator.UNOFFICIAL_COMPLIANCE_CLASSES == UNOFFICIAL_COMPLIANCE_CLASSES
    assert validator.PHASE_ALIASES == _PHASE_ALIASES


@pytest.mark.parametrize(
    ("name", "expected_reason"),
    [
        ("throughput-timed-out-stream-with-score", "stream_count_mismatch"),
        ("throughput-phase-failed-with-score", "missing_primary_metric"),
        ("throughput-stream-unsuccessful-with-score", "missing_primary_metric"),
        ("throughput-stream-success-not-boolean", "stream_count_mismatch"),
        ("throughput-phase-errors-with-score", "missing_primary_metric"),
        ("throughput-outstanding-work-with-score", "missing_primary_metric"),
        ("throughput-empty-stream-results-with-score", "stream_count_mismatch"),
        ("throughput-phase-block-missing-with-score", "missing_primary_metric"),
    ],
)
def test_an_unvalidated_throughput_phase_is_not_ranked_even_with_a_score(
    tmp_path: Path, name: str, expected_reason: str
) -> None:
    validator = _load_validator()
    path = tmp_path / "bundle.json"
    payload = _parity_variants()[name]
    path.write_text(json.dumps(payload), encoding="utf-8")

    explorer_phase, explorer_reason = _explorer_pipeline_view(path)

    assert validator.exclusion_reason(payload) == expected_reason
    assert explorer_reason == expected_reason
    assert explorer_phase == "throughput"
    assert BundleTransformer().to_manifest_entry(path).throughput_at_size is None


def test_a_timed_out_stream_cannot_form_a_throughput_identity(tmp_path: Path) -> None:
    validator = _load_validator()
    for platform in ("DuckDB", "Spark"):
        _write_phase_bundle(
            tmp_path,
            f"{platform}.json",
            platform=platform,
            power="NOT_RUN",
            throughput="COMPLETED",
            test_type="throughput",
        )
    timed_out = json.loads((tmp_path / "Spark.json").read_text(encoding="utf-8"))
    timed_out["platform"]["name"] = "Doris"
    phase = timed_out["phases"]["throughput_test"]
    phase["status"] = "FAILED"
    phase["stream_results"] = phase["stream_results"][:2]
    phase["errors"] = ["Stream 3 timed out"]
    timed_out["summary"]["tpc_metrics"]["throughput_at_size"] = 500.0
    timed_out["run"].pop("streams")
    (tmp_path / "Doris.json").write_text(json.dumps(timed_out), encoding="utf-8")

    cohorts = validator.cohort_platforms(validator.discover_bundles(tmp_path))

    assert cohorts[("tpch", "1.0#throughput#3streams")] == {"DuckDB", "Spark"}
    assert cohorts[("tpch", "1.0#throughput#2streams")] == set()
    assert validator.main(tmp_path) == 1
