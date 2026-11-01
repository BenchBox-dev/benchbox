from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import re
import signal
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO

import psutil

from benchbox.utils import git_flood, heavy_status, heavy_watchdog, host_lock, process_tree, resource_limits
from benchbox.utils.clock import elapsed_seconds, mono_time
from benchbox.utils.footprint import pid_footprint_bytes

INTERVAL_ENV = "BENCHBOX_SENTINEL_INTERVAL_SECONDS"
DEFAULT_INTERVAL_SECONDS = 5.0
WORKTREE_REFRESH_SECONDS = 60.0
LANGUAGE_SERVER_MARKERS = ("lsp", "language_server", "language-server", "langserver")
LOG_MAX_BYTES = 1024 * 1024
LOG_BACKUPS = 2
WARN_PRESSURE_LEVEL = 2
CRITICAL_PRESSURE_LEVEL = heavy_watchdog.CRITICAL_PRESSURE_LEVEL
TOP_COUNT = 5
COMMAND_WIDTH = 200
GIB = resource_limits.GIB
LOG_PREFIX = "[heavy-sentinel]"
GIT_TIMEOUT_SECONDS = 5.0
CREATE_TIME_TOLERANCE = 1e-3
SIG_TERM = signal.Signals["SIGTERM"]
SIG_KILL = getattr(signal, "SIGKILL", SIG_TERM)
EXIT_ALREADY_RUNNING = host_lock.EXIT_REFUSED
DENIED_NAMES = frozenset(
    {
        "node",
        "nodejs",
        "bun",
        "deno",
        "electron",
        "code",
        "cursor",
        "zed",
        "vim",
        "nvim",
        "emacs",
        "ty",
        "ruff",
        "tern",
        "pyright",
        "pyright-langserver",
        "basedpyright",
        "basedpyright-langserver",
        "pylsp",
        "jedi-language-server",
        "rust-analyzer",
        "gopls",
        "typescript-language-server",
        "bash",
        "zsh",
        "sh",
        "dash",
        "fish",
        "tmux",
        "git",
    }
)
_PYTHON_NAME = re.compile(r"^python[0-9.]*$", re.IGNORECASE)
_INTERPRETER_ARGS = 3


@dataclass(frozen=True)
class ProcInfo:
    pid: int
    ppid: int
    name: str
    exe: str | None
    cmdline: tuple[str, ...]
    cwd: str | None
    created: float


@dataclass(frozen=True)
class Measured:
    proc: ProcInfo
    footprint_bytes: int
    scope: str
    held: bool


@dataclass(frozen=True)
class Evaluation:
    measured: tuple[Measured, ...]
    breaches: tuple[heavy_watchdog.Breach, ...]
    offender: Measured | None


@dataclass(frozen=True)
class SentinelPolicy:
    mode: str
    budget_bytes: int | None
    interval_seconds: float = DEFAULT_INTERVAL_SECONDS
    grace_seconds: float = heavy_watchdog.DEFAULT_GRACE_SECONDS
    worktree_refresh_seconds: float = WORKTREE_REFRESH_SECONDS


def _basename(value: str) -> str:
    return os.path.basename(value.rstrip("/")).lower()


def name_is_allowed(name: str) -> bool:
    lowered = name.lower()
    if lowered in DENIED_NAMES:
        return False
    return bool(_PYTHON_NAME.match(lowered)) or lowered in heavy_status.SERVER_NAMES or "questdb" in lowered


def is_candidate(proc: ProcInfo) -> bool:
    names = {_basename(proc.name)}
    if proc.exe:
        names.add(_basename(proc.exe))
    if names & DENIED_NAMES:
        return False
    leading = proc.cmdline[:_INTERPRETER_ARGS]
    base = {_basename(arg) for arg in leading}
    stems = {Path(arg).stem for arg in base}
    if stems & DENIED_NAMES or base & DENIED_NAMES:
        return False
    if any(marker in stem for stem in stems for marker in LANGUAGE_SERVER_MARKERS):
        return False
    mcp_parts = [_basename(leading[0])] if leading else []
    mcp_parts += [part for arg in leading[1:] for part in (_basename(arg), _basename(os.path.dirname(arg)))]
    if any("mcp" in part for part in mcp_parts):
        return False
    return any(name_is_allowed(name) for name in names)


def _root_variants(roots: Sequence[Path]) -> tuple[str, ...]:
    variants: set[str] = set()
    for root in roots:
        variants.add(os.path.normpath(str(root)))
        variants.add(os.path.realpath(str(root)))
    return tuple(sorted(variants))


