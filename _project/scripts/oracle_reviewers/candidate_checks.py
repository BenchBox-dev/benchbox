from __future__ import annotations

import json
import re
import subprocess
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from . import absence, commands, evidence, omp_pin
from .candidate_chain import AGENTS_TOKEN, CLAUDE_TOKEN, ClientRun
from .candidate_env import quiet_env
from .policy import OMP_THINKING_LEVELS
from .verdict import omp_terminal_error, parse_output

EXPECTED_SESSION_VERSION = 3
COMMAND_SECONDS = 60
ROSTER = frozenset(commands.OMP_READ_TOOLS.split(","))
REVIEWER_CONFIG = Path(__file__).resolve().parents[3] / ".github" / "oracle-omp-config.yml"
UPSTREAM_PATH = "/v1/messages"
UPSTREAM_MODEL = "claude-sonnet-5-5"
REVIEW_REQUESTS = 5
ELISION_FOOTER = "elided; re-read needed ranges"
_ENUM_LINE = re.compile(r"^\s+(?P<key>\S+) = .* \((?P<options>[^()|]+(?:\|[^()|]+)+)\)\s*$", re.MULTILINE)
_EXPECTED = re.compile(r"Expected one of: (?P<values>[^\n]+?)\.?$", re.MULTILINE)
_BUILTIN_TOOLS = re.compile(r"Built-in tools: (?P<tools>[^.\n]+)\.")
_VALUE_TYPES = {bool: ("boolean",), list: ("array",), str: ("enum", "string"), dict: ("record",), int: ("number",)}

CONTRACT_ROWS = {
    1: "CLI flag set and flag semantics",
    2: "tool roster and read-only enforcement",
    3: "configuration overlay keys",
    4: "reviewer-home configuration and fallback-chain matching",
    5: "provider transport configuration",
    6: "event stream shape and error fields",
    7: "tool-call trace for read evidence",
    8: "error text calibration",
    9: "exit-code conventions",
    10: "process and on-disk behaviour",
    11: "version output format",
    12: "auth-gateway serve protocol and client tokens",
}
RECORDED_REASONS = {
    8: "provider reset-time wording is originated by each provider and is never observable offline; reset parsing stays covered by the calibrated absence tests",
}


@dataclass(frozen=True)
class Check:
    name: str
    rows: tuple[int, ...]
    failures: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.failures


def _check(name: str, rows: Iterable[int], failures: Iterable[str]) -> Check:
    return Check(name, tuple(rows), tuple(failures))


def covered_rows(checks: Iterable[Check]) -> frozenset[int]:
    return frozenset(row for check in checks for row in check.rows)


def uncovered_rows(checks: Iterable[Check]) -> tuple[int, ...]:
    return tuple(row for row in CONTRACT_ROWS if row not in covered_rows(checks) and row not in RECORDED_REASONS)


