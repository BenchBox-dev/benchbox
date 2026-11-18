from __future__ import annotations

import io
import json
import os
import plistlib
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

from benchbox.utils import git_flood, heavy_sentinel as hs, heavy_status, host_lock as hl, orphan_spin, sentinel_launchd

pytestmark = [pytest.mark.unit, pytest.mark.medium, pytest.mark.skipif(sys.platform == "win32", reason="POSIX only")]


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hl, "real_home", lambda: tmp_path / "home")
    monkeypatch.setenv(heavy_status.SENTINEL_PID_ENV, str(tmp_path / "home" / "sentinel.pid"))


GIB = 2**30
MIB = 2**20
PYTHON = "/usr/bin/python3"
REPO_ROOT = Path(__file__).resolve().parents[3]


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
    orphans: Callable[[], list[orphan_spin.OrphanShell]] = list,
    clock: Callable[[], float] | None = None,
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
        orphan_reader=lambda roots: orphans(),
        environ=environ or {},
        exclude=(),
        **({} if clock is None else {"clock": clock}),
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


def test_command_line_paths_are_used_only_when_the_cwd_is_unreadable() -> None:
    roots = hs._root_variants([Path("/work/BenchBox.wt-a")])
    elsewhere = proc(1, cwd="/other", exe=PYTHON, cmdline=("python3", "/work/BenchBox.wt-a/run.py"))
    assert hs.locate(elsewhere, roots) is None


def test_language_server_argv_stems_are_never_candidates() -> None:
    for stem in ("pylsp", "my_language_server", "my-language-server", "jedilangserver", "ty_lsp"):
        server = proc(10, cwd="/work/BenchBox.wt-a", cmdline=("python3.12", f"/tools/{stem}.py"))
        assert not hs.is_candidate(server)


def test_mcp_servers_are_never_candidates_but_a_repo_named_mcp_is_not_a_marker() -> None:
    assert not hs.is_candidate(proc(9, cwd="/w", cmdline=("python3.12", "/tools/tractable-mcp")))
    assert not hs.is_candidate(proc(10, cwd="/w", cmdline=("python3.12", "/tools/todo-db-mcp")))
    assert not hs.is_candidate(proc(11, cwd="/w", cmdline=("python3.12", "/tools/mcp-server/run.py")))
    assert not hs.is_candidate(proc(12, cwd="/w", cmdline=("python3.12", "-m", "benchbox.mcp.server")))
    inside = proc(13, cwd="/w", cmdline=("/work/BenchBox.wt-mcp-fix/.venv/bin/python", "-m", "pytest", "tests/mcp"))
    assert hs.is_candidate(inside)


class VanishingProcess:
    info = {"pid": 4242, "ppid": 1, "name": "python3.12", "create_time": 1.0, "uids": None}

    def as_dict(self, attrs: list[str], ad_value: object = None) -> dict[str, object]:
        raise psutil.NoSuchProcess(4242)


def test_a_process_that_exits_while_being_read_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: iter([VanishingProcess()]))
    assert hs.read_processes() == []
    vanishing = VanishingProcess()
    vanishing.info = {**vanishing.info, "name": "git"}
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: iter([vanishing]))
    assert git_flood.read_git_processes(["/work/BenchBox.wt-a"]) == []


def test_policy_reads_the_injected_environment_not_the_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BENCHBOX_HEAVY_WATCHDOG", "off")
    args = hs.build_parser().parse_args([])
    env = {"BENCHBOX_HEAVY_WATCHDOG": "kill", "BENCHBOX_HEAVY_WATCHDOG_GRACE_SECONDS": "7"}
    policy = hs.policy_from_env(env, args)
    assert policy.mode == "kill"
    assert policy.grace_seconds == 7.0


def test_the_pid_file_is_fixed_and_independent_of_the_log_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hl, "real_home", lambda: tmp_path)
    env = {heavy_status.SENTINEL_LOG_ENV: str(tmp_path / "elsewhere" / "s.log")}
    assert heavy_status.sentinel_pid_path(env) == tmp_path / ".benchbox" / "sentinel.pid"


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


@pytest.fixture(autouse=True)
def isolated_orphan_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "state" / orphan_spin.ACTIVE_NAME
    monkeypatch.setattr(heavy_status, "orphan_spin_path", lambda environ=None: path)
    return path


