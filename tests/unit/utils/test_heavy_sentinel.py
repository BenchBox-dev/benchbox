from __future__ import annotations

import io
import os
import plistlib
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import psutil
import pytest

from benchbox.utils import git_flood, heavy_sentinel as hs, heavy_status, host_lock as hl, sentinel_launchd

pytestmark = [pytest.mark.unit, pytest.mark.medium, pytest.mark.skipif(sys.platform == "win32", reason="POSIX only")]

GIB = 2**30
MIB = 2**20
PYTHON = "/usr/bin/python3"


def proc(
    pid: int,
    name: str = "python3.12",
    cwd: str | None = None,
    exe: str | None = None,
    cmdline: tuple[str, ...] = (),
) -> hs.ProcInfo:
    return hs.ProcInfo(pid, 1, name, exe, cmdline or (name,), cwd, float(pid))


def make_sentinel(
    table: list[hs.ProcInfo],
    sizes: dict[int, int],
    *,
    roots: tuple[Path, ...] = (Path("/work/BenchBox.wt-a"),),
    pressure: int = 0,
    held: frozenset[int] = frozenset(),
    mode: str = "warn",
    budget: int | None = 4 * GIB,
    git: list[git_flood.GitProc] | None = None,
    environ: dict[str, str] | None = None,
) -> tuple[hs.Sentinel, list[str]]:
    lines: list[str] = []
    sentinel = hs.Sentinel(
        hs.SentinelPolicy(mode=mode, budget_bytes=budget, grace_seconds=0.0),
        log=lines.append,
        process_reader=lambda: table,
        footprint=sizes.get,
        worktree_lister=lambda: roots,
        pressure_reader=lambda: pressure,
        holder_reader=lambda: held,
        sleeper=lambda seconds: None,
        git_reader=lambda roots: git or [],
        total_ram=lambda: 16 * GIB,
        environ=environ or {},
        exclude=(),
    )
    return sentinel, lines


def offender_of(
    sentinel: hs.Sentinel,
    table: list[hs.ProcInfo],
    sizes: dict[int, int],
    held: frozenset[int] = frozenset(),
    pressure: int = 0,
) -> int | None:
    sentinel._refresh_roots()
    measured, _ = sentinel._measure(table, held)
    flagged = [hs.Measured(m.proc, m.footprint_bytes, m.scope, m.proc.pid in held) for m in measured]
    result = hs.evaluate(flagged, pressure, sentinel.policy.budget_bytes)
    return None if result.offender is None else result.offender.proc.pid


def test_over_budget_process_with_cwd_in_a_worktree_is_selected() -> None:
    table = [proc(10, cwd="/work/BenchBox.wt-a/sub"), proc(11, cwd="/work/BenchBox.wt-a")]
    sizes = {10: 5 * GIB, 11: 1 * GIB}
    sentinel, _ = make_sentinel(table, sizes)
    assert offender_of(sentinel, table, sizes) == 10


def test_over_budget_process_outside_any_checkout_is_never_selected() -> None:
    table = [proc(10, cwd="/elsewhere"), proc(11, cwd="/work/BenchBox.wt-ab"), proc(12, cwd="/work/BenchBox.wt-a")]
    sizes = {10: 9 * GIB, 11: 9 * GIB, 12: GIB}
    sentinel, lines = make_sentinel(table, sizes)
    assert offender_of(sentinel, table, sizes) is None
    sentinel.step()
    assert not any("BREACH" in line for line in lines)


def test_a_live_holders_tree_is_skipped_unless_pressure_is_critical() -> None:
    table = [proc(10, cwd="/work/BenchBox.wt-a"), proc(11, cwd="/work/BenchBox.wt-a")]
    sizes = {10: 6 * GIB, 11: 5 * GIB}
    sentinel, _ = make_sentinel(table, sizes, held=frozenset({10}))
    assert offender_of(sentinel, table, sizes, held=frozenset({10})) == 11
    assert offender_of(sentinel, table, sizes, held=frozenset({10}), pressure=3) == 11
    assert offender_of(sentinel, table, sizes, held=frozenset({10}), pressure=4) == 10


def test_a_held_process_alone_over_budget_is_left_to_its_watchdog() -> None:
    table = [proc(10, cwd="/work/BenchBox.wt-a")]
    sizes = {10: 6 * GIB}
    sentinel, lines = make_sentinel(table, sizes, held=frozenset({10}))
    sentinel.step()
    assert not any("BREACH" in line for line in lines)
    assert any("[lock holder tree]" in line for line in lines)


