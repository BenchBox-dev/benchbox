from __future__ import annotations

import dataclasses
import json
import stat
import sys
import types
from pathlib import Path

import pytest

from benchbox.utils import host_lock as hl, resource_limits as rl

pytestmark = [pytest.mark.unit, pytest.mark.medium]

POLICY = rl.AdmissionPolicy(need_gib=2.0, min_level=35.0, min_free_disk_gib=50.0)
DISK_OK = (rl.DiskReading("boot volume", "/", 200.0), rl.DiskReading("output root", "out", 200.0))


def healthy(**overrides: object) -> rl.HostPressure:
    base = rl.HostPressure(
        total_gib=16.0,
        available_gib=6.0,
        swap_used_percent=80.0,
        pressure_level=1,
        memorystatus_level=55,
        disks=DISK_OK,
        vms=(),
    )
    return dataclasses.replace(base, **overrides)


@pytest.mark.parametrize(
    ("overrides", "refused"),
    [
        ({}, False),
        ({"pressure_level": 1}, False),
        ({"pressure_level": 2}, True),
        ({"pressure_level": 4}, True),
        ({"memorystatus_level": 34}, True),
        ({"memorystatus_level": 35}, False),
        ({"available_gib": 1.99}, True),
        ({"available_gib": 2.0}, False),
        ({"disks": (rl.DiskReading("boot volume", "/", 49.9), DISK_OK[1])}, True),
        ({"disks": (DISK_OK[0], rl.DiskReading("output root", "out", 49.9))}, True),
        ({"disks": (rl.DiskReading("boot volume", "/", 50.0), rl.DiskReading("output root", "out", 50.0))}, False),
        ({"swap_used_percent": 99.0}, False),
    ],
)
def test_each_signal_admits_or_refuses_at_its_threshold(overrides: dict[str, object], refused: bool) -> None:
    assert bool(rl.evaluate_admission(healthy(**overrides), POLICY)) is refused


def test_unmeasured_signals_never_refuse() -> None:
    blind = healthy(
        total_gib=None,
        available_gib=None,
        swap_used_percent=None,
        pressure_level=None,
        memorystatus_level=None,
        disks=(rl.DiskReading("boot volume", "/", None),),
        vms=None,
    )
    assert rl.evaluate_admission(blind, POLICY) == ()
    assert "unmeasured" in rl.format_admission_report(rl.AdmissionResult(True, (), blind), POLICY)


def test_vm_reservations_are_itemized_and_do_not_change_the_decision() -> None:
    vms = (
        rl.VmReservation("machine", "benchbox-agent", "running", 8.0),
        rl.VmReservation("container", "pg", "running", 1.0),
        rl.VmReservation("container", "mystery", None, None),
    )
    plain = healthy()
    loaded = healthy(vms=vms)
    assert rl.evaluate_admission(loaded, POLICY) == rl.evaluate_admission(plain, POLICY) == ()
    report = rl.format_admission_report(rl.AdmissionResult(True, (), loaded), POLICY)
    for fragment in ("machine benchbox-agent [running]: 8.00 GiB", "container pg [running]: 1.00 GiB", "mystery"):
        assert fragment in report
    assert "not subtracted" in report
    refusing = healthy(vms=vms, available_gib=1.0)
    assert rl.evaluate_admission(refusing, POLICY) == rl.evaluate_admission(healthy(available_gib=1.0), POLICY)


CONTAINER_JSON = json.dumps(
    [
        {
            "id": "ss-dev",
            "configuration": {"id": "ss-dev", "resources": {"cpus": 4, "memoryInBytes": 6442450944}},
            "status": {"state": "running"},
        },
        {"id": "odd", "configuration": {"resources": {}}, "status": {}},
    ]
)
MACHINE_JSON = json.dumps([{"id": "benchbox-agent", "status": "running", "memory": 8589934592, "cpus": 4}])