class SpinRig:
    def __init__(self, *, cpu_per_second: float, mode: str = "warn", environ: dict[str, str] | None = None) -> None:
        self.now = 1000.0
        self.cpu_seconds = 50.0
        self.rate = cpu_per_second
        self.present = True
        self.shell = orphan_spin.OrphanShell(
            pid=4242,
            created=1.0,
            name="bash",
            cwd="/work/BenchBox.wt-a/sub",
            command="bash -c while true; do :; done",
            cpu_seconds=self.cpu_seconds,
        )
        self.sentinel, self.lines = make_sentinel(
            [],
            {},
            mode=mode,
            orphans=self.table,
            clock=lambda: self.now,
            environ=environ,
        )

    def table(self) -> list[orphan_spin.OrphanShell]:
        if not self.present:
            return []
        return [
            orphan_spin.OrphanShell(
                self.shell.pid,
                self.shell.created,
                self.shell.name,
                self.shell.cwd,
                self.shell.command,
                self.cpu_seconds,
            )
        ]

    def tick(self, seconds: float) -> None:
        self.now += seconds
        self.cpu_seconds += self.rate * seconds
        self.sentinel.step()

    def warnings(self) -> list[str]:
        return [line for line in self.lines if "orphaned CPU-bound shell" in line and "WARNING" in line]


def test_an_orphaned_shell_holding_sustained_cpu_is_reported_once_and_never_signalled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[int] = []
    monkeypatch.setattr(hs.Sentinel, "_send", lambda self, proc, sig: sent.append(proc.pid))
    rig = SpinRig(cpu_per_second=1.0, mode="kill")
    for _ in range(80):
        rig.tick(5.0)
    warnings = rig.warnings()
    assert len(warnings) == 1
    assert "pid 4242 bash at 100% CPU" in warnings[0]
    assert "cwd /work/BenchBox.wt-a/sub" in warnings[0]
    assert "never signalled" in warnings[0]
    assert sent == []


def test_a_reported_orphan_is_published_for_the_readers_until_it_goes_idle_or_exits(
    isolated_orphan_report: Path,
) -> None:
    rig = SpinRig(cpu_per_second=1.0)
    for _ in range(80):
        rig.tick(5.0)
    published = orphan_spin.ActiveSpin(**json.loads(isolated_orphan_report.read_text(encoding="utf-8"))[0])
    assert (published.pid, published.cwd, published.cpu_fraction) == (4242, "/work/BenchBox.wt-a/sub", 1.0)
    assert published.busy_since < time.time()
    rig.rate = 0.0
    rig.tick(5.0)
    rig.tick(5.0)
    assert not isolated_orphan_report.exists()
    rig.rate = 1.0
    for _ in range(80):
        rig.tick(5.0)
    assert isolated_orphan_report.exists()
    rig.present = False
    rig.tick(5.0)
    assert not isolated_orphan_report.exists()


def test_nothing_is_published_without_a_report(isolated_orphan_report: Path) -> None:
    rig = SpinRig(cpu_per_second=0.0)
    for _ in range(80):
        rig.tick(5.0)
    assert not isolated_orphan_report.exists()


def test_an_orphaned_shell_is_not_reported_before_the_sustain_window() -> None:
    rig = SpinRig(cpu_per_second=1.0)
    for _ in range(50):
        rig.tick(5.0)
    assert rig.warnings() == []


def test_an_idle_orphaned_shell_is_never_reported() -> None:
    rig = SpinRig(cpu_per_second=0.01)
    for _ in range(400):
        rig.tick(5.0)
    assert rig.warnings() == []


def test_a_burst_shorter_than_the_window_resets_when_the_shell_goes_idle() -> None:
    rig = SpinRig(cpu_per_second=1.0)
    for _ in range(40):
        rig.tick(5.0)
    rig.rate = 0.0
    rig.tick(5.0)
    rig.rate = 1.0
    for _ in range(40):
        rig.tick(5.0)
    assert rig.warnings() == []


def test_the_orphan_thresholds_come_from_the_environment() -> None:
    rig = SpinRig(
        cpu_per_second=0.3,
        environ={orphan_spin.CPU_FRACTION_ENV: "0.25", orphan_spin.SECONDS_ENV: "30"},
    )
    for _ in range(10):
        rig.tick(5.0)
    assert len(rig.warnings()) == 1


def test_an_orphan_that_exits_is_logged_as_gone_and_a_new_one_is_tracked_afresh() -> None:
    rig = SpinRig(cpu_per_second=1.0)
    for _ in range(80):
        rig.tick(5.0)
    rig.present = False
    rig.tick(5.0)
    assert any("pid 4242 is gone" in line for line in rig.lines)
    rig.present = True
    for _ in range(80):
        rig.tick(5.0)
    assert len(rig.warnings()) == 2