def test_combined_footprint_breaches_only_under_elevated_pressure() -> None:
    table = [proc(10, cwd="/work/BenchBox.wt-a"), proc(11, cwd="/work/BenchBox.wt-a")]
    sizes = {10: 3 * GIB, 11: 3 * GIB}
    calm, _ = make_sentinel(table, sizes, pressure=1)
    assert offender_of(calm, table, sizes, pressure=1) is None
    warned, lines = make_sentinel(table, sizes, pressure=2)
    warned.step()
    assert any("memory pressure with combined footprint" in line for line in lines)


@pytest.mark.parametrize(
    "name", ["node", "bun", "ty", "ruff", "tern", "pyright-langserver", "bash", "zsh", "git", "Code", "nvim"]
)
def test_agent_harnesses_editors_servers_shells_and_git_are_never_candidates(name: str) -> None:
    table = [proc(10, name=name, cwd="/work/BenchBox.wt-a", exe=f"/work/BenchBox.wt-a/.venv/bin/{name}")]
    sizes = {10: 20 * GIB}
    sentinel, lines = make_sentinel(table, sizes)
    assert offender_of(sentinel, table, sizes) is None
    sentinel.step()
    assert not any("BREACH" in line for line in lines)


def test_a_python_language_server_is_never_a_candidate() -> None:
    server = proc(10, cwd="/work/BenchBox.wt-a", cmdline=("python3.12", "/usr/lib/pylsp"))
    assert not hs.is_candidate(server)
    assert hs.is_candidate(proc(11, cwd="/work/BenchBox.wt-a", cmdline=("python3.12", "-m", "pytest")))


def test_java_and_database_binaries_are_candidates() -> None:
    assert hs.is_candidate(proc(1, name="java"))
    assert hs.is_candidate(proc(2, name="clickhouse-server"))
    assert not hs.is_candidate(proc(3, name="Slack"))


def test_unreadable_cwd_falls_back_to_executable_and_command_line_paths() -> None:
    roots = hs._root_variants([Path("/work/BenchBox.wt-a")])
    by_exe = proc(1, cwd=None, exe="/work/BenchBox.wt-a/.venv/bin/python3.12")
    by_cmd = proc(2, cwd=None, exe=PYTHON, cmdline=("python3", "/work/BenchBox.wt-a/run.py"))
    neither = proc(3, cwd=None, exe=PYTHON, cmdline=("python3", "-c", "pass"))
    assert hs.locate(by_exe, roots) == "exe"
    assert hs.locate(by_cmd, roots) == "cmdline"
    assert hs.locate(neither, roots) is None


def test_warn_mode_reports_once_per_offender_and_never_signals(monkeypatch: pytest.MonkeyPatch) -> None:
    table = [proc(10, cwd="/work/BenchBox.wt-a")]
    sentinel, lines = make_sentinel(table, {10: 6 * GIB})
    sent: list[int] = []
    monkeypatch.setattr(sentinel, "_send", lambda p, sig: sent.append(p.pid))
    sentinel.step()
    sentinel.step()
    assert sum("BREACH process footprint" in line for line in lines) == 1
    assert "action: warn only" in "\n".join(lines)
    assert sent == []


def test_off_mode_does_nothing() -> None:
    table = [proc(10, cwd="/work/BenchBox.wt-a")]
    sentinel, lines = make_sentinel(table, {10: 6 * GIB}, mode="off")
    sentinel.step()
    assert lines == []


def test_an_unmeasured_budget_never_acts() -> None:
    table = [proc(10, cwd="/work/BenchBox.wt-a")]
    sentinel, lines = make_sentinel(table, {10: 60 * GIB}, budget=None)
    sentinel.step()
    assert not any("BREACH" in line for line in lines)


def test_summary_lists_footprints_of_benchbox_rooted_processes() -> None:
    table = [proc(10, cwd="/work/BenchBox.wt-a"), proc(11, cwd="/other")]
    sentinel, lines = make_sentinel(table, {10: GIB, 11: GIB})
    sentinel.step()
    text = "\n".join(lines)
    assert "1 BenchBox-rooted processes 1.00 GiB" in text
    assert "pid 10 python3.12 1.00 GiB by cwd" in text
    assert "pid 11" not in text


