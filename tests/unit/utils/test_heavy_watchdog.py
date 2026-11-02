from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import psutil
import pytest

from benchbox.utils import heavy_watchdog as hw, host_lock as hl, process_tree as pt

pytestmark = [pytest.mark.unit, pytest.mark.medium, pytest.mark.skipif(sys.platform == "win32", reason="POSIX only")]

MIB = 2**20
GIB = 2**30


@pytest.fixture
def spawned() -> Iterator[list[subprocess.Popen | int]]:
    procs: list[subprocess.Popen | int] = []
    yield procs
    for item in procs:
        if isinstance(item, int):
            targets = [item]
        else:
            try:
                targets = [child.pid for child in psutil.Process(item.pid).children(recursive=True)]
            except psutil.Error:
                targets = []
        for pid in targets:
            try:
                psutil.Process(pid).kill()
            except psutil.Error:
                pass
    for item in procs:
        if not isinstance(item, int):
            if item.poll() is None:
                item.kill()
            item.wait()


def wait_for(predicate: Callable[[], bool], timeout: float = 20.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def alive(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def read_pid(path: Path) -> int:
    assert wait_for(lambda: path.exists() and path.read_text(encoding="utf-8").strip() != "")
    return int(path.read_text(encoding="utf-8"))


def start_driver(
    holder: subprocess.Popen, budget: int, mode: str, spawned: list[subprocess.Popen], stderr_path: Path
) -> subprocess.Popen:
    config = {"pid": holder.pid, "created": psutil.Process(holder.pid).create_time(), "budget": budget, "mode": mode}
    with stderr_path.open("w", encoding="utf-8") as sink:
        proc = subprocess.Popen(
            [sys.executable, "-m", "tests.unit.utils.heavy_watchdog_helper", "driver", json.dumps(config)],
            stderr=sink,
            stdout=subprocess.DEVNULL,
        )
    spawned.append(proc)
    return proc


def start_nested_holder(tmp_path: Path, spawned: list[subprocess.Popen], alloc_mib: int) -> subprocess.Popen:
    holder = subprocess.Popen(
        [sys.executable, "-m", "tests.unit.utils.heavy_watchdog_helper", "nested_holder", str(tmp_path), str(alloc_mib)]
    )
    spawned.append(holder)
    assert wait_for(lambda: (tmp_path / "gc.pid").exists() and (tmp_path / "gc.pid").read_text(encoding="utf-8") != "")
    return holder


def policy(budget_gib: float = 1.0, **overrides: object) -> hw.WatchPolicy:
    base: dict[str, object] = {
        "holder_pid": os.getpid(),
        "holder_created": psutil.Process().create_time(),
        "budget_bytes": int(budget_gib * GIB),
        "mode": "warn",
        "kill_free_disk_gib": 20.0,
        "grace_seconds": 0.5,
        "disks": (("boot volume", "/"),),
    }
    base.update(overrides)
    return hw.WatchPolicy(**base)


def root_sample(total: int) -> pt.TreeSample:
    member = pt.TreeMember(os.getpid(), os.getppid(), "python", psutil.Process().create_time(), 0, total)
    return pt.TreeSample(os.getpid(), (member,), total)


def step_once(
    pol: hw.WatchPolicy, *, footprint: int = 0, pressure: int | None = 1, free_gib: float | None = 500.0
) -> str:
    out = io.StringIO()
    dog = hw.Watchdog(
        pol,
        sampler=lambda pid, exclude=(): root_sample(footprint),
        pressure_reader=lambda: pressure,
        disk_reader=lambda roots: [hw.DiskFree(label, path, free_gib) for label, path in roots],
        out=out,
    )
    assert dog.step() is True
    return out.getvalue()


def test_grandchild_over_baseline_is_terminated_within_five_seconds(tmp_path: Path, spawned) -> None:
    holder = start_nested_holder(tmp_path, spawned, alloc_mib=128)
    grandchild = int(read_pid(tmp_path / "gc.pid"))
    baseline = pt.tree_footprint_bytes(holder.pid)
    assert baseline is not None
    report = tmp_path / "watch.err"
    start_driver(holder, baseline + 64 * MIB, "kill", spawned, report)
    (tmp_path / "go").write_text("x", encoding="utf-8")
    started = time.monotonic()
    assert wait_for(lambda: not alive(grandchild), timeout=5.0), report.read_text(encoding="utf-8")
    assert time.monotonic() - started < 5.0
    text = report.read_text(encoding="utf-8")
    assert "BREACH tree footprint" in text
    assert f"pid {grandchild} " in text
    assert "GiB" in text
    assert "not tree-attributed" in text
    assert alive(holder.pid)


def test_warn_mode_reports_once_and_leaves_the_tree_alive(tmp_path: Path, spawned) -> None:
    holder = start_nested_holder(tmp_path, spawned, alloc_mib=128)
    grandchild = int(read_pid(tmp_path / "gc.pid"))
    baseline = pt.tree_footprint_bytes(holder.pid)
    assert baseline is not None
    report = tmp_path / "watch.err"
    start_driver(holder, baseline + 64 * MIB, "warn", spawned, report)
    (tmp_path / "go").write_text("x", encoding="utf-8")
    assert wait_for(lambda: "BREACH" in report.read_text(encoding="utf-8"), timeout=10.0)
    time.sleep(1.5)
    assert alive(grandchild)
    assert report.read_text(encoding="utf-8").count("BREACH tree footprint") == 1


def test_holder_over_budget_exits_with_distinct_status_after_descendants_die(tmp_path: Path, spawned) -> None:
    env = dict(os.environ)
    env.update(
        {
            "BENCHBOX_HEAVY_MEMORY_BUDGET_GIB": "0.25",
            "BENCHBOX_HEAVY_WATCHDOG_INTERVAL_SECONDS": "0.2",
            "BENCHBOX_HEAVY_WATCHDOG_GRACE_SECONDS": "2",
            "BENCHBOX_HEAVY_KILL_FREE_DISK_GIB": "0",
            "BENCHBOX_HEAVY_WATCHDOG": "kill",
        }
    )
    env.pop(hl.OWNER_ENV, None)
    holder = subprocess.Popen(
        [sys.executable, "-m", "tests.unit.utils.heavy_watchdog_helper", "guarded_holder", str(tmp_path)],
        env=env,
        stderr=subprocess.PIPE,
        text=True,
    )
    spawned.append(holder)
    kid = read_pid(tmp_path / "kid.pid")
    (tmp_path / "go").write_text("x", encoding="utf-8")
    code = holder.wait(timeout=30)
    assert code == hl.EXIT_WATCHDOG == 79, holder.stderr.read()
    assert wait_for(lambda: not alive(kid), timeout=5.0)


def test_sibling_and_double_forked_descendant_are_never_signalled(tmp_path: Path, spawned) -> None:
    sibling = subprocess.Popen(
        [sys.executable, "-m", "tests.unit.utils.heavy_watchdog_helper", "alloc", "400", str(tmp_path / "sibling.pid")]
    )
    spawned.append(sibling)
    holder = subprocess.Popen(
        [sys.executable, "-m", "tests.unit.utils.heavy_watchdog_helper", "orphan_holder", str(tmp_path)]
    )
    spawned.append(holder)
    victim = read_pid(tmp_path / "victim.pid")
    daemon = read_pid(tmp_path / "daemon.pid")
    read_pid(tmp_path / "sibling.pid")
    spawned.append(daemon)
    assert psutil.Process(daemon).ppid() != holder.pid
    orphan = pt.TreeMember(daemon, holder.pid, "python", psutil.Process(daemon).create_time(), 1, None)
    assert pt.verified_descendant(orphan, holder.pid, psutil.Process(holder.pid).create_time()) is None
    start_driver(holder, 200 * MIB, "kill", spawned, tmp_path / "watch.err")
    assert wait_for(lambda: not alive(victim), timeout=10.0)
    time.sleep(1.5)
    assert alive(daemon)
    assert alive(sibling.pid)
    assert alive(holder.pid)


def test_watcher_keeps_sampling_while_the_holder_holds_the_gil_and_exits_with_it(tmp_path: Path, spawned) -> None:
    holder = subprocess.Popen(
        [sys.executable, "-m", "tests.unit.utils.heavy_watchdog_helper", "gil_holder", str(tmp_path)]
    )
    spawned.append(holder)
    assert wait_for((tmp_path / "spinning").exists)
    victim = read_pid(tmp_path / "victim.pid")
    start_driver_proc = start_driver(holder, 200 * MIB, "kill", spawned, tmp_path / "watch.err")
    assert wait_for(lambda: not alive(victim), timeout=10.0)
    assert holder.poll() is None
    holder.kill()
    holder.wait()
    assert start_driver_proc.wait(timeout=10) == 0


def test_watcher_script_exits_when_the_holder_is_already_gone(tmp_path: Path) -> None:
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    created = psutil.Process(gone.pid).create_time()
    gone.wait()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchbox.utils.heavy_watchdog",
            "--holder-pid",
            str(gone.pid),
            "--holder-created",
            repr(created),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0


def test_normal_readings_never_breach() -> None:
    assert step_once(policy(), footprint=GIB // 4) == ""


def test_footprint_above_budget_breaches() -> None:
    text = step_once(policy(budget_gib=0.5), footprint=GIB)
    assert "BREACH tree footprint" in text
    assert "1.00 GiB" in text


def test_budget_zero_disables_the_footprint_rule() -> None:
    assert step_once(policy(budget_gib=0.0), footprint=100 * GIB) == ""


def test_critical_pressure_breaches_and_lower_levels_do_not() -> None:
    assert "BREACH memory pressure" in step_once(policy(), pressure=4)
    assert step_once(policy(), pressure=2) == ""
    assert step_once(policy(), pressure=1) == ""
    assert step_once(policy(), pressure=None) == ""


def test_low_free_disk_breaches_each_root_and_enough_disk_does_not() -> None:
    assert "BREACH free disk (boot volume /)" in step_once(policy(), free_gib=19.9)
    assert step_once(policy(), free_gib=20.0) == ""
    assert step_once(policy(), free_gib=None) == ""
    assert step_once(policy(kill_free_disk_gib=5.0), free_gib=10.0) == ""


@pytest.mark.parametrize(
    ("admit_floor", "kill_floor", "expected"),
    [(None, None, 20.0), ("50", None, 20.0), ("100", None, 20.0), ("5", None, 2.0), ("2", None, 0.8), ("5", "3", 3.0)],
)
def test_kill_disk_floor_stays_below_the_admission_floor_unless_set_explicitly(
    monkeypatch: pytest.MonkeyPatch, admit_floor: str | None, kill_floor: str | None, expected: float
) -> None:
    for name, value in (
        ("BENCHBOX_HEAVY_MIN_FREE_DISK_GIB", admit_floor),
        ("BENCHBOX_HEAVY_KILL_FREE_DISK_GIB", kill_floor),
    ):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    args = hw.build_parser().parse_args(["--holder-pid", "1", "--holder-created", "0", "--budget-bytes", "0"])
    assert hw.policy_from_args(args).kill_free_disk_gib == pytest.approx(expected)


def test_evaluate_breaches_reports_every_failing_signal() -> None:
    pol = policy(budget_gib=0.5)
    disks = [hw.DiskFree("boot volume", "/", 1.0)]
    kinds = [b.signal for b in hw.evaluate_breaches(GIB, 4, disks, pol)]
    assert kinds == ["tree footprint", "memory pressure", "free disk (boot volume /)"]


def test_warn_mode_reports_once_per_breach_episode() -> None:
    pol = policy(budget_gib=0.5, mode="warn")
    readings = [GIB, GIB, GIB // 8, GIB]
    out = io.StringIO()
    current = {"value": 0}
    dog = hw.Watchdog(
        pol,
        sampler=lambda pid, exclude=(): root_sample(current["value"]),
        pressure_reader=lambda: 1,
        disk_reader=lambda roots: [],
        out=out,
    )
    for value in readings:
        current["value"] = value
        dog.step()
    assert out.getvalue().count("BREACH tree footprint") == 2
    assert "warn only" in out.getvalue()


def test_inode_mismatch_warns_loudly_without_killing(tmp_path: Path) -> None:
    lock = tmp_path / "test.lock"
    lock.write_text("x", encoding="utf-8")
    pol = policy(lock_path=str(lock), lock_inode=lock.stat().st_ino + 1)
    text = step_once(pol, footprint=1)
    assert "WARNING" in text
    assert "Not killing anything" in text
    assert "BREACH" not in text
    assert step_once(policy(lock_path=str(lock), lock_inode=lock.stat().st_ino), footprint=1) == ""


def test_sample_tree_sums_descendants_and_honours_exclusions(tmp_path: Path, spawned) -> None:
    holder = start_nested_holder(tmp_path, spawned, alloc_mib=1)
    sample = pt.sample_tree(holder.pid)
    assert sample is not None
    pids = {m.pid for m in sample.members}
    assert holder.pid in pids
    assert len(pids) >= 3
    assert sample.total_bytes == sum(m.footprint_bytes or 0 for m in sample.members)
    depths = {m.pid: m.depth for m in sample.members}
    assert depths[holder.pid] == 0
    excluded = pt.sample_tree(holder.pid, exclude={m.pid for m in sample.members if m.depth == 1})
    assert excluded is not None
    assert {m.pid for m in excluded.members} == {holder.pid}
    assert pt.sample_tree(2**22 + 7) is None


def test_surviving_descendant_is_listed_on_release_and_left_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str], spawned
) -> None:
    monkeypatch.setenv("BENCHBOX_HEAVY_WATCHDOG", "warn")
    monkeypatch.setenv("BENCHBOX_HEAVY_MEMORY_BUDGET_GIB", "64")
    monkeypatch.setenv("BENCHBOX_HEAVY_KILL_FREE_DISK_GIB", "0")
    monkeypatch.delenv(hl.OWNER_ENV, raising=False)
    with hl.hold(tmp_path / "test.lock", phase="t", guard=True):
        survivor = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        spawned.append(survivor)
    err = capfd.readouterr().err
    assert "surviving descendant" in err
    assert str(survivor.pid) in err
    assert survivor.poll() is None


def test_off_mode_warns_loudly_and_starts_no_watcher(capfd: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    handle = hl.HostLock(tmp_path / "test.lock", None, os.getpid())
    assert hl.start_guard(handle, environ={hl.WATCHDOG_MODE_ENV: "off"}) is None
    assert "disables the watchdog" in capfd.readouterr().err


def guard_env(tmp_path: Path, budget_gib: str = "0.25") -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "BENCHBOX_TEST_LOCK_DIR": str(tmp_path),
            "BENCHBOX_HEAVY_MEMORY_BUDGET_GIB": budget_gib,
            "BENCHBOX_HEAVY_WATCHDOG_INTERVAL_SECONDS": "0.2",
            "BENCHBOX_HEAVY_KILL_FREE_DISK_GIB": "0",
            "BENCHBOX_HEAVY_WATCHDOG": "kill",
        }
    )
    env.pop(hl.OWNER_ENV, None)
    return env


def test_run_guard_subprocess_stops_a_runaway_command_with_the_watchdog_status(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchbox.utils.host_lock",
            "run",
            "--guard",
            "--",
            sys.executable,
            "-m",
            "tests.unit.utils.heavy_watchdog_helper",
            "alloc",
            "400",
            str(tmp_path / "run.pid"),
        ],
        env=guard_env(tmp_path),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == hl.EXIT_WATCHDOG == 79, result.stderr
    assert "BREACH tree footprint" in result.stderr
    assert "exiting 79" in result.stderr
    pid_file = tmp_path / "run.pid"
    if pid_file.exists():
        assert not alive(int(pid_file.read_text(encoding="utf-8")))


def test_nested_run_guard_under_an_outer_holder_is_guarded(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchbox.utils.host_lock",
            "run",
            "--",
            sys.executable,
            "-m",
            "benchbox.utils.host_lock",
            "run",
            "--guard",
            "--",
            sys.executable,
            "-m",
            "tests.unit.utils.heavy_watchdog_helper",
            "alloc",
            "400",
            str(tmp_path / "nested.pid"),
        ],
        env=guard_env(tmp_path),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == hl.EXIT_WATCHDOG, result.stderr
    assert "BREACH tree footprint" in result.stderr


def test_slow_grandchild_ignoring_sigterm_dies_after_its_parent_is_terminated(tmp_path: Path, spawned) -> None:
    holder = subprocess.Popen(
        [sys.executable, "-m", "tests.unit.utils.heavy_watchdog_helper", "stubborn_holder", str(tmp_path), "300"]
    )
    spawned.append(holder)
    stubborn = read_pid(tmp_path / "stubborn.pid")
    start_driver(holder, 200 * MIB, "kill", spawned, tmp_path / "watch.err")
    assert wait_for(lambda: not alive(stubborn), timeout=15.0), (tmp_path / "watch.err").read_text(encoding="utf-8")
    assert alive(holder.pid)


def test_unreadable_footprints_are_named_in_the_breach_report() -> None:
    member = pt.TreeMember(os.getpid(), os.getppid(), "python", psutil.Process().create_time(), 0, None)
    out = io.StringIO()
    dog = hw.Watchdog(
        policy(budget_gib=0.0, kill_free_disk_gib=20.0),
        sampler=lambda pid, exclude=(): pt.TreeSample(os.getpid(), (member,), 0),
        pressure_reader=lambda: 4,
        disk_reader=lambda roots: [],
        out=out,
    )
    dog.step()
    assert "footprint unreadable" in out.getvalue()
