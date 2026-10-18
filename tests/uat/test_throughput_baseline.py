from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests.uat import throughput_baseline
from tests.uat.throughput import main
from tests.uat.throughput_baseline import (
    detect_cpu_model,
    load_baseline_records,
    parse_cpuinfo,
    record_baseline,
    rolling_median,
    runner_class_for,
)

pytestmark = pytest.mark.medium

SLOW = "AMD EPYC 7763 64-Core Processor"
FAST = "Intel(R) Xeon(R) Platinum 8370C CPU @ 2.80GHz"

X86_CPUINFO = """processor\t: 0
vendor_id\t: AuthenticAMD
cpu family\t: 25
model\t\t: 1
model name\t: AMD EPYC 7763 64-Core Processor
stepping\t: 1
microcode\t: 0xa0011d1
cpu MHz\t\t: 3242.100

processor\t: 1
vendor_id\t: AuthenticAMD
cpu family\t: 25
model\t\t: 1
model name\t: AMD EPYC 7763 64-Core Processor
"""

ARM_BOARD_CPUINFO = """processor\t: 0
BogoMIPS\t: 108.00
Features\t: fp asimd evtstrm crc32 cpuid
CPU implementer\t: 0x41
CPU architecture: 8
CPU variant\t: 0x0
CPU part\t: 0xd08
CPU revision\t: 3

Hardware\t: BCM2711
Revision\t: c03111
Model\t\t: Raspberry Pi 4 Model B Rev 1.1
"""

ARM_SERVER_CPUINFO = """processor\t: 0
BogoMIPS\t: 2100.00
Features\t: fp asimd evtstrm aes pmull sha1 sha2 crc32 atomics
CPU implementer\t: 0x41
CPU architecture: 8
CPU variant\t: 0x3
CPU part\t: 0xd0c
CPU revision\t: 1
"""

POWER_CPUINFO = """processor\t: 0
cpu\t\t: POWER8 (architected), altivec supported
model\t\t: 8247-22L
machine\t\t: PowerNV 8247-22L
"""


def _good_result(throughput_at_size: float) -> dict:
    return {"summary": {"tpc_metrics": {"throughput_at_size": throughput_at_size}}}


def _write(tmp_path: Path, value: float, *, run_id: str, day: int, cpu: str = SLOW, cpus: int = 4) -> Path:
    return record_baseline(
        tmp_path / run_id,
        _good_result(value),
        platform="duckdb",
        benchmark="tpch",
        scale=1,
        streams=3,
        env={"GITHUB_RUN_ID": run_id, "GITHUB_RUN_ATTEMPT": "2"},
        cpu_model=cpu,
        cpu_count=cpus,
        recorded_at=datetime(2026, 10, day, 6, 0, tzinfo=timezone.utc),
    )


def test_parse_cpuinfo_prefers_model_name_over_the_numeric_x86_model_line():
    assert parse_cpuinfo(X86_CPUINFO) == SLOW


def test_parse_cpuinfo_uses_hardware_on_arm_boards():
    assert parse_cpuinfo(ARM_BOARD_CPUINFO) == "BCM2711"


def test_parse_cpuinfo_describes_arm_servers_that_report_only_implementer_and_part():
    assert parse_cpuinfo(ARM_SERVER_CPUINFO) == "arm 0x41 0xd0c"


def test_parse_cpuinfo_uses_a_non_numeric_model_as_a_fallback():
    assert parse_cpuinfo(POWER_CPUINFO) == "8247-22L"


def test_parse_cpuinfo_ignores_a_numeric_model_with_nothing_else():
    assert parse_cpuinfo("processor\t: 0\nmodel\t\t: 1\n") is None
    assert parse_cpuinfo("") is None


def test_detect_cpu_model_reads_proc_cpuinfo_and_falls_back_to_the_platform_processor(monkeypatch):
    monkeypatch.setattr(Path, "read_text", lambda self, **kwargs: X86_CPUINFO)
    assert detect_cpu_model() == SLOW

    def missing(self, **kwargs):
        raise OSError

    monkeypatch.setattr(Path, "read_text", missing)
    monkeypatch.setattr(throughput_baseline._platform, "processor", lambda: "arm")
    assert detect_cpu_model() == "arm"
    monkeypatch.setattr(throughput_baseline._platform, "processor", lambda: "")
    assert detect_cpu_model() == "unknown"