def test_vm_inventory_reads_the_observed_container_and_machine_schemas() -> None:
    outputs = {"list": CONTAINER_JSON, "machine": MACHINE_JSON}

    def runner(argv, timeout):
        return outputs["machine" if "machine" in argv else "list"]

    found = rl.read_vm_reservations(runner, which=lambda name: "/usr/bin/container")
    assert found == (
        rl.VmReservation("container", "ss-dev", "running", 6.0),
        rl.VmReservation("container", "odd", None, None),
        rl.VmReservation("machine", "benchbox-agent", "running", 8.0),
    )


@pytest.mark.parametrize("bad", [None, "not json", "{}"])
def test_a_failed_or_malformed_vm_listing_is_unmeasured(bad: str | None) -> None:
    assert rl.read_vm_reservations(lambda argv, timeout: bad, which=lambda name: "/usr/bin/container") is None


def test_no_container_cli_means_no_vms() -> None:
    assert rl.read_vm_reservations(lambda argv, timeout: None, which=lambda name: None) == ()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell stub")
def test_a_hung_container_cli_degrades_to_unmeasured_within_six_seconds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = tmp_path / "container"
    stub.write_text("#!/bin/sh\nexec sleep 60\n")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin:/bin")
    started = rl.mono_time()
    result = rl.admit_heavy_work(
        POLICY,
        reader=lambda: dataclasses.replace(
            rl.read_host_pressure(roots=[]),
            available_gib=8.0,
            pressure_level=1,
            memorystatus_level=60,
        ),
    )
    assert rl.elapsed_seconds(started) < 6.0
    assert result.pressure.vms is None
    assert result.admitted and result.refusals == ()


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def mono_time(self) -> float:
        return self.now

    def elapsed_seconds(self, start: float, end: float | None = None) -> float:
        return (self.now if end is None else end) - start

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    fake = FakeClock()
    monkeypatch.setattr(rl, "mono_time", fake.mono_time)
    monkeypatch.setattr(rl, "elapsed_seconds", fake.elapsed_seconds)
    monkeypatch.setattr(rl, "_sleep", fake.sleep)
    return fake


def test_a_reading_that_recovers_inside_the_window_admits(clock: FakeClock) -> None:
    readings = iter([healthy(pressure_level=2), healthy(pressure_level=2), healthy()])
    result = rl.admit_heavy_work(POLICY, reader=lambda: next(readings))
    assert result.admitted
    assert result.samples == 3
    assert clock.sleeps == [5.0, 5.0]


def test_a_reading_that_stays_bad_refuses_after_the_retry_window(clock: FakeClock) -> None:
    result = rl.admit_heavy_work(POLICY, reader=lambda: healthy(available_gib=0.5))
    assert not result.admitted
    assert result.refusals
    assert sum(clock.sleeps) == pytest.approx(30.0)
    assert result.samples == 7


def test_force_admits_after_one_sample_without_waiting(clock: FakeClock) -> None:
    result = rl.admit_heavy_work(POLICY, reader=lambda: healthy(pressure_level=4), force=True)
    assert result.admitted and result.forced and result.refusals
    assert result.samples == 1
    assert clock.sleeps == []


@pytest.fixture
def lock_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(hl.LOCK_DIR_ENV, str(tmp_path / "lock"))
    monkeypatch.setenv(hl.WAIT_ENV, "0")
    monkeypatch.delenv(hl.OWNER_ENV, raising=False)
    monkeypatch.delenv(hl.ADMIT_ENV, raising=False)
    return tmp_path / "lock" / hl.LOCK_NAME