def test_orphans_are_not_read_when_no_checkout_is_known() -> None:
    read: list[Sequence[str]] = []
    sentinel, lines = make_sentinel([], {}, roots=())
    sentinel._orphan_reader = lambda roots: read.append(roots) or []
    sentinel.step()
    assert read == []
    assert not any("orphaned" in line for line in lines)


class FakeProc:
    def __init__(self, info: dict[str, object], detail: dict[str, object]) -> None:
        self.info = info
        self._detail = detail

    def as_dict(self, attrs: list[str], ad_value: object = None) -> dict[str, object]:
        return {key: self._detail.get(key, ad_value) for key in attrs}


def fake_shell(
    pid: int,
    *,
    ppid: int = 1,
    name: str = "bash",
    cwd: str | None = "/work/BenchBox.wt-a",
    user: int = 501,
) -> FakeProc:
    return FakeProc(
        {
            "pid": pid,
            "ppid": ppid,
            "name": name,
            "create_time": float(pid),
            "uids": SimpleNamespace(real=user),
        },
        {"cwd": cwd, "cmdline": [name, "-c", "spin"], "cpu_times": SimpleNamespace(user=40.0, system=2.0)},
    )


def test_the_process_table_filter_selects_only_orphaned_shells_of_this_user_in_a_checkout() -> None:
    table = [
        fake_shell(10),
        fake_shell(11, ppid=500),
        fake_shell(12, name="python3.12"),
        fake_shell(13, cwd="/home/someone/notes"),
        fake_shell(14, cwd=None),
        fake_shell(15, user=502),
        fake_shell(16, cwd="/work/BenchBox.wt-ab"),
        fake_shell(17, name="zsh", cwd="/work/BenchBox.wt-a/deep/sub"),
    ]
    found = orphan_spin.read_orphan_shells(("/work/BenchBox.wt-a",), processes=lambda attrs: iter(table), uid=501)
    assert [shell.pid for shell in found] == [10, 17]
    assert found[0].cpu_seconds == 42.0
    assert found[0].command == "bash -c spin"


def test_summaries_are_logged_only_when_the_heavy_set_pressure_or_flood_changes() -> None:
    table = [proc(10, cwd="/work/BenchBox.wt-a"), proc(11, cwd="/work/BenchBox.wt-a")]
    sizes = {10: GIB, 11: 3 * GIB}
    pressure = {"level": 1}
    sentinel, lines = make_sentinel(table, sizes)
    sentinel._pressure_reader = lambda: pressure["level"]
    sentinel.step()
    sentinel.step()
    assert sum("BenchBox-rooted" in line for line in lines) == 1
    sizes[10] = 3 * GIB
    sentinel.step()
    assert sum("BenchBox-rooted" in line for line in lines) == 2
    pressure["level"] = 2
    sentinel.step()
    assert sum("BenchBox-rooted" in line for line in lines) == 3
    sizes[10] = 3 * GIB + MIB
    sentinel.step()
    assert sum("BenchBox-rooted" in line for line in lines) == 3


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
    repo = REPO_ROOT
    assert repo.is_dir()
    env = {heavy_status.AGENTS_DIR_ENV: str(agents), "BENCHBOX_HEAVY_WATCHDOG": "warn"}
    assert hs.main(["install", "--launchctl", str(script), "--repo", str(repo), "--python", sys.executable], env) == 0
    plist_path = agents / f"{heavy_status.LAUNCH_AGENT_LABEL}.plist"
    data = plistlib.loads(plist_path.read_bytes())
    assert data["ProgramArguments"] == [sys.executable, "-m", "benchbox.utils.host_lock", "sentinel"]
    assert data["WorkingDirectory"] == str(repo.resolve())
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
        assert sentinel_launchd.install(env, repo=REPO_ROOT, python=sys.executable, launchctl=str(script)) == 0
    verbs = [line.split()[0] for line in record.read_text(encoding="utf-8").splitlines()]
    assert verbs == ["bootstrap", "bootout", "bootstrap"]


