from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from benchbox.cli.orchestrator import BenchmarkOrchestrator
from benchbox.core import run_service
from benchbox.core.schemas import BenchmarkConfig
from benchbox.utils import disk_guard
from benchbox.utils.disk_guard import (
    DEFAULT_MIN_FREE_PERCENT,
    MIN_FREE_PERCENT_ENV,
    LowDiskSpaceError,
    check_free_space,
    ensure_free_space,
    largest_consumers,
    resolve_min_free_percent,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]

GIB = 1024**3
SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_free_disk.py"
SPEC = importlib.util.spec_from_file_location("check_free_disk", SCRIPT)
assert SPEC and SPEC.loader
check_free_disk = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = check_free_disk
SPEC.loader.exec_module(check_free_disk)


def _usage(free: int, total: int = 100 * GIB):
    return shutil._ntuple_diskusage(total, total - free, free)


def _populate(root: Path) -> None:
    (root / "big").mkdir()
    (root / "big" / "a.bin").write_bytes(b"x" * 5000)
    (root / "small").mkdir()
    (root / "small" / "b.bin").write_bytes(b"x" * 10)
    (root / "loose.bin").write_bytes(b"x" * 100)


def test_default_threshold_is_five_percent():
    assert resolve_min_free_percent(env={}) == DEFAULT_MIN_FREE_PERCENT == 5.0


def test_threshold_from_env_and_explicit_override():
    assert resolve_min_free_percent(env={MIN_FREE_PERCENT_ENV: "12.5"}) == 12.5
    assert resolve_min_free_percent("20", env={MIN_FREE_PERCENT_ENV: "12.5"}) == 20.0


@pytest.mark.parametrize("bad", ["abc", "-1", "100", "150"])
def test_invalid_threshold_rejected(bad):
    with pytest.raises(ValueError):
        resolve_min_free_percent(bad)


def test_above_threshold_passes(tmp_path):
    with patch.object(disk_guard.shutil, "disk_usage", return_value=_usage(6 * GIB)):
        status = ensure_free_space(tmp_path)
    assert status.ok
    assert status.free_percent == pytest.approx(6.0)


def test_exactly_at_threshold_passes(tmp_path):
    with patch.object(disk_guard.shutil, "disk_usage", return_value=_usage(5 * GIB)):
        assert check_free_space(tmp_path).ok


def test_below_threshold_raises(tmp_path):
    with patch.object(disk_guard.shutil, "disk_usage", return_value=_usage(4 * GIB)):
        with pytest.raises(LowDiskSpaceError):
            ensure_free_space(tmp_path)


def test_custom_threshold_changes_outcome(tmp_path):
    with patch.object(disk_guard.shutil, "disk_usage", return_value=_usage(4 * GIB)):
        assert ensure_free_space(tmp_path, 3).ok
        with pytest.raises(LowDiskSpaceError):
            ensure_free_space(tmp_path, 10)


def test_zero_threshold_disables_check(tmp_path):
    with patch.object(disk_guard.shutil, "disk_usage", return_value=_usage(0)):
        assert ensure_free_space(tmp_path, 0).ok


def test_missing_path_checks_nearest_existing_parent(tmp_path):
    with patch.object(disk_guard.shutil, "disk_usage", return_value=_usage(50 * GIB)) as usage:
        status = check_free_space(tmp_path / "does" / "not" / "exist")
    assert status.path == tmp_path.absolute()
    usage.assert_called_once_with(tmp_path.absolute())


def test_message_names_free_total_and_largest_consumers(tmp_path):
    _populate(tmp_path)
    with patch.object(disk_guard.shutil, "disk_usage", return_value=_usage(1 * GIB)):
        with pytest.raises(LowDiskSpaceError) as excinfo:
            ensure_free_space(tmp_path)
    message = str(excinfo.value)
    assert str(tmp_path.absolute()) in message
    assert "1.00 GB free of 100.00 GB" in message
    assert f"free={GIB} total={100 * GIB} bytes" in message
    assert "minimum 5%" in message
    assert MIN_FREE_PERCENT_ENV in message
    assert message.index("big") < message.index("loose.bin") < message.index("small")


