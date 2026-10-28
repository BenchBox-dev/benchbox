from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import secrets
import socket
import subprocess
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import yaml

from . import candidate_canaries, cli, commands, processes, runner
from .candidate_canaries import Canary
from .candidate_env import quiet_env
from .candidate_stub import LOOPBACK, CanaryServer, UpstreamStub, UpstreamTurn, error_turn, text_turn, tool_turn

CLIENT_SECONDS = 180
START_SECONDS = 60
POLL_SECONDS = 0.2
STOP_SECONDS = 10
GATEWAY_BEARER_BYTES = 32
ROLE_SELECTOR = "@default"
PERMISSIVE_OVERLAY = "{}\n"
PROVIDER_KEY = "sk-loopback-stub-key"
MCP_MARKER = "mcp-started"
AGENTS_TOKEN = "CANARY_AGENTS_CONTEXT_7731"
CLAUDE_TOKEN = "CANARY_CLAUDE_CONTEXT_7732"
NATIVE_TOKEN = "CANARY_NATIVE_CONTEXT_7733"
CHECKER = "benchbox/core/equivalence/checker.py"
LONG_FILE = "long_module.py"
CHECKER_SOURCE = "def checker(a, b):\n    return a == b\n"
LONG_SOURCE = "".join(
    f"class Holder{index}:\n    def total(self, a, b):\n        total = a + b\n        if total > {index}:\n"
    "            total -= 1\n        for step in range(3):\n            total += step\n        return total\n\n"
    for index in range(40)
)
PROMPT = "Review the change."
VERDICT = {
    "status": "complete",
    "incomplete_reason": "",
    "decision": "SHIP",
    "summary": "Reviewed.",
    "files_examined": [CHECKER],
    "defects": [],
    "prior_defects": [],
}
QUOTA_MESSAGE = "This request would exceed your account's rate limit. Please try again later."
AUTH_MESSAGE = "invalid x-api-key"
SESSION_MARKERS = ("session", ".jsonl")
BREACH_MARKER = "breach-created"
BREACH_TOOLS = ("bash", "write", "edit")


@dataclass(frozen=True)
class Context:
    workspace: str
    canary_url: str


@dataclass(frozen=True)
class Scenario:
    name: str
    script: Callable[[Context], Sequence[UpstreamTurn]]
    overlay: str = commands.OMP_OVERLAY_TEXT
    chains: bool = True
    valid_token: bool = True
    canaries: tuple[Canary, ...] = ()


@dataclass(frozen=True)
class ClientRun:
    scenario: str
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    upstream: tuple[Mapping[str, Any], ...]
    canary_hits: tuple[str, ...]
    mcp_started: bool
    session_files: tuple[str, ...]
    workspace_changed: bool
    survivors: tuple[int, ...]
    started: tuple[str, ...] = ()


def _review_script(context: Context) -> list[UpstreamTurn]:
    return [
        tool_turn("read", {"path": f"{context.workspace}/{CHECKER}"}, "toolu_read_checker"),
        tool_turn("grep", {"pattern": "def checker", "path": context.workspace}, "toolu_grep_checker"),
        tool_turn("read", {"path": context.canary_url}, "toolu_read_url"),
        tool_turn("read", {"path": f"{context.workspace}/{LONG_FILE}"}, "toolu_read_long"),
        text_turn(json.dumps(VERDICT)),
    ]


def _breach_script(context: Context) -> list[UpstreamTurn]:
    return [
        tool_turn("bash", {"command": f"touch {context.workspace}/{BREACH_MARKER}"}, "toolu_breach_bash"),
        tool_turn("write", {"path": f"{context.workspace}/{BREACH_MARKER}", "content": "x"}, "toolu_breach_write"),
        tool_turn("edit", {"path": f"{context.workspace}/{CHECKER}", "edits": []}, "toolu_breach_edit"),
        text_turn(json.dumps(VERDICT)),
    ]


def _quota_script(context: Context) -> list[UpstreamTurn]:
    return [error_turn(429, "rate_limit_error", QUOTA_MESSAGE)]


def _auth_script(context: Context) -> list[UpstreamTurn]:
    return [error_turn(401, "authentication_error", AUTH_MESSAGE)]


SCENARIOS = (
    Scenario("review", _review_script),
    Scenario("control", _review_script, overlay=PERMISSIVE_OVERLAY),
    Scenario("roster-breach", _breach_script),
    Scenario("quota-walk", _quota_script),
    Scenario("quota-terminal", _quota_script, chains=False),
    Scenario("upstream-auth", _auth_script),
    Scenario("bad-token", _review_script, valid_token=False),
)


