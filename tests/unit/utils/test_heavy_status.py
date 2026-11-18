from __future__ import annotations

import dataclasses
import os
import stat
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchbox.utils import git_flood, heavy_status as hs, host_lock as hl, resource_limits as rl

pytestmark = [pytest.mark.unit, pytest.mark.medium]

SENT = hs.SentinelState(False, None, Path("x/sentinel.pid"), False)
GIB = rl.GIB


@pytest.fixture(autouse=True)
def isolated_sentinel_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(hs.SENTINEL_LOG_ENV, str(tmp_path / "sentinel-state" / "sentinel.log"))
    monkeypatch.setenv(hs.SENTINEL_PID_ENV, str(tmp_path / "sentinel-state" / "sentinel.pid"))
    monkeypatch.setenv(hs.AGENTS_DIR_ENV, str(tmp_path / "agents"))


PS_TABLE = "\n".join(
    [
        "    1     0 /sbin/launchd",
        "61182     1 /usr/bin/java -Xmx2g -jar /opt/questdb/questdb.jar -d /tmp/qdb",
        "78738     1 /usr/bin/java -jar /opt/questdb/questdb.jar",
        "  500     1 /usr/bin/java -jar /opt/elsewhere/other.jar",
        "  600   400 /usr/bin/java -jar /opt/questdb/questdb.jar",
        "  700     1 /Applications/Safari.app/Contents/MacOS/Safari",
        "garbage line",
    ]
)
LSOF_CWD = "\n".join(
    [
        "p61182",
        "n/Users/example/Developer/BenchBox.wt-feature",
        "p78738",
        "n/Users/example/qdb-data",
        "p500",
        "n/Users/example/Developer/BenchBox",
    ]
)


def fake_runner(argv, timeout):
    if argv[0] == "ps":
        return PS_TABLE
    if argv[0] == "lsof":
        return LSOF_CWD
    if argv[0] == "sysctl":
        return "1\n" if "pressure" in argv[-1] else "55\n"
    if argv[0] == "container":
        return "[]"
    return None


def pressure(**overrides: object) -> rl.HostPressure:
    base = rl.HostPressure(
        total_gib=16.0,
        available_gib=6.0,
        swap_used_percent=10.0,
        pressure_level=1,
        memorystatus_level=55,
        disks=(rl.DiskReading("boot volume", "/", 200.0),),
        vms=(
            rl.VmReservation("machine", "benchbox-agent", "running", 8.0),
            rl.VmReservation("container", "ss-dev", "running", 6.0),
        ),
    )
    return dataclasses.replace(base, **overrides)