def _under(path: str, roots: Sequence[str]) -> bool:
    normal = os.path.normpath(path)
    return any(normal == root or normal.startswith(root + os.sep) for root in roots)


def locate(proc: ProcInfo, roots: Sequence[str]) -> str | None:
    if proc.cwd and _under(proc.cwd, roots):
        return "cwd"
    if proc.exe and _under(proc.exe, roots):
        return "exe"
    if proc.cwd is None and any(os.path.isabs(arg) and _under(arg, roots) for arg in proc.cmdline):
        return "cmdline"
    return None


def evaluate(measured: Sequence[Measured], pressure: int, budget_bytes: int | None) -> Evaluation:
    eligible = [m for m in measured if not m.held or pressure >= CRITICAL_PRESSURE_LEVEL]
    if budget_bytes is None or budget_bytes <= 0 or not eligible:
        return Evaluation(tuple(measured), (), None)
    top = max(eligible, key=lambda m: m.footprint_bytes)
    breaches: list[heavy_watchdog.Breach] = []
    limit = f"{budget_bytes / GIB:.2f} GiB budget"
    if top.footprint_bytes > budget_bytes:
        breaches.append(
            heavy_watchdog.Breach(
                "process footprint", f"pid {top.proc.pid} {top.proc.name} {top.footprint_bytes / GIB:.2f} GiB", limit
            )
        )
    total = sum(m.footprint_bytes for m in eligible)
    if pressure >= WARN_PRESSURE_LEVEL and total > budget_bytes:
        breaches.append(
            heavy_watchdog.Breach(
                "memory pressure with combined footprint",
                f"pressure level {pressure}, {len(eligible)} processes {total / GIB:.2f} GiB",
                limit,
            )
        )
    return Evaluation(tuple(measured), tuple(breaches), top if breaches else None)


def read_processes(prefilter: Callable[[str], bool] = name_is_allowed) -> list[ProcInfo]:
    uid = os.getuid() if hasattr(os, "getuid") else None
    found: list[ProcInfo] = []
    for proc in psutil.process_iter(["pid", "ppid", "name", "create_time", "uids"]):
        info = proc.info
        if info["pid"] is None or info["name"] is None or info["create_time"] is None:
            continue
        uids = info["uids"]
        if uid is not None and uids is not None and uids.real != uid:
            continue
        if not prefilter(info["name"]):
            continue
        try:
            detail = proc.as_dict(["exe", "cmdline", "cwd"], ad_value=None)
        except psutil.NoSuchProcess:
            continue
        found.append(
            ProcInfo(
                pid=info["pid"],
                ppid=info["ppid"] or 0,
                name=info["name"],
                exe=detail["exe"] or None,
                cmdline=tuple(detail["cmdline"] or ()),
                cwd=detail["cwd"] or None,
                created=info["create_time"],
            )
        )
    return found


def primary_clone(start: Path, runner: resource_limits.Runner = resource_limits.run_text) -> Path | None:
    text = runner(
        ["git", "-C", str(start), "rev-parse", "--path-format=absolute", "--git-common-dir"], GIT_TIMEOUT_SECONDS
    )
    if text is None or not text.strip():
        return None
    common = Path(text.strip())
    return common.parent if common.name == ".git" else common


def list_worktrees(primary: Path, runner: resource_limits.Runner = resource_limits.run_text) -> list[Path]:
    text = runner(["git", "-C", str(primary), "worktree", "list", "--porcelain"], GIT_TIMEOUT_SECONDS)
    if text is None:
        return []
    paths = [Path(line[len("worktree ") :]) for line in text.splitlines() if line.startswith("worktree ")]
    return [path for path in paths if path.is_dir()]


def discover_worktrees(
    start: Path | None = None, runner: resource_limits.Runner = resource_limits.run_text
) -> list[Path]:
    primary = primary_clone(Path(__file__).resolve().parent if start is None else start, runner)
    if primary is None:
        return []
    return list_worktrees(primary, runner) or [primary]


def held_tree_pids(
    lock_path: Path, sampler: Callable[[int], process_tree.TreeSample | None] = process_tree.sample_tree
) -> frozenset[int]:
    holder = heavy_status.read_holder_state(lock_path)
    if holder is None or not holder.live or holder.pid is None:
        return frozenset()
    sample = sampler(holder.pid)
    if sample is None:
        return frozenset({holder.pid})
    return frozenset(member.pid for member in sample.members)