def git_procs(count: int) -> list[git_flood.GitProc]:
    return [git_flood.GitProc(1000 + n, "/work/BenchBox.wt-a", "git status") for n in range(count)]


def test_a_git_flood_by_count_is_reported_and_never_signalled(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[int] = []
    monkeypatch.setattr(hs.Sentinel, "_send", lambda self, proc, sig: sent.append(proc.pid))
    sizes = {1000 + n: 100 * MIB for n in range(16)}
    sentinel, lines = make_sentinel([], sizes, mode="kill", git=git_procs(16))
    sentinel.step()
    text = "\n".join(lines)
    assert "WARNING git flood: 16 git processes >= 16" in text
    assert "git is never signalled" in text
    assert "pid 1000 0.10 GiB cwd /work/BenchBox.wt-a cmd git status" in text
    assert sent == []


def test_a_git_flood_by_footprint_is_reported_below_the_count_threshold() -> None:
    sizes = {1000 + n: 2 * GIB for n in range(3)}
    sentinel, lines = make_sentinel([], sizes, git=git_procs(3))
    sentinel.step()
    assert any("git footprint 6.00 GiB >= 4.00 GiB" in line for line in lines)


def test_the_git_flood_thresholds_come_from_the_environment() -> None:
    sizes = {1000 + n: MIB for n in range(3)}
    sentinel, lines = make_sentinel([], sizes, git=git_procs(3), environ={git_flood.COUNT_ENV: "3"})
    sentinel.step()
    assert any("3 git processes >= 3" in line for line in lines)
    quiet, quiet_lines = make_sentinel([], sizes, git=git_procs(3))
    quiet.step()
    assert not any("git flood" in line for line in quiet_lines)


def test_a_git_flood_is_reported_again_only_after_the_summary_interval() -> None:
    sizes = {1000 + n: MIB for n in range(16)}
    sentinel, lines = make_sentinel([], sizes, git=git_procs(16))
    sentinel.step()
    sentinel.step()
    assert sum("WARNING git flood" in line for line in lines) == 1


def test_kill_mode_verifies_identity_before_signalling() -> None:
    table = [proc(10, cwd="/work/BenchBox.wt-a")]
    sentinel, lines = make_sentinel(table, {10: 6 * GIB}, mode="kill")
    sentinel.step()
    assert any("skipped SIGTERM for pid 10" in line and "no longer the sampled process" in line for line in lines)


def test_worktrees_come_from_the_primary_clone_of_the_common_git_dir(tmp_path: Path) -> None:
    primary = tmp_path / "BenchBox"
    linked = tmp_path / "BenchBox.wt-x"
    primary.mkdir()
    linked.mkdir()
    calls: list[list[str]] = []

    def runner(argv, timeout):
        calls.append(list(argv))
        if "rev-parse" in argv:
            return f"{primary}/.git\n"
        return f"worktree {primary}\nHEAD abc\n\nworktree {linked}\nHEAD def\n\nworktree {tmp_path / 'gone'}\n"

    assert hs.discover_worktrees(tmp_path, runner) == [primary, linked]
    assert calls[1][:3] == ["git", "-C", str(primary)]


def test_held_tree_pids_ignores_a_dead_holder(tmp_path: Path) -> None:
    lock = tmp_path / hl.LOCK_NAME
    lock.write_text("pid:999999 owner:999999:1.00 started:2026-01-01 00:00:00 phase:run cmd:old\n", encoding="utf-8")
    assert hs.held_tree_pids(lock) == frozenset()


def test_held_tree_pids_returns_the_live_holders_tree(tmp_path: Path) -> None:
    handle = hl.acquire(tmp_path / hl.LOCK_NAME, wait_seconds=0, phase="run", command="x")
    try:
        assert os.getpid() in hs.held_tree_pids(handle.path)
    finally:
        handle.release()


def test_log_rotation_bounds_the_log_size(tmp_path: Path) -> None:
    path = tmp_path / "logs" / "sentinel.log"
    log = hs.SentinelLog(path, max_bytes=2000, backups=1, err=io.StringIO())
    for index in range(500):
        log.write(f"line {index} " + "x" * 80)
    log.close()
    sizes = [item.stat().st_size for item in path.parent.iterdir()]
    assert len(sizes) == 2
    assert max(sizes) <= 2000
    assert path.parent.joinpath("sentinel.log.1").is_file()


def stub_launchctl(tmp_path: Path) -> tuple[Path, Path]:
    record = tmp_path / "launchctl.calls"
    script = tmp_path / "launchctl"
    script.write_text(f'#!/bin/sh\necho "$@" >> {record}\nexit 0\n')
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script, record


def test_install_and_uninstall_use_the_agents_dir_and_launchctl_bootstrap_bootout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agents = tmp_path / "LaunchAgents"
    script, record = stub_launchctl(tmp_path)
    repo = tmp_path / "BenchBox"
    repo.mkdir()
    env = {heavy_status.AGENTS_DIR_ENV: str(agents), "BENCHBOX_HEAVY_WATCHDOG": "warn"}
    assert hs.main(["install", "--launchctl", str(script), "--repo", str(repo), "--python", "/p/python"], env) == 0
    plist_path = agents / f"{heavy_status.LAUNCH_AGENT_LABEL}.plist"
    data = plistlib.loads(plist_path.read_bytes())
    assert data["ProgramArguments"] == ["/p/python", "-m", "benchbox.utils.host_lock", "sentinel"]
    assert data["WorkingDirectory"] == str(repo)
    assert data["EnvironmentVariables"]["BENCHBOX_HEAVY_WATCHDOG"] == "warn"
    assert data["KeepAlive"] is True
    uid = os.getuid()
    assert record.read_text(encoding="utf-8").splitlines() == [f"bootstrap gui/{uid} {plist_path}"]
    assert hs.main(["uninstall", "--launchctl", str(script)], env) == 0
    assert not plist_path.exists()
    assert record.read_text(encoding="utf-8").splitlines()[-1] == f"bootout gui/{uid}/{heavy_status.LAUNCH_AGENT_LABEL}"


def test_reinstall_boots_out_the_loaded_agent_first(tmp_path: Path) -> None:
    agents = tmp_path / "LaunchAgents"
    script, record = stub_launchctl(tmp_path)
    env = {heavy_status.AGENTS_DIR_ENV: str(agents)}
    for _ in range(2):
        assert sentinel_launchd.install(env, repo=tmp_path, python="/p/python", launchctl=str(script)) == 0
    verbs = [line.split()[0] for line in record.read_text(encoding="utf-8").splitlines()]
    assert verbs == ["bootstrap", "bootout", "bootstrap"]


def test_a_failing_bootstrap_is_reported(tmp_path: Path) -> None:
    script = tmp_path / "launchctl"
    script.write_text("#!/bin/sh\necho denied >&2\nexit 5\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    env = {heavy_status.AGENTS_DIR_ENV: str(tmp_path / "agents")}
    assert sentinel_launchd.install(env, repo=tmp_path, python="/p/python", launchctl=str(script)) == 1


def test_status_reports_the_sentinel_running_and_not_running(tmp_path: Path) -> None:
    env = {heavy_status.SENTINEL_LOG_ENV: str(tmp_path / "sentinel.log"), heavy_status.AGENTS_DIR_ENV: str(tmp_path)}
    stopped = heavy_status.read_sentinel_state(env)
    assert not stopped.running
    assert "sentinel: not running" in heavy_status._render_sentinel(_reading(stopped))[0]
    (tmp_path / heavy_status.SENTINEL_PID_NAME).write_text(hl.process_identity() + "\n", encoding="utf-8")
    running = heavy_status.read_sentinel_state(env)
    assert running.running and running.pid == os.getpid()
    assert f"sentinel: running (pid {os.getpid()}" in heavy_status._render_sentinel(_reading(running))[0]
    (tmp_path / heavy_status.SENTINEL_PID_NAME).write_text("999999:1.00\n", encoding="utf-8")
    assert not heavy_status.read_sentinel_state(env).running


def _reading(state: heavy_status.SentinelState) -> heavy_status.StatusReading:
    return heavy_status.StatusReading(Path("x/test.lock"), None, 0, None, None, None, None, state)


def test_a_second_sentinel_refuses_while_one_is_live(tmp_path: Path) -> None:
    pid_path = tmp_path / "sentinel.pid"
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        pid_path.write_text(hl.process_identity(psutil.Process(other.pid)) + "\n", encoding="utf-8")
        assert not hs.claim_pid_file(pid_path)
    finally:
        other.kill()
        other.wait()
    assert hs.claim_pid_file(pid_path)
    hs.release_pid_file(pid_path)
    assert not pid_path.exists()


@pytest.fixture
def spawned() -> Iterator[list[subprocess.Popen]]:
    procs: list[subprocess.Popen] = []
    yield procs
    for item in procs:
        if item.poll() is None:
            item.kill()
        item.wait()


def wait_for(predicate: Callable[[], bool], timeout: float = 25.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.1)
    return False


def start_sentinel(tmp_path: Path, worktree: Path, mode: str, spawned: list[subprocess.Popen]) -> tuple[Path, Path]:
    log = tmp_path / "state" / "sentinel.log"
    env = {
        **os.environ,
        "BENCHBOX_HEAVY_WATCHDOG": mode,
        "BENCHBOX_HEAVY_MEMORY_BUDGET_GIB": "0.1",
        "BENCHBOX_HEAVY_WATCHDOG_GRACE_SECONDS": "3",
        hl.LOCK_DIR_ENV: str(tmp_path / "locks"),
    }
    spawned.append(
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "benchbox.utils.host_lock",
                "sentinel",
                "--duration",
                "60",
                "--interval",
                "0.2",
                "--log",
                str(log),
                "--worktree",
                str(worktree),
            ],
            env=env,
            stderr=subprocess.DEVNULL,
        )
    )
    return log, tmp_path / "state" / heavy_status.SENTINEL_PID_NAME


