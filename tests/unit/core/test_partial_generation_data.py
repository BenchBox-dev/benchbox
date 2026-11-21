from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from benchbox.core.runner.runner import _ensure_data_generated, _validate_manifest_if_present
from benchbox.core.schemas import BenchmarkConfig
from benchbox.core.ssb.generator import SSBDataGenerator
from benchbox.core.tpcds.generator import TPCDSDataGenerator
from benchbox.core.tpch.generator import TPCHDataGenerator
from benchbox.ssb import SSB
from benchbox.utils import run_deadline
from benchbox.utils.data_validation import BenchmarkDataValidator
from benchbox.utils.generation_marker import (
    GENERATION_IN_PROGRESS_MARKER,
    generation_in_progress,
    track_generation,
)
from benchbox.utils.run_deadline import RunDeadline, RunTimeoutError, active_run_deadline
from tests.unit.core.generator_stubs import make_stub

pytestmark = [pytest.mark.unit, pytest.mark.medium]

HANG_SECONDS = 20


@pytest.fixture
def deadline(monkeypatch: pytest.MonkeyPatch):
    clock = [1000.0]
    monkeypatch.setattr(run_deadline, "mono_time", lambda: clock[0])
    monkeypatch.setattr(run_deadline, "elapsed_seconds", lambda started: clock[0] - started)
    active = RunDeadline(1)
    clock[0] += 0.7
    return active


def _expected_rows(benchmark: str) -> dict[str, int]:
    expectations = BenchmarkDataValidator(benchmark, 0.01).table_expectations
    return {table: max(1, expectation.expected_rows) for table, expectation in expectations.items()}


def _partial(rows: int) -> int:
    return rows if rows < 100 else int(rows * 0.97)


def _tpch_files(partial: bool) -> dict[str, int]:
    return {f"{table}.tbl": _partial(rows) if partial else rows for table, rows in _expected_rows("tpch").items()}


def _tpcds_files(partial: bool) -> dict[str, int]:
    return {f"{table}.dat": _partial(rows) if partial else rows for table, rows in _expected_rows("tpcds").items()}


def _line_count(path: Path) -> int:
    return path.read_text(encoding="utf-8").count("\n")


def _tpch(tmp_path: Path, stub) -> TPCHDataGenerator:
    generator = TPCHDataGenerator(scale_factor=0.01, output_dir=tmp_path / "data", compress_data=False)
    (stub.path.parent / "dists.dss").write_text("dists", encoding="utf-8")
    generator._dbgen_exe = stub.path
    generator.dbgen_available = True
    generator.dbgen_path = stub.path.parent
    return generator


def _tpcds(tmp_path: Path, stub) -> TPCDSDataGenerator:
    generator = TPCDSDataGenerator(scale_factor=0.01, output_dir=tmp_path / "data", compress_data=False)
    generator.dsdgen_exe = stub.path
    generator.dsdgen_available = True
    return generator


GENERATORS = {
    "tpch": (_tpch, "dbgen", _tpch_files, "lineitem.tbl"),
    "tpcds": (_tpcds, "dsdgen", _tpcds_files, "store_sales.dat"),
}


@pytest.fixture(params=sorted(GENERATORS))
def scenario(request, tmp_path, monkeypatch):
    build, tool, files, sample = GENERATORS[request.param]

    def generator(**spec):
        stub = make_stub(tmp_path, monkeypatch, tool, **spec)
        return build(tmp_path, stub), stub

    return request.param, generator, files, sample, tmp_path / "data"


def test_a_directory_left_by_a_killed_generator_is_regenerated(scenario, deadline):
    benchmark, generator, files, sample, data = scenario
    killed, stub = generator(files=files(True), hang=HANG_SECONDS)

    with active_run_deadline(deadline), pytest.raises(RunTimeoutError):
        killed.generate()

    assert (data / GENERATION_IN_PROGRESS_MARKER).exists()
    assert not (data / "_datagen_manifest.json").exists()
    assert _line_count(data / sample) < _expected_rows(benchmark)[sample.split(".")[0]]

    complete, stub = generator(files=files(False))
    complete.generate()

    assert len(stub.invocations()) == 2
    assert _line_count(data / sample) == _expected_rows(benchmark)[sample.split(".")[0]]
    assert not (data / GENERATION_IN_PROGRESS_MARKER).exists()
    assert (data / "_datagen_manifest.json").exists()


def test_a_directory_with_a_manifest_and_the_marker_is_regenerated(scenario):
    _, generator, files, _, data = scenario
    first, stub = generator(files=files(False))
    first.generate()
    assert (data / "_datagen_manifest.json").exists()
    (data / GENERATION_IN_PROGRESS_MARKER).touch()

    second, stub = generator(files=files(False))
    second.generate()

    assert len(stub.invocations()) == 2
    assert not (data / GENERATION_IN_PROGRESS_MARKER).exists()