class SentinelLog:
    def __init__(
        self,
        path: Path | None,
        *,
        max_bytes: int = LOG_MAX_BYTES,
        backups: int = LOG_BACKUPS,
        err: IO[str] | None = None,
    ) -> None:
        stream = sys.stderr if err is None else err
        self._err = stream if err is not None or stream.isatty() else None
        self._logger = logging.Logger(f"benchbox.sentinel.{id(self)}")
        self._handler: logging.handlers.RotatingFileHandler | None = None
        if path is not None:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                self._handler = logging.handlers.RotatingFileHandler(
                    path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8"
                )
                self._handler.setFormatter(logging.Formatter("%(message)s"))
                self._logger.addHandler(self._handler)
            except OSError as exc:
                (self._err or sys.stderr).write(f"{LOG_PREFIX} cannot open log {path}: {exc}\n")

    def write(self, message: str) -> None:
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {LOG_PREFIX} {message}"
        if self._err is not None:
            self._err.write(line + "\n")
            self._err.flush()
        if self._handler is not None:
            try:
                self._logger.info(line)
            except Exception:
                pass

    def close(self) -> None:
        if self._handler is not None:
            self._logger.removeHandler(self._handler)
            self._handler.close()
            self._handler = None


class Sentinel:
    def __init__(
        self,
        policy: SentinelPolicy,
        *,
        log: Callable[[str], None],
        process_reader: Callable[[], Sequence[ProcInfo]] = read_processes,
        footprint: Callable[[int], int | None] = pid_footprint_bytes,
        worktree_lister: Callable[[], Sequence[Path]] = discover_worktrees,
        pressure_reader: Callable[[], int | None] = heavy_watchdog.read_pressure_level,
        holder_reader: Callable[[], frozenset[int]] = lambda: frozenset(),
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = mono_time,
        git_reader: Callable[[Sequence[str]], Sequence[git_flood.GitProc]] = git_flood.read_git_processes,
        total_ram: Callable[[], int | None] = git_flood.total_ram_bytes,
        environ: Mapping[str, str] | None = None,
        exclude: Sequence[int] = (),
    ) -> None:
        self.policy = policy
        self._log = log
        self._process_reader = process_reader
        self._footprint = footprint
        self._worktree_lister = worktree_lister
        self._pressure_reader = pressure_reader
        self._holder_reader = holder_reader
        self._sleeper = sleeper
        self._clock = clock
        self._exclude = frozenset({os.getpid(), *exclude})
        self._git_reader = git_reader
        self._total_ram = total_ram
        self._environ = os.environ if environ is None else environ
        self._roots: tuple[str, ...] = ()
        self._root_count = 0
        self._roots_at: float | None = None
        self._summary_key: tuple[frozenset[int], int, bool] | None = None
        self._flooded = False
        self._reported: tuple[int, float] | None = None

    def _refresh_roots(self) -> None:
        now = self._clock()
        if self._roots_at is not None and elapsed_seconds(self._roots_at, now) < self.policy.worktree_refresh_seconds:
            return
        self._roots_at = now
        try:
            worktrees = list(self._worktree_lister())
        except Exception as exc:
            self._log(f"could not list worktrees: {exc}; keeping {self._root_count} known checkouts")
            return
        if not worktrees:
            self._log("worktree listing returned no checkouts; keeping the previous set")
            return
        roots = _root_variants(worktrees)
        if roots != self._roots:
            self._log(f"watching {len(worktrees)} checkouts (first: {worktrees[0]})")
        self._roots = roots
        self._root_count = len(worktrees)

    def _measure(self, procs: Sequence[ProcInfo], held: frozenset[int]) -> tuple[list[Measured], int]:
        measured: list[Measured] = []
        unreadable = 0
        for proc in procs:
            if proc.pid in self._exclude or not is_candidate(proc):
                continue
            if proc.cwd is None:
                unreadable += 1
            scope = locate(proc, self._roots)
            if scope is None:
                continue
            size = self._footprint(proc.pid)
            if size is None:
                continue
            measured.append(Measured(proc, size, scope, proc.pid in held))
        return measured, unreadable

    def _summarize(self, measured: Sequence[Measured], unreadable: int, pressure: int, flooded: bool) -> None:
        budget_bytes = self.policy.budget_bytes
        heavy = frozenset(
            m.proc.pid for m in measured if budget_bytes is not None and m.footprint_bytes * 2 > budget_bytes
        )
        key = (heavy, pressure, flooded)
        if key == self._summary_key:
            return
        self._summary_key = key
        budget = "unmeasured" if budget_bytes is None else f"{budget_bytes / GIB:.2f} GiB"
        total = sum(m.footprint_bytes for m in measured) / GIB
        self._log(
            f"mode {self.policy.mode}; budget {budget}; pressure level {pressure}; {len(measured)} BenchBox-rooted "
            f"processes {total:.2f} GiB; cwd unreadable for {unreadable} candidate processes"
        )
        for item in sorted(measured, key=lambda m: m.footprint_bytes, reverse=True)[:TOP_COUNT]:
            self._log(self._describe(item))

    @staticmethod
    def _describe(item: Measured) -> str:
        command = " ".join(item.proc.cmdline)[:COMMAND_WIDTH]
        holder = " [lock holder tree]" if item.held else ""
        return (
            f"  pid {item.proc.pid} {item.proc.name} {item.footprint_bytes / GIB:.2f} GiB by {item.scope}"
            f"{holder} cwd {item.proc.cwd or '?'} cmd {command}"
        )

    def _verified(self, proc: ProcInfo) -> psutil.Process | None:
        try:
            live = psutil.Process(proc.pid)
            return live if abs(live.create_time() - proc.created) <= CREATE_TIME_TOLERANCE else None
        except (psutil.Error, OSError):
            return None

    def _send(self, proc: ProcInfo, sig: signal.Signals) -> None:
        live = self._verified(proc)
        if live is None:
            self._log(f"skipped {sig.name} for pid {proc.pid} ({proc.name}): no longer the sampled process")
            return
        try:
            live.send_signal(sig)
            self._log(f"sent {sig.name} to pid {proc.pid} ({proc.name})")
        except (psutil.Error, OSError) as exc:
            self._log(f"could not send {sig.name} to pid {proc.pid}: {exc}")

    def _act(self, offender: Measured) -> None:
        proc = offender.proc
        self._send(proc, SIG_TERM)
        deadline = self._clock() + self.policy.grace_seconds
        while not process_tree.is_gone(proc.pid, proc.created):
            if self._clock() >= deadline:
                self._send(proc, SIG_KILL)
                return
            self._sleeper(0.1)

    def _check_git(self) -> bool:
        if not self._roots:
            return False
        try:
            procs = self._git_reader(self._roots)
            flood = git_flood.evaluate(procs, self._environ, total_ram=self._total_ram(), footprint=self._footprint)
        except Exception as exc:
            self._log(f"could not read git processes: {exc}")
            return self._flooded
        if flood.breached and not self._flooded:
            self._log(f"WARNING git flood: {'; '.join(flood.breaches)}; git is never signalled by the sentinel")
            for line in git_flood.describe(flood):
                self._log(line)
        elif self._flooded and not flood.breached:
            self._log("git flood cleared")
        self._flooded = flood.breached
        return flood.breached

    def step(self) -> None:
        if self.policy.mode == "off":
            return
        self._refresh_roots()
        pressure = self._pressure_reader() or 0
        measured, unreadable = self._measure(self._process_reader(), frozenset())
        held = self._holder_reader() if measured else frozenset()
        if held:
            measured = [Measured(m.proc, m.footprint_bytes, m.scope, m.proc.pid in held) for m in measured]
        evaluation = evaluate(measured, pressure, self.policy.budget_bytes)
        flooded = self._check_git()
        self._summarize(measured, unreadable, pressure, flooded)
        offender = evaluation.offender
        if offender is None:
            self._reported = None
            return
        key = (offender.proc.pid, offender.proc.created)
        action = "kill" if self.policy.mode == "kill" else "warn only"
        if self.policy.mode == "warn" and self._reported == key:
            return
        self._reported = key
        for breach in evaluation.breaches:
            self._log(f"BREACH {breach.signal}: measured {breach.measured}, limit {breach.limit}; action: {action}")
        self._log(self._describe(offender))
        if self.policy.mode == "kill":
            self._act(offender)

    def run(self, duration_seconds: float | None = None) -> None:
        started = self._clock()
        while duration_seconds is None or elapsed_seconds(started, self._clock()) < duration_seconds:
            try:
                self.step()
            except (psutil.Error, OSError) as exc:
                self._log(f"sample failed: {exc}")
            self._sleeper(self.policy.interval_seconds)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m benchbox.utils.host_lock sentinel")
    parser.add_argument("--duration", type=float, help="exit after this many seconds")
    parser.add_argument("--log", type=Path, help=f"log file (default: ~/.benchbox/{heavy_status.SENTINEL_LOG_NAME})")
    parser.add_argument("--interval", type=float, help="seconds between samples")
    parser.add_argument("--worktree", action="append", type=Path, help="watch only these checkouts (repeatable)")
    sub = parser.add_subparsers(dest="action")
    for action in ("install", "uninstall"):
        item = sub.add_parser(action, help=f"{action} the per-user LaunchAgent")
        item.add_argument("--launchctl", default="launchctl")
    sub.choices["install"].add_argument("--python")
    sub.choices["install"].add_argument("--repo", type=Path)
    return parser