def start_hog(worktree: Path, spawned: list[subprocess.Popen]) -> subprocess.Popen:
    code = "b = b'x' * (300 * 1024 * 1024)\nimport time\nprint('ready', flush=True)\ntime.sleep(120)\n"
    hog = subprocess.Popen([sys.executable, "-c", code], cwd=worktree, stdout=subprocess.PIPE, text=True)
    spawned.append(hog)
    assert hog.stdout is not None
    assert hog.stdout.readline().strip() == "ready"
    return hog


def test_kill_mode_terminates_an_over_budget_child_in_a_registered_worktree(
    tmp_path: Path, spawned: list[subprocess.Popen]
) -> None:
    worktree = tmp_path / "BenchBox.wt-fake"
    worktree.mkdir()
    hog = start_hog(worktree, spawned)
    log, _ = start_sentinel(tmp_path, worktree, "kill", spawned)
    assert wait_for(lambda: hog.poll() is not None)
    assert wait_for(lambda: log.is_file() and "sent SIGTERM" in log.read_text(encoding="utf-8"))
    text = log.read_text(encoding="utf-8")
    assert f"pid {hog.pid}" in text
    assert "BREACH process footprint" in text


def test_warn_mode_reports_but_leaves_the_child_running(tmp_path: Path, spawned: list[subprocess.Popen]) -> None:
    worktree = tmp_path / "BenchBox.wt-fake"
    worktree.mkdir()
    hog = start_hog(worktree, spawned)
    log, _ = start_sentinel(tmp_path, worktree, "warn", spawned)
    assert wait_for(lambda: log.is_file() and "BREACH process footprint" in log.read_text(encoding="utf-8"))
    time.sleep(1.5)
    assert hog.poll() is None
    assert "sent SIG" not in log.read_text(encoding="utf-8")