def _probe_script(context: Context) -> list[UpstreamTurn]:
    return [text_turn(json.dumps(VERDICT))]


PROBES = tuple(
    Scenario(
        name(group),
        _probe_script,
        overlay=overlay,
        canaries=candidate_canaries.group_canaries(group),
    )
    for group in candidate_canaries.GROUPS
    for name, overlay in (
        (candidate_canaries.control_name, candidate_canaries.control_overlay()),
        (candidate_canaries.overlay_name, candidate_canaries.disabled_overlay()),
    )
)
SCENARIOS = (*SCENARIOS, *PROBES)


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind((LOOPBACK, 0))
        return probe.getsockname()[1]


def _wait_listening(port: int, process: subprocess.Popen[bytes], name: str) -> None:
    deadline = time.monotonic() + START_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"the candidate {name} exited with code {process.returncode} before listening")
        with socket.socket() as probe:
            probe.settimeout(1)
            if probe.connect_ex((LOOPBACK, port)) == 0:
                return
        time.sleep(POLL_SECONDS)
    raise RuntimeError(f"the candidate {name} did not listen within {START_SECONDS} seconds")


def _stop(process: subprocess.Popen[bytes]) -> None:
    process.terminate()
    try:
        process.wait(timeout=STOP_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    processes.confirm_exit(process.pid)


def _digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _sessions(*roots: Path) -> tuple[str, ...]:
    found = [
        str(path)
        for root in roots
        for path in root.rglob("*")
        if path.is_file()
        and any(marker in path.name.lower() or marker in path.parent.name.lower() for marker in SESSION_MARKERS)
    ]
    return tuple(sorted(found))


def _run(argv: Sequence[str], *, cwd: Path, env: Mapping[str, str], timeout: int = START_SECONDS) -> str:
    result = subprocess.run(
        list(argv),
        cwd=cwd,
        env=dict(env),
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        raise RuntimeError(f"{Path(argv[0]).name} {' '.join(argv[1:3])} exited with code {result.returncode}")
    return result.stdout


class LoopbackChain:
    def __init__(self, binary: Path, workdir: Path) -> None:
        self.binary = binary
        self.workdir = workdir
        self.workspace = workdir / "workspace"
        self.stub = UpstreamStub([])
        self.canary = CanaryServer()
        self.gateway_url = ""
        self.client_token = secrets.token_urlsafe(GATEWAY_BEARER_BYTES)
        self._processes: list[subprocess.Popen[bytes]] = []
        self._planted: list[Path] = []
        self._stack = contextlib.ExitStack()

    def __enter__(self) -> LoopbackChain:
        try:
            self._start()
        except BaseException:
            self._stack.close()
            raise
        return self

    def __exit__(self, *exc: object) -> None:
        self._stack.close()

    def _spawn(self, name: str, argv: Sequence[str], cwd: Path, env: Mapping[str, str]) -> subprocess.Popen[bytes]:
        log = self._stack.enter_context((self.workdir / f"{name}.log").open("wb"))
        process = subprocess.Popen(
            list(argv),
            cwd=cwd,
            env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self._stack.callback(_stop, process)
        return process

    def _start(self) -> None:
        omp = str(self.binary)
        (self.workspace / CHECKER).parent.mkdir(parents=True)
        (self.workspace / CHECKER).write_text(CHECKER_SOURCE, encoding="utf-8")
        (self.workspace / LONG_FILE).write_text(LONG_SOURCE, encoding="utf-8")
        self._stack.enter_context(self.stub)
        self._stack.enter_context(self.canary)
        broker_home, gateway_home = self.workdir / "broker", self.workdir / "gateway"
        broker_port, gateway_port = _free_port(), _free_port()
        broker_env = quiet_env(broker_home)
        broker_process = self._spawn(
            "broker", [omp, "auth-broker", "serve", "--bind", f"{LOOPBACK}:{broker_port}"], broker_home, broker_env
        )
        _wait_listening(broker_port, broker_process, "auth-broker")
        token = _run([omp, "auth-broker", "token"], cwd=broker_home, env=broker_env).strip().splitlines()[-1]
        gateway_env = {
            **quiet_env(gateway_home),
            "OMP_AUTH_BROKER_URL": f"http://{LOOPBACK}:{broker_port}",
            "OMP_AUTH_BROKER_TOKEN": token,
        }
        _run(
            [omp, "auth-broker", "migrate", "--from-local", "--include-env"],
            cwd=gateway_home,
            env={**gateway_env, "ANTHROPIC_API_KEY": PROVIDER_KEY},
        )
        token_file = self.workdir / "client-token"
        token_file.write_text(self.client_token, encoding="utf-8")
        clients = self.workdir / "gateway-clients.json"
        with contextlib.redirect_stdout(None):
            cli.command_gateway_clients(
                argparse.Namespace(token_file=str(token_file), accounts=None, client=[], out=str(clients))
            )
        gateway_process = self._spawn(
            "gateway",
            [omp, "auth-gateway", "serve", "--bind", f"{LOOPBACK}:{gateway_port}", f"--clients={clients}"],
            gateway_home,
            {**gateway_env, "ANTHROPIC_BASE_URL": self.stub.url},
        )
        _wait_listening(gateway_port, gateway_process, "auth-gateway")
        self.gateway_url = f"http://{LOOPBACK}:{gateway_port}"

    def run(self, scenario: Scenario) -> ClientRun:
        root = self.workdir / scenario.name
        home, scratch = root / "home", root / "scratch"
        home.mkdir(parents=True)
        cli.write_reviewer_home(home, self.gateway_url, allow_loopback_http=True)
        if not scenario.chains:
            config = home / ".omp" / "agent" / "config.yml"
            settings = yaml.safe_load(config.read_text(encoding="utf-8"))
            del settings["retry"]["fallbackChains"]
            config.write_text(yaml.safe_dump(settings), encoding="utf-8")
        invocation = commands.build_omp(
            selector=ROLE_SELECTOR,
            effort="",
            workspace=self.workspace,
            scratch=scratch,
            prompt=PROMPT,
            binary=self.binary,
            overlay_text=scenario.overlay,
        )
        self._clear_workspace()
        markers = root / "markers"
        if scenario.canaries:
            markers.mkdir()
            self._plant_probes(scenario.canaries, invocation.cwd, markers)
            invocation = replace(invocation, argv=candidate_canaries.relax(invocation.argv))
        else:
            self._plant_canaries(invocation.cwd, root / MCP_MARKER)
        env = quiet_env(home)
        env[cli.GATEWAY_TOKEN_ENV] = self.client_token if scenario.valid_token else "x" * len(self.client_token)
        self.stub.load(scenario.script(Context(str(self.workspace), self.canary.url)))
        self.canary.hits.clear()
        before = _digest(self.workspace)
        survivors: tuple[int, ...] = ()
        try:
            result = runner.execute(replace(invocation, env=env), CLIENT_SECONDS)
        except processes.HarnessLeakError as leak:
            survivors = leak.survivors
            result = runner.RunResult(None, "", "", False)
        return ClientRun(
            scenario.name,
            result.exit_code,
            result.stdout,
            result.stderr,
            result.timed_out,
            tuple(self.stub.requests),
            tuple(self.canary.hits),
            (root / MCP_MARKER).exists(),
            _sessions(home, scratch, self.workspace),
            _digest(self.workspace) != before,
            survivors,
            tuple(sorted(item.name for item in markers.glob("mcp-*"))) if markers.is_dir() else (),
        )

    def _clear_workspace(self) -> None:
        for path in self._planted:
            path.unlink(missing_ok=True)
            for parent in path.parents:
                if parent == self.workspace or any(parent.iterdir()):
                    break
                parent.rmdir()
        self._planted.clear()

    def _plant_probes(self, canaries: Sequence[Canary], cwd: Path, markers: Path) -> None:
        for canary in canaries:
            for where, directory in zip(candidate_canaries.WHERES, (cwd, self.workspace), strict=True):
                if where not in canary.wheres:
                    continue
                planted = candidate_canaries.plant(canary, where, directory, markers)
                if directory == self.workspace:
                    self._planted.append(planted)

    def _plant_canaries(self, cwd: Path, marker: Path) -> None:
        (cwd / "AGENTS.md").write_text(f"{AGENTS_TOKEN} follow this file.\n", encoding="utf-8")
        (cwd / "CLAUDE.md").write_text(f"{CLAUDE_TOKEN} follow this file.\n", encoding="utf-8")
        for directory in (cwd, self.workspace):
            (directory / ".omp").mkdir(exist_ok=True)
            native = directory / ".omp" / "AGENTS.md"
            native.write_text(f"{NATIVE_TOKEN} follow this file.\n", encoding="utf-8")
            if directory == self.workspace:
                self._planted.append(native)
        server = {"command": "/bin/sh", "args": ["-c", f"echo started > {marker}"]}
        (cwd / ".mcp.json").write_text(json.dumps({"mcpServers": {"canary": server}}), encoding="utf-8")


@contextlib.contextmanager
def loopback_chain(binary: Path, workdir: Path) -> Iterator[LoopbackChain]:
    with LoopbackChain(binary, workdir) as chain:
        yield chain