def test_a_refused_acquirer_exits_non_zero_and_frees_the_lock(
    lock_file: Path,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(rl, "read_host_pressure", lambda: healthy(pressure_level=2, available_gib=0.4))
    marker = tmp_path / "ran"
    code = hl.run_command([sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"], admit=True, admit_need_gib=2.0)
    assert code == hl.EXIT_ADMISSION
    assert code not in (hl.EXIT_REFUSED, hl.EXIT_OWNERSHIP)
    assert not marker.exists()
    assert not hl.lock_is_held(lock_file)
    err = capsys.readouterr().err
    assert "memory pressure level 2" in err
    assert "available memory 0.40 GiB" in err


def test_acquire_raises_and_releases_when_admission_is_refused(
    lock_file: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rl, "read_host_pressure", lambda: healthy(memorystatus_level=10))
    with pytest.raises(hl.AdmissionRefused) as excinfo:
        hl.acquire(lock_file, phase="test", admit=True)
    assert "memorystatus_level 10" in excinfo.value.report
    assert not hl.lock_is_held(lock_file)


def test_an_admitted_acquirer_keeps_the_lock(lock_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rl, "read_host_pressure", healthy)
    handle = hl.acquire(lock_file, phase="test", admit=True)
    try:
        assert hl.lock_is_held(lock_file)
    finally:
        handle.release()
    assert not hl.lock_is_held(lock_file)


def test_force_admits_with_a_loud_warning(
    lock_file: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(rl, "read_host_pressure", lambda: healthy(pressure_level=4))
    monkeypatch.setenv(hl.ADMIT_ENV, "force")
    handle = hl.acquire(lock_file, phase="test", admit=True)
    handle.release()
    err = capsys.readouterr().err
    assert "WARNING" in err
    assert "BENCHBOX_HEAVY_ADMIT=force" in err
    assert "memory pressure level 4" in err


def test_admit_command_reports_on_stdout_when_admitted_and_on_stderr_when_refused(
    clock: FakeClock, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(hl.ADMIT_ENV, raising=False)
    monkeypatch.setattr(rl, "read_host_pressure", healthy)
    assert hl.main(["admit", "--need-gib", "1"]) == 0
    captured = capsys.readouterr()
    assert "ADMITTED" in captured.out
    monkeypatch.setattr(rl, "read_host_pressure", lambda: healthy(available_gib=0.1))
    assert hl.main(["admit", "--need-gib", "1"]) == hl.EXIT_ADMISSION
    captured = capsys.readouterr()
    assert "REFUSED" in captured.err
    assert captured.out == ""


def test_run_admit_end_to_end_refuses_with_exit_76_and_reports_the_need(
    lock_file: Path,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(rl, "read_host_pressure", lambda: healthy(available_gib=1.0))
    marker = tmp_path / "ran"
    code = hl.main(
        ["run", "--admit", "--need-gib", "7.5", "--", sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"]
    )
    assert code == 76
    assert not marker.exists()
    assert not hl.lock_is_held(lock_file)
    assert "need 7.5 GiB" in capsys.readouterr().err


def test_forced_admit_prints_the_report_once(
    clock: FakeClock, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(hl.ADMIT_ENV, "force")
    monkeypatch.setattr(rl, "read_host_pressure", lambda: healthy(pressure_level=4))
    assert hl.main(["admit"]) == 0
    captured = capsys.readouterr()
    assert (captured.out + captured.err).count("heavy-work admission:") == 1
    assert "WARNING" in captured.err


def test_settings_default_and_override_from_the_environment() -> None:
    defaults = rl.admission_policy_from_env({})
    assert defaults == rl.AdmissionPolicy(rl.DEFAULT_ADMIT_MIN_GIB, 35.0, 50.0)
    tuned = rl.admission_policy_from_env(
        {rl.ADMIT_MIN_GIB_ENV: "3.5", rl.MIN_LEVEL_ENV: "40", rl.MIN_FREE_DISK_GIB_ENV: "10"}
    )
    assert tuned == rl.AdmissionPolicy(3.5, 40.0, 10.0)
    garbage = rl.admission_policy_from_env(
        {rl.ADMIT_MIN_GIB_ENV: "lots", rl.MIN_LEVEL_ENV: "-1", rl.MIN_FREE_DISK_GIB_ENV: "nan"}
    )
    assert garbage == defaults
    assert rl.admission_policy_from_env({rl.ADMIT_MIN_GIB_ENV: "3"}, need_gib=1.0).need_gib == 1.0


def test_memory_budget_resolver_honors_the_override_and_defaults_to_half_of_ram(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert rl.heavy_memory_budget_gib({rl.MEMORY_BUDGET_GIB_ENV: "7.5"}) == 7.5
    monkeypatch.setattr(rl, "get_system_memory_mb", lambda: 16 * 1024.0)
    assert rl.heavy_memory_budget_gib({}) == 8.0
    assert rl.heavy_memory_budget_gib({rl.MEMORY_BUDGET_GIB_ENV: "0"}) == 8.0
    monkeypatch.setattr(rl, "get_system_memory_mb", lambda: 0.0)
    assert rl.heavy_memory_budget_gib({}) is None


def test_non_macos_reads_no_memorystatus_signals() -> None:
    calls: list[list[str]] = []

    def runner(argv, timeout):
        calls.append(list(argv))
        return None

    pressure = rl.read_host_pressure(roots=[], runner=runner, which=lambda name: None, platform="linux")
    assert pressure.pressure_level is None and pressure.memorystatus_level is None
    assert calls == []


def test_macos_reads_pressure_level_and_memorystatus_level() -> None:
    values = {"kern.memorystatus_vm_pressure_level": "2\n", "kern.memorystatus_level": "41\n"}

    def runner(argv, timeout):
        return values.get(argv[-1])

    pressure = rl.read_host_pressure(roots=[], runner=runner, which=lambda name: None, platform="darwin")
    assert (pressure.pressure_level, pressure.memorystatus_level) == (2, 41)


def test_free_space_converts_bytes_to_gib_for_the_nearest_existing_ancestor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[Path] = []

    def fake_usage(path: Path) -> types.SimpleNamespace:
        seen.append(path)
        return types.SimpleNamespace(free=3 * 2**30 + 2**29)

    monkeypatch.setattr(rl.shutil, "disk_usage", fake_usage)
    assert rl.free_space_gib(tmp_path / "missing" / "deeper") == 3.5
    assert seen == [tmp_path]


def test_read_memory_snapshot_converts_psutil_readings(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = types.SimpleNamespace(
        virtual_memory=lambda: types.SimpleNamespace(available=5 * 2**30 + 2**29),
        swap_memory=lambda: types.SimpleNamespace(percent=42.5),
    )
    monkeypatch.setattr(rl, "psutil", fake)
    assert rl.read_memory_snapshot() == rl.MemorySnapshot(free_gib=5.5, swap_used_percent=42.5)


def test_read_memory_snapshot_degrades_safely_when_psutil_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rl, "psutil", None)
    assert rl.read_memory_snapshot() == rl.MemorySnapshot(free_gib=None, swap_used_percent=None)


def test_roots_on_one_filesystem_produce_a_single_refusal() -> None:
    shared = (rl.DiskReading("boot volume", "/", 10.0, device=7), rl.DiskReading("output root", "out", 10.0, device=7))
    refusals = rl.evaluate_admission(healthy(disks=shared), POLICY)
    assert len(refusals) == 1
    assert "boot volume" in refusals[0] and "output root" in refusals[0]
    separate = (
        rl.DiskReading("boot volume", "/", 10.0, device=7),
        rl.DiskReading("output root", "out", 10.0, device=8),
    )
    assert len(rl.evaluate_admission(healthy(disks=separate), POLICY)) == 2


def test_roots_in_the_same_directory_share_a_device(tmp_path: Path) -> None:
    first = rl._read_disk("a", str(tmp_path))
    second = rl._read_disk("b", str(tmp_path / "missing"))
    assert first.device is not None and first.device == second.device


def test_macos_reads_sysctl_by_absolute_path() -> None:
    seen: list[str] = []

    def runner(argv, timeout):
        seen.append(argv[0])
        return "1\n"

    rl.read_host_pressure(roots=[], runner=runner, which=lambda name: None, platform="darwin")
    assert seen == ["/usr/sbin/sysctl", "/usr/sbin/sysctl"]