def test_a_child_outside_the_registered_worktree_is_left_alone(tmp_path: Path, spawned: list[subprocess.Popen]) -> None:
    worktree = tmp_path / "BenchBox.wt-fake"
    elsewhere = tmp_path / "other"
    worktree.mkdir()
    elsewhere.mkdir()
    hog = start_hog(elsewhere, spawned)
    log, _ = start_sentinel(tmp_path, worktree, "kill", spawned)
    assert wait_for(lambda: log.is_file() and "started pid" in log.read_text(encoding="utf-8"))
    time.sleep(2.0)
    assert hog.poll() is None
    assert "BREACH" not in log.read_text(encoding="utf-8")


def test_the_sentinel_records_its_pid_for_heavy_status_and_removes_it_on_exit(
    tmp_path: Path, spawned: list[subprocess.Popen]
) -> None:
    worktree = tmp_path / "BenchBox.wt-fake"
    worktree.mkdir()
    log, pid_path = start_sentinel(tmp_path, worktree, "warn", spawned)
    assert wait_for(pid_path.is_file)
    env = {heavy_status.SENTINEL_LOG_ENV: str(log), heavy_status.AGENTS_DIR_ENV: str(tmp_path)}
    assert heavy_status.read_sentinel_state(env).running
    sentinel = spawned[-1]
    sentinel.terminate()
    sentinel.wait(timeout=20)
    assert not pid_path.exists()
    assert not heavy_status.read_sentinel_state(env).running