def test_a_failing_bootstrap_is_reported(tmp_path: Path) -> None:
    script = tmp_path / "launchctl"
    script.write_text("#!/bin/sh\necho denied >&2\nexit 5\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    env = {heavy_status.AGENTS_DIR_ENV: str(tmp_path / "agents")}
    assert sentinel_launchd.install(env, repo=REPO_ROOT, python=sys.executable, launchctl=str(script)) == 1


def test_install_refuses_when_the_sentinel_cannot_start_with_that_python(tmp_path: Path) -> None:
    script, record = stub_launchctl(tmp_path)
    agents = tmp_path / "agents"
    env = {heavy_status.AGENTS_DIR_ENV: str(agents)}
    assert sentinel_launchd.install(env, repo=tmp_path, python="/nonexistent/python", launchctl=str(script)) == 1
    assert not record.exists()
    assert not agents.exists()


def test_install_records_the_launchd_error_path_and_forwards_heavy_settings(tmp_path: Path) -> None:
    script, _ = stub_launchctl(tmp_path)
    repo = REPO_ROOT
    assert repo.is_dir()
    env = {
        heavy_status.AGENTS_DIR_ENV: str(tmp_path / "agents"),
        "BENCHBOX_HEAVY_MEMORY_BUDGET_GIB": "6",
        "BENCHBOX_HEAVY_WATCHDOG_GRACE_SECONDS": "4",
        "BENCHBOX_GIT_FLOOD_COUNT": "9",
        hl.LOCK_DIR_ENV: str(tmp_path / "locks"),
        "BENCHBOX_UNRELATED": "x",
    }
    assert sentinel_launchd.install(env, repo=repo, python=sys.executable, launchctl=str(script)) == 0
    data = plistlib.loads(heavy_status.launch_agent_path(env).read_bytes())
    variables = data["EnvironmentVariables"]
    assert variables["BENCHBOX_HEAVY_MEMORY_BUDGET_GIB"] == "6"
    assert variables["BENCHBOX_HEAVY_WATCHDOG_GRACE_SECONDS"] == "4"
    assert variables["BENCHBOX_GIT_FLOOD_COUNT"] == "9"
    assert variables[hl.LOCK_DIR_ENV] == str(tmp_path / "locks")
    assert "BENCHBOX_UNRELATED" not in variables
    assert data["StandardErrorPath"] == str(heavy_status.sentinel_launchd_err_path())
    assert data["StandardErrorPath"].endswith("sentinel.launchd.err")


def test_uninstall_fails_unless_the_agent_was_simply_not_loaded(tmp_path: Path) -> None:
    env = {heavy_status.AGENTS_DIR_ENV: str(tmp_path / "agents")}
    plist_path = heavy_status.launch_agent_path(env)
    plist_path.parent.mkdir()
    plist_path.write_text("x", encoding="utf-8")
    broken = tmp_path / "launchctl-broken"
    broken.write_text("#!/bin/sh\necho denied >&2\nexit 5\n")
    broken.chmod(broken.stat().st_mode | stat.S_IXUSR)
    assert sentinel_launchd.uninstall(env, launchctl=str(broken)) == 1
    assert plist_path.exists()
    idle = tmp_path / "launchctl-idle"
    idle.write_text("#!/bin/sh\necho 'Boot-out failed: 3: No such process' >&2\nexit 3\n")
    idle.chmod(idle.stat().st_mode | stat.S_IXUSR)
    assert sentinel_launchd.uninstall(env, launchctl=str(idle)) == 0
    assert not plist_path.exists()


def test_status_reports_the_sentinel_running_and_not_running(tmp_path: Path) -> None:
    pid_file = tmp_path / heavy_status.SENTINEL_PID_NAME
    env = {
        heavy_status.SENTINEL_LOG_ENV: str(tmp_path / "other" / "sentinel.log"),
        heavy_status.SENTINEL_PID_ENV: str(pid_file),
        heavy_status.AGENTS_DIR_ENV: str(tmp_path),
    }
    stopped = heavy_status.read_sentinel_state(env)
    assert not stopped.running
    assert "sentinel: not running" in heavy_status._render_sentinel(_reading(stopped))[0]
    pid_file.write_text(hl.process_identity() + "\n", encoding="utf-8")
    running = heavy_status.read_sentinel_state(env)
    assert running.running and running.pid == os.getpid()
    assert f"sentinel: running (pid {os.getpid()}" in heavy_status._render_sentinel(_reading(running))[0]
    pid_file.write_text("999999:1.00\n", encoding="utf-8")
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
        "BENCHBOX_HEAVY_LINUX_MACHINE": "benchbox-test-absent-machine",
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
    return log, tmp_path / "home" / heavy_status.SENTINEL_PID_NAME


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
    env = {
        heavy_status.SENTINEL_LOG_ENV: str(log),
        heavy_status.SENTINEL_PID_ENV: str(pid_path),
        heavy_status.AGENTS_DIR_ENV: str(tmp_path),
    }
    assert heavy_status.read_sentinel_state(env).running
    sentinel = spawned[-1]
    sentinel.terminate()
    sentinel.wait(timeout=20)
    assert not pid_path.exists()
    assert not heavy_status.read_sentinel_state(env).running