def test_a_real_x86_runner_gets_a_cpu_model_class_not_the_numeric_model_line():
    assert runner_class_for(parse_cpuinfo(X86_CPUINFO), 4) == "amd-epyc-7763-64-core-processor-4cpu"


def test_record_baseline_captures_throughput_runner_and_run_identity(tmp_path: Path):
    path = _write(tmp_path, 98548.3, run_id="555", day=4)

    record = json.loads(path.read_text(encoding="utf-8"))

    assert path.name == "throughput-baseline-duckdb-tpch-sf1-555-2.json"
    assert record["throughput_at_size"] == pytest.approx(98548.3)
    assert record["runner_class"] == "amd-epyc-7763-64-core-processor-4cpu"
    assert record["cpu_model"] == SLOW
    assert record["cpu_count"] == 4
    assert record["run_id"] == "555"
    assert record["run_attempt"] == "2"
    assert record["commit_sha"] == ""
    assert record["runner_image"] == ""
    assert record["platform"] == "duckdb"
    assert record["scale_factor"] == 1.0
    assert record["streams"] == 3
    assert record["recorded_at"] == "2026-10-04T06:00:00+00:00"


def test_record_baseline_captures_commit_and_runner_image_from_the_environment(tmp_path: Path):
    path = record_baseline(
        tmp_path,
        _good_result(1.0),
        platform="duckdb",
        benchmark="tpch",
        scale=1,
        streams=3,
        env={"GITHUB_SHA": "abc123", "ImageVersion": "20260930.1"},
        cpu_model=SLOW,
        cpu_count=4,
    )

    record = json.loads(path.read_text(encoding="utf-8"))

    assert (record["commit_sha"], record["runner_image"]) == ("abc123", "20260930.1")


def test_runner_class_separates_cpu_models_and_core_counts():
    assert runner_class_for(FAST, 4) != runner_class_for(SLOW, 4)
    assert runner_class_for(SLOW, 2) != runner_class_for(SLOW, 4)
    assert runner_class_for("", 4) == "unknown-4cpu"


def test_load_baseline_records_reads_nested_artifact_dirs_and_skips_bad_files(tmp_path: Path):
    _write(tmp_path, 1.0, run_id="555", day=4)
    _write(tmp_path, 2.0, run_id="556", day=5)
    (tmp_path / "557").mkdir()
    (tmp_path / "557" / "throughput-baseline-garbage.json").write_text("{oops", encoding="utf-8")
    (tmp_path / "558").mkdir()
    (tmp_path / "558" / "throughput-baseline-old.json").write_text('{"schema_version": 0}', encoding="utf-8")

    assert sorted(record["run_id"] for record in load_baseline_records(tmp_path)) == ["555", "556"]


def test_rolling_median_is_computed_per_runner_class(tmp_path: Path):
    for day, value in zip(range(1, 4), (73000, 75000, 79000)):
        _write(tmp_path, value, run_id=str(day), day=day, cpu=SLOW)
    for day, value in zip(range(1, 4), (92000, 96000, 98000)):
        _write(tmp_path, value, run_id=str(10 + day), day=day, cpu=FAST)
    records = load_baseline_records(tmp_path)

    slow = rolling_median(
        records, platform="duckdb", benchmark="tpch", scale=1, streams=3, runner_class=runner_class_for(SLOW, 4)
    )
    fast = rolling_median(
        records, platform="duckdb", benchmark="tpch", scale=1, streams=3, runner_class=runner_class_for(FAST, 4)
    )

    assert (slow.median, slow.count) == (75000, 3)
    assert (fast.median, fast.count) == (96000, 3)