def test_orphans_are_ppid_one_servers_with_a_benchbox_worktree_cwd(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    found = hs.find_orphans(fake_runner, footprint=lambda pid: 2 * GIB if pid == 61182 else None)
    assert found is not None
    assert [(o.pid, o.cwd, o.footprint_bytes) for o in found] == [
        (61182, "/Users/example/Developer/BenchBox.wt-feature", 2 * GIB),
        (500, "/Users/example/Developer/BenchBox", None),
    ]


def test_orphans_are_unmeasured_when_the_process_table_is_unreadable() -> None:
    assert hs.find_orphans(lambda argv, timeout: None) is None


def test_orphans_are_unmeasured_when_cwd_lookup_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")

    def runner(argv, timeout):
        return PS_TABLE if argv[0] == "ps" else None

    assert hs.find_orphans(runner) is None


def test_an_unreadable_pid_does_not_hide_the_other_orphans(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")

    def runner(argv, timeout):
        if argv[0] == "ps":
            return PS_TABLE
        if argv[0] == "lsof":
            pid = argv[-1]
            return None if pid == "61182" else f"p{pid}\nn/Users/example/Developer/BenchBox\n"
        return None

    found = hs.find_orphans(runner)
    assert found is not None
    assert [o.pid for o in found] == [78738, 500]


def test_no_candidates_skips_the_cwd_lookup() -> None:
    calls: list[str] = []

    def runner(argv, timeout):
        calls.append(argv[0])
        return "  10     1 /sbin/launchd\n"

    assert hs.find_orphans(runner) == ()
    assert calls == ["ps"]


def collect(**overrides: object) -> hs.StatusReading:
    options: dict[str, object] = {
        "runner": fake_runner,
        "which": lambda name: None,
        "footprint": lambda pid: None,
        "top_reader": lambda: ((4242, "python", 3 * GIB),),
        "pressure_reader": pressure,
        "git_flood_reader": lambda: None,
    }
    options.update(overrides)
    return hs.collect(**options)


@pytest.fixture
def lock_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(hl.LOCK_DIR_ENV, str(tmp_path))
    return tmp_path


def test_status_renders_holder_waiters_vms_top_and_orphan(lock_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    handle = hl.acquire(lock_dir / hl.LOCK_NAME, wait_seconds=0, phase="run", command="make heavy-run")
    marker = hl.waiters_dir(handle.path) / str(os.getpid())
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(hl.process_identity(), encoding="utf-8")
    try:
        reading = collect(footprint=lambda pid: GIB)
    finally:
        marker.unlink()
        handle.release()
    text = hs.render(reading)
    assert "holder: HELD" in text
    assert "make heavy-run" in text
    assert "live waiters: 1" in text
    assert "machine benchbox-agent [running]: 8.00 GiB" in text
    assert "container ss-dev [running]: 6.00 GiB" in text
    assert "4242" in text and "3.00 GiB" in text and "python" in text
    assert "ORPHAN pid 61182" in text
    assert "BenchBox.wt-feature" in text
    assert "ORPHAN pid 78738" not in text


def test_status_reports_free_when_no_lock_file_exists(lock_dir: Path) -> None:
    reading = collect()
    text = hs.render(reading)
    assert "holder: free" in text
    assert "live waiters: 0" in text
    assert not (lock_dir / hl.LOCK_NAME).exists()


def test_a_record_from_an_exited_process_is_shown_as_free(lock_dir: Path) -> None:
    path = lock_dir / hl.LOCK_NAME
    path.write_text("pid:999999 owner:999999:1.00 started:2026-01-01 00:00:00 phase:run cmd:old\n", encoding="utf-8")
    text = hs.render(collect())
    assert "holder: free (last record belongs to a process that has exited)" in text
    assert "stale:" in text


def test_status_never_blocks_on_a_held_lock_and_leaves_it_untouched(lock_dir: Path) -> None:
    path = lock_dir / hl.LOCK_NAME
    handle = hl.acquire(path, wait_seconds=0, phase="run")
    before = path.read_bytes()
    try:
        started = rl.mono_time()
        collect()
        assert rl.elapsed_seconds(started) < 5.0
        assert path.read_bytes() == before
        assert hl.lock_is_held(path)
    finally:
        handle.release()


def test_an_unreadable_lock_file_is_unmeasured_not_free() -> None:
    holder = hs.HolderReading(hs.UNREADABLE_RECORD, None, False, None)
    reading = hs.StatusReading(Path("x/test.lock"), holder, 0, None, None, None, None, SENT)
    assert "holder: unmeasured" in hs.render(reading)


def test_render_uses_the_injected_environment_for_policy() -> None:
    reading = hs.StatusReading(Path("x/test.lock"), None, 0, pressure(), None, (), (), SENT)
    assert "(need 2 GiB)" in hs.render(reading, {})
    assert "(need 9 GiB)" in hs.render(reading, {rl.ADMIT_MIN_GIB_ENV: "9"})


def test_unmeasured_sections_render_explicitly() -> None:
    reading = hs.StatusReading(Path("x/test.lock"), None, 0, None, None, None, None, SENT)
    text = hs.render(reading)
    assert "host readings: unmeasured" in text
    assert text.count("unmeasured") == 5


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell stub")
def test_a_hung_container_cli_is_cut_off_and_shown_as_unmeasured(
    lock_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stubs = tmp_path / "bin"
    stubs.mkdir()
    stub = stubs / "container"
    stub.write_text("#!/bin/sh\nexec sleep 60\n")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{stubs}:/usr/bin:/bin:/usr/sbin:/sbin")
    started = rl.mono_time()
    reading = hs.collect(git_flood_reader=lambda: None)
    elapsed = rl.elapsed_seconds(started)
    assert elapsed < 7.0
    assert reading.pressure is not None
    assert reading.pressure.vms is None
    assert "VM reservations: unmeasured" in hs.render(reading)


def test_render_shows_a_git_flood_warning_with_the_top_processes() -> None:
    procs = [git_flood.GitProc(100 + n, "/work/BenchBox.wt-a", "git status") for n in range(3)]
    flood = git_flood.evaluate(
        procs, {git_flood.COUNT_ENV: "2"}, total_ram=16 * GIB, footprint=lambda pid: (pid - 99) * GIB // 4
    )
    reading = collect(git_flood_reader=lambda: flood)
    text = hs.render(reading)
    assert "WARNING git flood: 3 git processes >= 2" in text
    assert "pid 102 0.75 GiB cwd /work/BenchBox.wt-a cmd git status" in text
    assert "unmeasured" in hs.render(collect(git_flood_reader=lambda: None))


def test_top_cpu_lists_the_busiest_processes_above_the_floor() -> None:
    listing = "\n".join(
        ["  10   0.4 /bin/idle", "  11  99.5 bash /tmp/spin.sh", "  12  42.0 /usr/bin/python3 job.py", "bad line"]
    )
    assert hs.parse_top_cpu(listing) == ((11, 99.5, "bash /tmp/spin.sh"), (12, 42.0, "/usr/bin/python3 job.py"))


def test_top_cpu_is_capped_at_the_display_count() -> None:
    listing = "\n".join(f"{n} {10 + n}.0 cmd{n}" for n in range(1, 12))
    assert len(hs.parse_top_cpu(listing)) == hs.TOP_CPU_COUNT


def test_a_shell_orphaned_to_init_is_an_orphan_candidate() -> None:
    table = "\n".join(
        ["500     1 bash /tmp/hl-smoke.sh", "501   500 bash /tmp/hl-smoke.sh", "502     1 /usr/bin/vim notes"]
    )
    assert hs._parse_ps(table) == [(500, "bash /tmp/hl-smoke.sh")]


ADMITTED = SimpleNamespace(admitted=True, refusals=(), forced=False)
SPIN_CWD = "/work/BenchBox.wt-spin"


def spin_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, alive: bool = True, sentinel_running: bool = True
) -> Callable[[list[str]], Iterator[object]]:
    from benchbox.utils import orphan_spin

    spin = orphan_spin.ActiveSpin(
        pid=4242,
        created=4242.0,
        name="bash",
        cwd=SPIN_CWD,
        command="bash -c while true; do :; done",
        cpu_fraction=0.97,
        busy_since=1000.0,
    )
    orphan_spin.write_active(hs.orphan_spin_path(), [spin])
    if sentinel_running:
        pid_path = hs.sentinel_pid_path()
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.write_text(hl.process_identity(), encoding="utf-8")

    class Proc:
        info = {
            "pid": 4242,
            "ppid": 1,
            "name": "bash",
            "create_time": 4242.0,
            "uids": SimpleNamespace(real=getattr(os, "getuid", lambda: 0)()),
        }

        def as_dict(self, attrs, ad_value=None):
            return {
                "cwd": SPIN_CWD,
                "cmdline": ["bash", "-c", "while true; do :; done"],
                "cpu_times": SimpleNamespace(user=1.0, system=0.0),
            }

    return lambda attrs: iter([Proc()] if alive else [])


def test_status_prints_an_active_orphan_report_with_pid_cwd_cpu_share_and_age(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    table = spin_fixture(tmp_path, monkeypatch)
    text = hs.render(collect(spin_reader=lambda: hs.read_active_spins(processes=table)), now=1600.0)
    assert "ORPHAN SPIN pid 4242" in text
    assert "97% CPU" in text
    assert "busy 600s" in text
    assert f"cwd {SPIN_CWD}" in text
    assert "never signalled" in text


def test_status_is_unchanged_without_an_active_orphan_report(tmp_path: Path) -> None:
    assert "ORPHAN SPIN" not in hs.render(collect())
    assert hs.active_spin_lines() == []


def test_a_report_for_an_exited_shell_is_not_printed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    table = spin_fixture(tmp_path, monkeypatch, alive=False)
    assert "ORPHAN SPIN" not in hs.render(collect(spin_reader=lambda: hs.read_active_spins(processes=table)))


def test_a_report_is_not_printed_when_the_sentinel_that_wrote_it_is_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    table = spin_fixture(tmp_path, monkeypatch, sentinel_running=False)
    assert hs.read_active_spins(processes=table) == ()


def test_admission_prints_the_report_and_still_admits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from benchbox.utils import resource_limits

    table = spin_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(resource_limits, "admit_heavy_work", lambda policy, force=False: ADMITTED)
    monkeypatch.setattr(resource_limits, "format_admission_report", lambda result, policy: "admitted")

    def spin_lines(environ):
        return hs._spin_block(hs.read_active_spins(environ, processes=table), 1600.0)

    assert hl.check_admission(environ=dict(os.environ), spin_lines=spin_lines) == "admitted"
    assert "ORPHAN SPIN pid 4242" in capsys.readouterr().err


def test_admission_prints_nothing_extra_without_a_report(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from benchbox.utils import resource_limits

    monkeypatch.setattr(resource_limits, "admit_heavy_work", lambda policy, force=False: ADMITTED)
    monkeypatch.setattr(resource_limits, "format_admission_report", lambda result, policy: "admitted")
    hl.check_admission(environ=dict(os.environ))
    assert "ORPHAN SPIN" not in capsys.readouterr().err