def test_a_completed_generation_leaves_no_marker_and_is_reused(scenario):
    _, generator, files, _, data = scenario
    first, stub = generator(files=files(False))
    first.generate()
    assert not (data / GENERATION_IN_PROGRESS_MARKER).exists()

    second, stub = generator(files=files(False))
    second.generate()

    assert len(stub.invocations()) == 1
    assert not (data / GENERATION_IN_PROGRESS_MARKER).exists()


def test_the_scan_path_refuses_nearly_complete_data_while_the_marker_is_present(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    for name, rows in _tpch_files(True).items():
        (data / name).write_text("1|row|\n" * rows, encoding="utf-8")
    validator = BenchmarkDataValidator("tpch", 0.01)

    assert validator.validate_data_directory(data).valid
    (data / "_datagen_manifest.json").unlink()
    (data / GENERATION_IN_PROGRESS_MARKER).touch()
    result = validator.validate_data_directory(data)

    assert not result.valid
    assert GENERATION_IN_PROGRESS_MARKER in result.issues[0]
    assert not (data / "_datagen_manifest.json").exists()


def _ssb_config() -> BenchmarkConfig:
    return BenchmarkConfig(name="ssb", display_name="SSB", scale_factor=0.01, compress_data=False, options={})


def _ssb(data: Path) -> SSB:
    return SSB(scale_factor=0.01, output_dir=data, compress_data=False)


def test_a_non_tpc_benchmark_leaves_no_marker_and_is_reused(tmp_path):
    data = tmp_path / "data"

    assert _ensure_data_generated(_ssb(data), _ssb_config()) == (True, False)
    assert not (data / GENERATION_IN_PROGRESS_MARKER).exists()

    assert _ensure_data_generated(_ssb(data), _ssb_config()) == (False, True)


def test_a_non_tpc_benchmark_regenerates_data_that_has_a_manifest_and_the_marker(tmp_path):
    data = tmp_path / "data"
    _ensure_data_generated(_ssb(data), _ssb_config())
    (data / GENERATION_IN_PROGRESS_MARKER).touch()

    assert _ensure_data_generated(_ssb(data), _ssb_config()) == (True, False)

    assert not (data / GENERATION_IN_PROGRESS_MARKER).exists()


def test_a_non_tpc_generation_that_stops_leaves_the_marker(tmp_path, monkeypatch):
    data = tmp_path / "data"

    def stopped(self):
        raise RunTimeoutError(1, "generate")

    monkeypatch.setattr(SSBDataGenerator, "_generate_part_data", stopped)
    with pytest.raises(RunTimeoutError):
        _ssb(data).generate_data()

    assert (data / GENERATION_IN_PROGRESS_MARKER).exists()
    assert not (data / "_datagen_manifest.json").exists()


def test_the_manifest_path_refuses_a_manifest_while_the_marker_is_present(tmp_path):
    data = tmp_path / "data"
    _ensure_data_generated(_ssb(data), _ssb_config())
    benchmark = Mock()
    benchmark.output_dir = data

    assert _validate_manifest_if_present(benchmark, _ssb_config(), quiet=True)[0] is True
    (data / GENERATION_IN_PROGRESS_MARKER).touch()
    assert _validate_manifest_if_present(benchmark, _ssb_config(), quiet=True) == (False, None, True)


def test_a_generation_sees_its_own_marker_as_absent_and_a_stale_one_as_present(tmp_path):
    seen: list[bool] = []

    with track_generation(tmp_path):
        seen.append(generation_in_progress(tmp_path))
        assert (tmp_path / GENERATION_IN_PROGRESS_MARKER).exists()
    assert not (tmp_path / GENERATION_IN_PROGRESS_MARKER).exists()

    (tmp_path / GENERATION_IN_PROGRESS_MARKER).touch()
    with track_generation(tmp_path):
        seen.append(generation_in_progress(tmp_path))
    assert not (tmp_path / GENERATION_IN_PROGRESS_MARKER).exists()

    assert seen == [False, True]


def test_the_marker_survives_a_failed_generation_and_nested_calls_remove_it_once(tmp_path):
    with pytest.raises(RuntimeError), track_generation(tmp_path):
        raise RuntimeError("stopped")
    assert (tmp_path / GENERATION_IN_PROGRESS_MARKER).exists()
    (tmp_path / GENERATION_IN_PROGRESS_MARKER).unlink()

    with track_generation(tmp_path):
        with track_generation(tmp_path):
            pass
        assert (tmp_path / GENERATION_IN_PROGRESS_MARKER).exists()
    assert not (tmp_path / GENERATION_IN_PROGRESS_MARKER).exists()


def test_a_directory_that_is_not_local_is_not_marked():
    with track_generation("s3://bucket/data"):
        assert not generation_in_progress("s3://bucket/data")