def _mode(environ: Mapping[str, str]) -> str:
    raw = (environ.get(heavy_watchdog.MODE_ENV) or "").strip().lower()
    return raw if raw in heavy_watchdog.MODES else heavy_watchdog.DEFAULT_MODE


def _float(environ: Mapping[str, str], name: str, default: float, minimum: float = 0.0) -> float:
    raw = environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value == value and value >= minimum else default


def policy_from_env(environ: Mapping[str, str], args: argparse.Namespace) -> SentinelPolicy:
    budget = resource_limits.heavy_memory_budget_gib(environ)
    interval = args.interval
    if interval is None:
        interval = _float(environ, INTERVAL_ENV, DEFAULT_INTERVAL_SECONDS, minimum=0.05)
    return SentinelPolicy(
        mode=_mode(environ),
        budget_bytes=None if budget is None else int(budget * GIB),
        interval_seconds=max(0.05, interval),
        grace_seconds=_float(environ, heavy_watchdog.GRACE_ENV, heavy_watchdog.DEFAULT_GRACE_SECONDS),
    )


def claim_pid_file(pid_path: Path) -> bool:
    recorded = heavy_status.read_sentinel_pid(pid_path)
    if recorded is not None and recorded[0] != os.getpid() and host_lock.process_is_live(*recorded):
        return False
    try:
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.write_text(host_lock.process_identity() + "\n", encoding="utf-8")
    except OSError:
        pass
    return True