def test_rolling_median_uses_only_the_latest_window_of_runs(tmp_path: Path):
    for day in range(1, 8):
        _write(tmp_path, float(day * 1000), run_id=str(day), day=day)

    result = rolling_median(
        load_baseline_records(tmp_path),
        platform="duckdb",
        benchmark="tpch",
        scale=1,
        streams=3,
        runner_class=runner_class_for(SLOW, 4),
        window=3,
    )

    assert (result.median, result.count) == (6000, 3)


def test_rolling_median_needs_a_matching_cell(tmp_path: Path):
    _write(tmp_path, 1000.0, run_id="1", day=1)
    records = load_baseline_records(tmp_path)
    runner_class = runner_class_for(SLOW, 4)

    assert (
        rolling_median(records, platform="postgresql", benchmark="tpch", scale=1, streams=3, runner_class=runner_class)
        is None
    )
    assert (
        rolling_median(records, platform="duckdb", benchmark="tpch", scale=10, streams=3, runner_class=runner_class)
        is None
    )
    assert (
        rolling_median(records, platform="duckdb", benchmark="tpch", scale=1, streams=3, runner_class="other") is None
    )
    assert (
        rolling_median(records, platform="duckdb", benchmark="tpch", scale=1, streams=1, runner_class=runner_class)
        is None
    )


def _median_argv(tmp_path: Path, runner_class: str) -> list[str]:
    return [
        "rolling-median",
        "--baseline-dir",
        str(tmp_path),
        "--platform",
        "duckdb",
        "--benchmark",
        "tpch",
        "--scale",
        "1",
        "--streams",
        "3",
        "--runner-class",
        runner_class,
    ]


def test_cli_rolling_median_prints_per_class_median(tmp_path: Path, capsys):
    for day, value in zip(range(1, 4), (1000.0, 2000.0, 3000.0)):
        _write(tmp_path, value, run_id=str(day), day=day)
    runner_class = runner_class_for(SLOW, 4)

    assert main(_median_argv(tmp_path, runner_class)) == 0

    assert json.loads(capsys.readouterr().out) == {"count": 3, "median": 2000.0, "runner_class": runner_class}


def test_cli_rolling_median_fails_without_baselines(tmp_path: Path, capsys):
    assert main(_median_argv(tmp_path, "none")) == 1
    assert "no retained baselines" in capsys.readouterr().out


def _record(
    directory: Path, value: float, *, run_id: str, attempt: str = "1", recorded_at: datetime, streams: int = 3
) -> Path:
    return record_baseline(
        directory,
        _good_result(value),
        platform="duckdb",
        benchmark="tpch",
        scale=1,
        streams=streams,
        env={"GITHUB_RUN_ID": run_id, "GITHUB_RUN_ATTEMPT": attempt},
        cpu_model=SLOW,
        cpu_count=4,
        recorded_at=recorded_at,
    )


def _values(records: list[dict]) -> list[float]:
    return sorted(record["throughput_at_size"] for record in records)


def test_load_baseline_records_rejects_a_record_outside_its_own_run_directory(tmp_path: Path):
    when = datetime(2026, 10, 1, tzinfo=timezone.utc)
    _record(tmp_path / "100", 1.0, run_id="100", recorded_at=when)
    _record(tmp_path / "200", 2.0, run_id="999", recorded_at=when)
    _record(tmp_path / "300" / "artifact", 3.0, run_id="300", recorded_at=when)

    assert _values(load_baseline_records(tmp_path)) == [1.0, 3.0]


def test_load_baseline_records_rejects_records_dated_in_the_future(tmp_path: Path):
    _record(tmp_path / "100", 1.0, run_id="100", recorded_at=datetime(2026, 10, 1, tzinfo=timezone.utc))
    _record(tmp_path / "200", 2.0, run_id="200", recorded_at=datetime(9999, 1, 1, tzinfo=timezone.utc))

    assert _values(load_baseline_records(tmp_path)) == [1.0]