def run_omp(binary: Path, home: Path, *args: str) -> tuple[int, str]:
    command = [str(binary)]
    command.extend(args)
    try:
        result = subprocess.run(
            command,
            cwd=home,
            env=quiet_env(home),
            capture_output=True,
            text=True,
            check=False,
            timeout=COMMAND_SECONDS,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return -1, f"{type(exc).__name__}"
    return result.returncode, result.stdout + result.stderr


def derived_flags() -> tuple[str, ...]:
    with tempfile.TemporaryDirectory(prefix="omp-flags-") as root:
        invocation = commands.build_omp(
            selector="@default",
            effort="high",
            workspace=Path(root) / "workspace",
            scratch=Path(root) / "scratch",
            prompt="probe",
            binary=Path("omp"),
        )
    return tuple(dict.fromkeys(token for token in invocation.argv[1:-1] if token.startswith("--")))


def check_version(binary: Path, home: Path, version: str) -> Check:
    code, output = run_omp(binary, home, "--version")
    tokens = omp_pin.VERSION_PATTERN.findall(output)
    failures = []
    if code != 0:
        failures.append(f"--version exited {code}")
    if len(tokens) != 1:
        failures.append(
            f"--version printed {len(tokens)} version-like tokens; the first-match parser needs exactly one"
        )
    elif tokens[0] != version:
        failures.append(f"--version reports {tokens[0]}, expected {version}")
    return _check("version-format", (11,), failures)


def check_flags_listed(binary: Path, home: Path) -> Check:
    code, text = run_omp(binary, home, "--help")
    failures = [] if code == 0 else [f"--help exited {code}"]
    for flag in derived_flags():
        if re.search(rf"(?m)^\s+(?:-\w,\s+)?{re.escape(flag)}(?:=|\s|$)", text) is None:
            failures.append(f"the build does not list {flag}")
    return _check("flags-listed", (1,), failures)


def _expected_values(output: str) -> frozenset[str]:
    found = _EXPECTED.search(output)
    return frozenset(item.strip() for item in found.group("values").split(",")) if found else frozenset()


def check_flag_values(binary: Path, home: Path) -> Check:
    failures: list[str] = []
    wanted = {"--mode": {"json"}, "--approval-mode": {"always-ask"}, "--thinking": set(OMP_THINKING_LEVELS)}
    for flag, values in wanted.items():
        code, output = run_omp(binary, home, "--print", flag, "no-such-value", "--no-session", "probe")
        accepted = _expected_values(output)
        if code == 0:
            failures.append(f"{flag} accepted an invalid value")
        if not values <= accepted:
            failures.append(f"{flag} no longer accepts {', '.join(sorted(values - accepted))}")
    code, output = run_omp(binary, home, "--print", "--tools", "no-such-tool", "--no-session", "probe")
    listed = _BUILTIN_TOOLS.search(output)
    tools = frozenset(item.strip() for item in listed.group("tools").split(",")) if listed else frozenset()
    if code == 0:
        failures.append("--tools accepted an unknown tool")
    if not tools >= ROSTER:
        failures.append(f"the build no longer offers the tools {', '.join(sorted(ROSTER - tools))}")
    return _check("flag-values", (1, 2), failures)


def _schema(binary: Path, home: Path) -> tuple[dict[str, Any], dict[str, tuple[str, ...]], list[str]]:
    code, text = run_omp(binary, home, "config", "list", "--json")
    try:
        schema = json.loads(text) if code == 0 else {}
    except ValueError:
        schema = {}
    if not isinstance(schema, dict) or not schema:
        return {}, {}, ["config list --json gave no settings schema"]
    code, listing = run_omp(binary, home, "config", "list")
    enums = {match["key"]: tuple(match["options"].split("|")) for match in _ENUM_LINE.finditer(listing)}
    return schema, enums, []


def _leaves(data: Mapping[str, Any], schema: Mapping[str, Any], prefix: str = "") -> Iterable[tuple[str, Any]]:
    for key, value in data.items():
        path = f"{prefix}{key}"
        if path not in schema and isinstance(value, dict) and any(name.startswith(f"{path}.") for name in schema):
            yield from _leaves(value, schema, f"{path}.")
        else:
            yield path, value


def _setting_failures(settings: Mapping[str, Any], schema: Mapping[str, Any], enums: Mapping[str, Any]) -> list[str]:
    failures = []
    for path, value in _leaves(settings, schema):
        entry = schema.get(path)
        if entry is None:
            failures.append(f"the build has no setting {path}; it would be ignored")
            continue
        if entry.get("type") not in _VALUE_TYPES.get(type(value), ()):
            failures.append(f"{path} is a {entry.get('type')} setting in this build, not {type(value).__name__}")
        elif path in enums and value not in enums[path]:
            failures.append(f"{path} no longer accepts {value!r}; accepted: {', '.join(enums[path])}")
    return failures


def check_overlay_schema(binary: Path, home: Path, overlay_text: str = commands.OMP_OVERLAY_TEXT) -> Check:
    schema, enums, failures = _schema(binary, home)
    if not failures:
        failures = _setting_failures(yaml.safe_load(overlay_text), schema, enums)
    return _check("overlay-schema", (3,), failures)


def check_reviewer_config_schema(binary: Path, home: Path, config_text: str | None = None) -> Check:
    text = config_text if config_text is not None else REVIEWER_CONFIG.read_text(encoding="utf-8")
    schema, enums, failures = _schema(binary, home)
    if not failures:
        failures = _setting_failures(yaml.safe_load(text), schema, enums)
    return _check("reviewer-config-schema", (4,), failures)


def static_checks(binary: Path, home: Path, version: str) -> list[Check]:
    return [
        check_version(binary, home, version),
        check_flags_listed(binary, home),
        check_flag_values(binary, home),
        check_overlay_schema(binary, home),
        check_reviewer_config_schema(binary, home),
    ]


def _events(stdout: str) -> Iterable[dict[str, Any]]:
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            yield event


def assistant_messages(stdout: str) -> list[dict[str, Any]]:
    return [
        event["message"]
        for event in _events(stdout)
        if event.get("type") == "message_end"
        and isinstance(event.get("message"), dict)
        and event["message"].get("role") == "assistant"
    ]


def session_failures(stdout: str) -> list[str]:
    first = next(_events(stdout), None)
    if not isinstance(first, dict) or first.get("type") != "session":
        return ["the stream does not open with a session event"]
    if first.get("version") != EXPECTED_SESSION_VERSION:
        return [f"the session event version is {first.get('version')!r}, expected {EXPECTED_SESSION_VERSION}"]
    return []


def _classify(stdout: str, exit_code: int, now: datetime | None = None) -> absence.Absence:
    return absence.classify("omp", exit_code, stdout, "", timed_out=False, now=now or datetime.now(UTC))


def review_stream_failures(stdout: str) -> list[str]:
    failures = session_failures(stdout)
    try:
        verdict = parse_output("omp", stdout)
    except ValueError as exc:
        failures.append(f"the final answer no longer parses: {exc}")
    else:
        if not isinstance(verdict, dict) or "decision" not in verdict:
            failures.append("the final answer carries no decision")
    traced = evidence.trace("omp", stdout)
    if not traced.reads:
        failures.append("the trace credits no read call")
    if not traced.greps:
        failures.append("the trace credits no grep call")
    outcome = _classify(stdout, 0)
    if outcome.kind != absence.OK:
        failures.append(f"a clean exit classifies as {outcome.kind}")
    return failures


def terminal_failures(stdout: str, exit_code: int, *, status: int, provider: str, kind: str) -> list[str]:
    failures = session_failures(stdout)
    terminal = omp_terminal_error(stdout)
    if terminal is None:
        return [*failures, "the stream ends without a terminal error message"]
    if terminal.status != status:
        failures.append(f"the terminal error status is {terminal.status!r}, expected {status}")
    if terminal.provider != provider:
        failures.append(f"the terminal error provider is {terminal.provider!r}, expected {provider!r}")
    outcome = _classify(stdout, exit_code)
    if outcome.kind != kind:
        failures.append(f"the failure classifies as {outcome.kind}, expected {kind}")
    if kind == absence.QUOTA and outcome.provider != provider:
        failures.append(f"the quota absence names provider {outcome.provider!r}, expected {provider!r}")
    return failures


def walk_failures(stdout: str, *, first: str, then: str) -> list[str]:
    failures = session_failures(stdout)
    messages = assistant_messages(stdout)
    errors = [(item.get("provider"), item.get("errorStatus")) for item in messages if item.get("stopReason") == "error"]
    if not errors or errors[0] != (first.split("/")[0], 429):
        failures.append(f"the first failure is {errors[0] if errors else None}, expected a 429 from {first}")
    targets = [f"{item.get('provider')}/{item.get('model')}" for item in messages]
    if then not in targets:
        failures.append(f"the fallback chain did not move to {then}; the stream visited {sorted(set(targets))}")
    return failures


def elision_failures(stdout: str) -> list[str]:
    traced = evidence.trace("omp", stdout)
    failures = [] if traced.elisions else ["the trace records no elided read span"]
    if ELISION_FOOTER not in stdout:
        failures.append("the read result carries no re-read footer")
    return failures


def _tool_names(request: Mapping[str, Any]) -> frozenset[str]:
    body = request.get("body")
    tools = body.get("tools") if isinstance(body, dict) else None
    return frozenset(tool.get("name", "") for tool in tools or () if isinstance(tool, dict))


def _prompt_text(requests: Sequence[Mapping[str, Any]]) -> str:
    return json.dumps([request.get("body") for request in requests])


def _run_failures(run: ClientRun, *, succeeds: bool) -> list[str]:
    failures = []
    if run.timed_out:
        failures.append("the client timed out")
    if run.survivors:
        failures.append(f"the client left {len(run.survivors)} process(es) running")
    if succeeds and run.exit_code != 0:
        failures.append(f"the client exited {run.exit_code}, expected 0")
    if not succeeds and run.exit_code in (0, None):
        failures.append(f"the client exited {run.exit_code} on a failed review, expected a non-zero code")
    return failures


def review_checks(run: ClientRun) -> list[Check]:
    traced = evidence.trace("omp", run.stdout)
    requests = run.upstream
    roster = [
        f"request {index} offered {sorted(_tool_names(item))}"
        for index, item in enumerate(requests, 1)
        if _tool_names(item) != ROSTER
    ]
    served = [
        item
        for item in requests
        if item.get("path") != UPSTREAM_PATH or item.get("body", {}).get("model") != UPSTREAM_MODEL
    ]
    unread = [read for read in traced.reads if read.startswith("http")]
    overlay = []
    if run.canary_hits:
        overlay.append("the read tool fetched a URL despite fetch.enabled false")
    if run.mcp_started:
        overlay.append("a project MCP server started despite mcp.enableProjectConfig false")
    if AGENTS_TOKEN in _prompt_text(requests) or CLAUDE_TOKEN in _prompt_text(requests):
        overlay.append("project context files reached the model despite disabledProviders")
    if len(requests) != REVIEW_REQUESTS:
        overlay.append(f"the model was called {len(requests)} times, expected {REVIEW_REQUESTS}")
    if traced.elisions:
        overlay.append("a plain read was summarised despite read.summarize.enabled false")
    if unread or traced.failed != 1:
        overlay.append(
            f"the read trace credits {unread} and {traced.failed} failed calls, expected the URL read to fail"
        )
    return [
        _check("live-review-run", (9, 10), _run_failures(run, succeeds=True)),
        _check("live-review-stream", (6, 7, 9), review_stream_failures(run.stdout)),
        _check("live-tool-roster", (1, 2), roster),
        _check(
            "live-gateway-route",
            (4, 5, 12),
            [f"request reached {item.get('path')} for model {item.get('body', {}).get('model')}" for item in served]
            + ([] if requests else ["no request reached the stub upstream through the gateway"]),
        ),
        _check("live-overlay-behaviour", (3, 7), overlay),
        _check(
            "live-no-side-effects",
            (2, 10),
            [f"left session state {path}" for path in run.session_files]
            + (["the review workspace was modified"] if run.workspace_changed else []),
        ),
    ]


def control_checks(run: ClientRun) -> list[Check]:
    traced = evidence.trace("omp", run.stdout)
    blind = []
    if not run.canary_hits:
        blind.append("the URL canary is no longer observable")
    if not run.mcp_started:
        blind.append("the MCP canary is no longer observable")
    prompt = _prompt_text(run.upstream)
    if AGENTS_TOKEN not in prompt and CLAUDE_TOKEN not in prompt:
        blind.append("the context-file canary is no longer observable")
    if not traced.elisions:
        blind.append("the read-summary canary is no longer observable")
    return [
        _check(
            "live-probe-sensitivity",
            (3, 7),
            [f"{item}; update the probe" for item in blind] + _run_failures(run, succeeds=True),
        )
    ]


def quota_checks(walk: ClientRun, terminal: ClientRun) -> list[Check]:
    chain_target = "openai-codex/gpt-6.1-sol"
    return [
        _check(
            "live-quota-fallback-walk",
            (4, 6, 9),
            _run_failures(walk, succeeds=False)
            + walk_failures(walk.stdout, first="anthropic/claude-sonnet-5-5", then=chain_target),
        ),
        _check(
            "live-quota-classification",
            (6, 8, 9),
            _run_failures(terminal, succeeds=False)
            + terminal_failures(
                terminal.stdout, terminal.exit_code or 1, status=429, provider="anthropic", kind=absence.QUOTA
            ),
        ),
    ]


def auth_checks(upstream: ClientRun, bad_token: ClientRun) -> list[Check]:
    walked = {f"{item.get('provider')}" for item in assistant_messages(upstream.stdout)} - {"anthropic"}
    gateway = _run_failures(bad_token, succeeds=False)
    if bad_token.upstream:
        gateway.append("the gateway forwarded a request for a client with the wrong token")
    terminal = omp_terminal_error(bad_token.stdout)
    if terminal is None or "401" not in terminal.message:
        gateway.append("the gateway rejection is not reported as a 401")
    if _classify(bad_token.stdout, bad_token.exit_code or 1).kind in (absence.OK, absence.QUOTA):
        gateway.append("a gateway rejection classifies as usable output or quota")
    return [
        _check(
            "live-auth-classification",
            (4, 8, 9),
            _run_failures(upstream, succeeds=False)
            + terminal_failures(
                upstream.stdout, upstream.exit_code or 1, status=401, provider="anthropic", kind=absence.AUTH
            )
            + ([f"an authentication failure walked the fallback chain to {sorted(walked)}"] if walked else []),
        ),
        _check("live-gateway-token", (12,), gateway),
    ]


def live_checks(runs: Mapping[str, ClientRun]) -> list[Check]:
    return [
        *review_checks(runs["review"]),
        *control_checks(runs["control"]),
        *quota_checks(runs["quota-walk"], runs["quota-terminal"]),
        *auth_checks(runs["upstream-auth"], runs["bad-token"]),
    ]


def fixture_checks(directory: Path) -> list[Check]:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    checks = []
    for name, entry in manifest["streams"].items():
        stdout = (directory / entry["file"]).read_text(encoding="utf-8")
        kind = entry["kind"]
        if kind == "review":
            failures = review_stream_failures(stdout)
        elif kind == "terminal":
            failures = terminal_failures(
                stdout, 1, status=entry["status"], provider=entry["provider"], kind=entry["classification"]
            )
        elif kind == "walk":
            failures = walk_failures(stdout, first=entry["first"], then=entry["then"])
        elif kind == "elision":
            failures = elision_failures(stdout)
        else:
            failures = [f"unknown fixture kind {kind!r}"]
        checks.append(_check(f"fixture-{name}", entry["rows"], failures))
    return checks