def release_pid_file(pid_path: Path) -> None:
    recorded = heavy_status.read_sentinel_pid(pid_path)
    if recorded is not None and recorded[0] == os.getpid():
        try:
            pid_path.unlink()
        except OSError:
            pass


def _terminate(signum: int, frame: object) -> None:
    raise SystemExit(0)


def run_sentinel(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    log_path = heavy_status.sentinel_log_path(environ)
    pid_path = heavy_status.sentinel_pid_path(environ)
    log = SentinelLog(log_path)
    if not claim_pid_file(pid_path):
        log.write(f"another sentinel is already running (pid file {pid_path}); exiting")
        log.close()
        return EXIT_ALREADY_RUNNING
    policy = policy_from_env(environ, args)
    lock_path = host_lock.lock_path(environ)
    lister: Callable[[], Sequence[Path]] = discover_worktrees
    if args.worktree:
        fixed = list(args.worktree)
        lister = lambda: fixed
    sentinel = Sentinel(
        policy,
        log=log.write,
        worktree_lister=lister,
        holder_reader=lambda: held_tree_pids(lock_path),
    )
    previous = signal.signal(SIG_TERM, _terminate)
    log.write(f"started pid {os.getpid()}; mode {policy.mode}; log {log_path}")
    if policy.budget_bytes is None:
        log.write("memory budget unmeasured; no process will be signalled")
    try:
        sentinel.run(args.duration)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        signal.signal(SIG_TERM, previous)
        log.write("stopped")
        release_pid_file(pid_path)
        log.close()
    return 0


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    env = dict(os.environ if environ is None else environ)
    if args.log is not None:
        env[heavy_status.SENTINEL_LOG_ENV] = str(args.log)
    if args.action in ("install", "uninstall"):
        from benchbox.utils import sentinel_launchd

        if args.action == "install":
            repo = args.repo or primary_clone(Path(__file__).resolve().parent) or Path.cwd()
            return sentinel_launchd.install(env, repo=repo, python=args.python, launchctl=args.launchctl)
        return sentinel_launchd.uninstall(env, launchctl=args.launchctl)
    return run_sentinel(args, env)


if __name__ == "__main__":
    raise SystemExit(main())