def test_largest_consumers_sorted_and_limited(tmp_path):
    _populate(tmp_path)
    consumers = largest_consumers(tmp_path, top_n=2)
    assert [item.name for item in consumers] == ["big", "loose.bin"]
    assert consumers[0].size_bytes == 5000
    assert all(item.complete for item in consumers)


def test_largest_consumers_marks_partial_when_budget_exhausted(tmp_path):
    _populate(tmp_path)
    consumers = largest_consumers(tmp_path, budget_seconds=-1.0)
    assert any(not item.complete for item in consumers)


def test_script_exit_codes(tmp_path, capsys):
    _populate(tmp_path)
    with patch.object(disk_guard.shutil, "disk_usage", return_value=_usage(50 * GIB)):
        assert check_free_disk.main(["--path", str(tmp_path)]) == 0
    assert "Disk OK" in capsys.readouterr().out
    with patch.object(disk_guard.shutil, "disk_usage", return_value=_usage(1 * GIB)):
        assert check_free_disk.main(["--path", str(tmp_path), "--min-free-percent", "5"]) == 1
    assert "Low disk space" in capsys.readouterr().err
    assert check_free_disk.main(["--path", str(tmp_path), "--min-free-percent", "200"]) == 2


def _low_usage():
    return patch.object(disk_guard.shutil, "disk_usage", return_value=_usage(1 * GIB))


def _run_service_kwargs(output_root, adapter_factory):
    return {
        "config": BenchmarkConfig(name="tpch", display_name="TPC-H", scale_factor=0.01),
        "benchmark_instance": object(),
        "database_config": None,
        "system_profile": None,
        "platform_config": None,
        "output_root": output_root,
        "phases_to_run": None,
        "adapter_factory": adapter_factory,
        "verbosity": None,
    }


def test_execute_run_fails_before_adapter_or_writes_when_disk_low(tmp_path, monkeypatch):
    monkeypatch.setenv("BENCHBOX_OUTPUT_DIR", str(tmp_path / "runs"))
    factory = Mock()
    with _low_usage(), patch("benchbox.core.run_service.run_benchmark_lifecycle") as lifecycle:
        with pytest.raises(LowDiskSpaceError):
            run_service.execute_run(**_run_service_kwargs(str(tmp_path / "out"), factory))
    factory.assert_not_called()
    lifecycle.assert_not_called()
    assert not (tmp_path / "out").exists()


def test_execute_run_checks_local_output_root_on_its_own_volume(tmp_path, monkeypatch):
    monkeypatch.setenv("BENCHBOX_OUTPUT_DIR", str(tmp_path / "runs"))
    seen = []

    def usage(path):
        seen.append(Path(path))
        return _usage(1 * GIB) if Path(path) == tmp_path.absolute() / "out" else _usage(50 * GIB)

    (tmp_path / "out").mkdir()
    with patch.object(disk_guard.shutil, "disk_usage", side_effect=usage):
        with pytest.raises(LowDiskSpaceError):
            run_service.execute_run(**_run_service_kwargs(str(tmp_path / "out"), Mock()))
    assert tmp_path.absolute() / "out" in seen


def test_execute_run_skips_cloud_output_root(tmp_path, monkeypatch):
    monkeypatch.setenv("BENCHBOX_OUTPUT_DIR", str(tmp_path / "runs"))
    with patch.object(disk_guard.shutil, "disk_usage", return_value=_usage(50 * GIB)) as usage:
        disk_guard.ensure_run_output_space("s3://bucket/prefix")
    assert all(call.args[0] != Path("s3://bucket/prefix") for call in usage.call_args_list)


def test_orchestrator_run_below_threshold_returns_failure_without_writes(tmp_path, monkeypatch):
    monkeypatch.setenv("BENCHBOX_OUTPUT_DIR", str(tmp_path / "runs"))
    orchestrator = BenchmarkOrchestrator()
    config = BenchmarkConfig(name="tpch", display_name="TPC-H", scale_factor=0.01)
    with (
        patch.object(orchestrator, "_get_benchmark_instance", return_value=object()),
        patch.object(orchestrator, "_apply_default_cloud_output_dir"),
        _low_usage(),
        patch("benchbox.core.run_service.run_benchmark_lifecycle") as lifecycle,
    ):
        result = orchestrator.execute_benchmark(config, system_profile=None, database_config=None)
    lifecycle.assert_not_called()
    assert "Low disk space" in str(result.__dict__)