def test_load_baseline_records_keeps_the_highest_attempt_per_run(tmp_path: Path):
    when = datetime(2026, 10, 1, tzinfo=timezone.utc)
    _record(tmp_path / "100" / "a", 1.0, run_id="100", attempt="1", recorded_at=when)
    _record(tmp_path / "100" / "b", 2.0, run_id="100", attempt="10", recorded_at=when)
    _record(tmp_path / "100" / "c", 3.0, run_id="100", attempt="2", recorded_at=when)

    assert _values(load_baseline_records(tmp_path)) == [2.0]


def test_load_baseline_records_drops_a_future_dated_attempt_before_choosing_the_latest(tmp_path: Path):
    _record(
        tmp_path / "100" / "a", 1.0, run_id="100", attempt="1", recorded_at=datetime(2026, 10, 1, tzinfo=timezone.utc)
    )
    _record(
        tmp_path / "100" / "b", 9.0, run_id="100", attempt="2", recorded_at=datetime(9999, 1, 1, tzinfo=timezone.utc)
    )

    assert _values(load_baseline_records(tmp_path)) == [1.0]


def test_load_baseline_records_ignores_records_older_than_not_before(tmp_path: Path):
    for run_id, day in (("100", 1), ("200", 10), ("300", 20)):
        _record(
            tmp_path / run_id, float(day), run_id=run_id, recorded_at=datetime(2026, 1, day, 6, tzinfo=timezone.utc)
        )

    assert _values(load_baseline_records(tmp_path, not_before="2026-01-10")) == [10.0, 20.0]
    assert _values(load_baseline_records(tmp_path, not_before="2026-01-10T07:00:00+00:00")) == [20.0]
    assert _values(load_baseline_records(tmp_path)) == [1.0, 10.0, 20.0]


@pytest.mark.parametrize("payload", ["[]", '{"schema_version": 1}', '{"schema_version": 1, "run_id": "1"}'])
def test_load_baseline_records_skips_records_missing_identity_fields(tmp_path: Path, payload: str):
    (tmp_path / "1").mkdir()
    (tmp_path / "1" / "throughput-baseline-x.json").write_text(payload, encoding="utf-8")

    assert load_baseline_records(tmp_path) == []


def test_rolling_median_never_mixes_stream_counts(tmp_path: Path):
    when = datetime(2026, 10, 1, tzinfo=timezone.utc)
    _record(tmp_path / "100", 100.0, run_id="100", recorded_at=when, streams=3)
    _record(tmp_path / "200", 900.0, run_id="200", recorded_at=when, streams=4)
    records = load_baseline_records(tmp_path)
    kwargs = {"platform": "duckdb", "benchmark": "tpch", "scale": 1, "runner_class": runner_class_for(SLOW, 4)}

    assert (
        rolling_median(records, streams=3, **kwargs).median,
        rolling_median(records, streams=3, **kwargs).count,
    ) == (100.0, 1)
    assert rolling_median(records, streams=4, **kwargs).median == 900.0
    assert rolling_median(records, streams=5, **kwargs) is None


def test_load_baseline_records_requires_the_run_id_as_the_first_directory(tmp_path: Path):
    when = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _record(tmp_path / "100", 1.0, run_id="100", recorded_at=when)
    _record(tmp_path / "100" / "200", 2.0, run_id="200", recorded_at=when)
    _record(tmp_path / "artifact" / "300", 3.0, run_id="300", recorded_at=when)

    assert _values(load_baseline_records(tmp_path)) == [1.0]


@pytest.mark.parametrize("value", ["10/01/2026", "yesterday", "2026-13-01"])
def test_load_baseline_records_rejects_a_malformed_not_before(tmp_path: Path, value: str):
    with pytest.raises(ValueError, match="THROUGHPUT_BASELINE_MIN_RECORDED_AT"):
        load_baseline_records(tmp_path, not_before=value)


def test_load_baseline_records_treats_a_naive_not_before_as_utc(tmp_path: Path):
    _record(tmp_path / "100", 1.0, run_id="100", recorded_at=datetime(2026, 1, 1, 6, tzinfo=timezone.utc))

    assert _values(load_baseline_records(tmp_path, not_before="2026-01-01T06:00:00")) == [1.0]
    assert load_baseline_records(tmp_path, not_before="2026-01-01T06:00:01") == []
