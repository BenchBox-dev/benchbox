from __future__ import annotations

import copy
import json
import stat
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import yaml

from _project.scripts.oracle_reviewers import (
    candidate_chain,
    candidate_checks,
    candidate_stub,
    commands,
    omp_candidate,
    omp_pin,
)
from _project.scripts.oracle_reviewers.candidate_chain import AGENTS_TOKEN, ClientRun

pytestmark = [pytest.mark.unit, pytest.mark.fast]

FIXTURES = omp_candidate.FIXTURE_DIR
STATIC = json.loads((FIXTURES / "static_outputs.json").read_text(encoding="utf-8"))
VERSION = "18.12.1"
SCRIPT = (
    "#!{python}\n"
    "import json, sys\n"
    "from pathlib import Path\n"
    "data = json.loads(Path({data!r}).read_text())\n"
    "Path({marker!r}).touch()\n"
    "args = sys.argv[1:]\n"
    "def emit(text, code=0):\n"
    "    sys.stdout.write(text)\n"
    "    raise SystemExit(code)\n"
    "if args == ['--version']:\n"
    "    emit(data['version'])\n"
    "if args == ['--help']:\n"
    "    emit(data['help'])\n"
    "if args[:2] == ['config', 'list']:\n"
    "    emit(json.dumps(data['config_json']) if '--json' in args else data['config_text'])\n"
    "if args[:1] == ['--print']:\n"
    "    flag = args[1]\n"
    "    if flag in data['invalid']:\n"
    "        emit(data['invalid'][flag], 2)\n"
    "emit('', 0)\n"
)


def _fake(tmp_path: Path, mutate: Callable[[dict[str, Any]], None] | None = None) -> tuple[Path, Path]:
    data = copy.deepcopy(STATIC)
    if mutate is not None:
        mutate(data)
    directory = tmp_path / "bin"
    directory.mkdir(exist_ok=True)
    (directory / "static.json").write_text(json.dumps(data), encoding="utf-8")
    binary = directory / "omp"
    providers = yaml.safe_load(commands.OMP_OVERLAY_TEXT)["disabledProviders"]
    script = SCRIPT.format(
        python=sys.executable, data=str(directory / "static.json"), marker=str(directory / "executed")
    )
    binary.write_text(script + "# " + " ".join(f'"{item}"' for item in providers) + "\n", encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return binary, home


def _static(
    tmp_path: Path, mutate: Callable[[dict[str, Any]], None] | None = None
) -> dict[str, candidate_checks.Check]:
    binary, home = _fake(tmp_path, mutate)
    return {check.name: check for check in candidate_checks.static_checks(binary, home, VERSION)}


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _request(tools: tuple[str, ...] = ("read", "grep", "glob"), prompt: str = "system") -> dict[str, Any]:
    return {
        "path": "/v1/messages",
        "body": {"model": "claude-sonnet-5-5", "system": prompt, "tools": [{"name": name} for name in tools]},
    }


def _run(name: str, stdout: str, exit_code: int | None = 0, **fields: Any) -> ClientRun:
    base = {
        "scenario": name,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": "",
        "timed_out": False,
        "upstream": (),
        "canary_hits": (),
        "mcp_started": False,
        "session_files": (),
        "workspace_changed": False,
        "survivors": (),
    }
    return ClientRun(**{**base, **fields})


def _good_runs() -> dict[str, ClientRun]:
    return {
        "review": _run("review", _read("chain_review.jsonl"), upstream=tuple(_request() for _ in range(5))),
        "control": _run(
            "control",
            _read("elided_read.jsonl"),
            canary_hits=("/canary",),
            mcp_started=True,
            upstream=(_request(prompt=f"context {AGENTS_TOKEN}"),),
        ),
        "roster-breach": _run(
            "roster-breach", _read("roster_breach.jsonl"), upstream=tuple(_request() for _ in range(4))
        ),
        "quota-walk": _run("quota-walk", _read("quota_walk.jsonl"), 1, upstream=(_request(),)),
        "quota-terminal": _run("quota-terminal", _read("quota_terminal.jsonl"), 1, upstream=(_request(),)),
        "upstream-auth": _run("upstream-auth", _read("upstream_auth.jsonl"), 1, upstream=(_request(),)),
        "bad-token": _run("bad-token", _read("gateway_rejection.jsonl"), 1),
    }


def _failed(checks: list[candidate_checks.Check]) -> dict[str, tuple[str, ...]]:
    return {check.name: check.failures for check in checks if not check.ok}


def test_a_build_with_the_documented_surface_passes_every_static_probe(tmp_path: Path) -> None:
    checks = _static(tmp_path)
    assert {name: check.failures for name, check in checks.items() if not check.ok} == {}


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (lambda data: data.update(version="omp/18.12.1 (build 18.12.2)"), "2 version-like tokens"),
        (lambda data: data.update(version="omp/18.13.0"), "reports 18.13.0, expected 18.12.1"),
        (lambda data: data.update(version="no version here"), "0 version-like tokens"),
    ],
)
def test_a_version_output_the_first_match_parser_could_misread_is_red(tmp_path: Path, mutation, expected: str) -> None:
    failures = _static(tmp_path, mutation)["version-format"].failures
    assert any(expected in item for item in failures)


def test_a_removed_flag_is_named_by_the_flag_probe(tmp_path: Path) -> None:
    def remove(data: dict[str, Any]) -> None:
        data["help"] = "\n".join(line for line in data["help"].splitlines() if "--no-title" not in line)

    failures = _static(tmp_path, remove)["flags-listed"].failures
    assert failures == ("the build does not list --no-title",)


def test_every_flag_the_reviewer_command_passes_has_a_listing_probe() -> None:
    assert {
        "--cwd",
        "--add-dir",
        "--print",
        "--mode",
        "--model",
        "--thinking",
        "--tools",
        "--no-extensions",
        "--no-lsp",
        "--no-skills",
        "--no-rules",
        "--approval-mode",
        "--no-title",
        "--no-session",
        "--config",
    } == set(candidate_checks.derived_flags())


@pytest.mark.parametrize(
    ("flag", "needle"),
    [
        ("--approval-mode", "--approval-mode no longer accepts always-ask"),
        ("--mode", "--mode no longer accepts json"),
    ],
)
def test_an_enumeration_that_drops_a_value_the_reviewer_uses_is_red(tmp_path: Path, flag: str, needle: str) -> None:
    def drop(data: dict[str, Any]) -> None:
        data["invalid"][flag] = data["invalid"][flag].replace("always-ask, ", "").replace("json, ", "")

    assert any(needle in item for item in _static(tmp_path, drop)["flag-values"].failures)


def test_a_build_that_accepts_an_invalid_flag_value_or_gains_no_read_tools_is_red(tmp_path: Path) -> None:
    def loosen(data: dict[str, Any]) -> None:
        del data["invalid"]["--mode"]
        data["invalid"]["--tools"] = "Unknown tool. Built-in tools: read, grep."

    failures = _static(tmp_path, loosen)["flag-values"].failures
    assert "--mode accepted an invalid value" in failures
    assert "the build no longer offers the tools glob" in failures


def test_an_overlay_key_the_build_does_not_know_is_reported_as_silently_ignored(tmp_path: Path) -> None:
    def forget(data: dict[str, Any]) -> None:
        del data["config_json"]["fetch.enabled"]

    assert _static(tmp_path, forget)["overlay-schema"].failures == (
        "the build has no setting fetch; it would be ignored",
    )


def test_an_overlay_value_the_build_no_longer_accepts_is_red(tmp_path: Path) -> None:
    def tighten(data: dict[str, Any]) -> None:
        data["config_text"] = data["config_text"].replace("(off|suggest|on)", "(suggest|on)")
        data["config_json"]["mcp.enableProjectConfig"]["type"] = "string"

    failures = _static(tmp_path, tighten)["overlay-schema"].failures
    assert any("prewalk.auto no longer accepts 'off'" in item for item in failures)
    assert any("mcp.enableProjectConfig is a string setting in this build, not bool" in item for item in failures)


def test_a_build_without_a_listable_settings_schema_is_red_for_both_configuration_probes(tmp_path: Path) -> None:
    checks = _static(tmp_path, lambda data: data.update(config_json={}))
    assert checks["overlay-schema"].failures == ("config list --json gave no settings schema",)
    assert checks["reviewer-config-schema"].failures == ("config list --json gave no settings schema",)


def test_an_enum_setting_whose_accepted_values_cannot_be_listed_is_red_not_waved_through(tmp_path: Path) -> None:
    failures = _static(tmp_path, lambda data: data.update(config_text=""))["overlay-schema"].failures
    assert "the build lists no accepted values for the enum setting prewalk.auto" in failures


def test_a_retry_policy_value_the_build_no_longer_accepts_is_red(tmp_path: Path) -> None:
    def tighten(data: dict[str, Any]) -> None:
        data["config_text"] = data["config_text"].replace(
            "(any|usage-limit|except-usage-limit)", "(any|except-usage-limit)"
        )

    failures = _static(tmp_path, tighten)["reviewer-config-schema"].failures
    assert any("retry.fallbackOn no longer accepts 'usage-limit'" in item for item in failures)


def test_a_renamed_chain_key_setting_in_the_reviewer_config_is_reported(tmp_path: Path) -> None:
    binary, home = _fake(tmp_path)
    text = (candidate_checks.REVIEWER_CONFIG).read_text(encoding="utf-8").replace("fallbackChains", "fallbackChain")
    check = candidate_checks.check_reviewer_config_schema(binary, home, text)
    assert check.failures == ("the build has no setting retry.fallbackChain; it would be ignored",)


def _captured(tmp_path: Path, mutate: Callable[[Path], None]) -> list[candidate_checks.Check]:
    directory = tmp_path / "fixtures"
    directory.mkdir()
    for item in FIXTURES.iterdir():
        (directory / item.name).write_bytes(item.read_bytes())
    (directory / "production.jsonl").write_bytes((FIXTURES.parent / "omp_multi_turn_review.jsonl").read_bytes())
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    manifest["streams"]["production-review"]["file"] = "production.jsonl"
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    mutate(directory)
    return candidate_checks.fixture_checks(directory)


def _rewrite(name: str, old: str, new: str) -> Callable[[Path], None]:
    def mutate(directory: Path) -> None:
        path = directory / name
        text = path.read_text(encoding="utf-8")
        assert old in text
        path.write_text(text.replace(old, new), encoding="utf-8")

    return mutate


def test_the_captured_streams_replay_green_through_the_current_parsers(tmp_path: Path) -> None:
    assert _failed(_captured(tmp_path, lambda directory: None)) == {}


@pytest.mark.parametrize(
    ("name", "old", "new", "check", "expected"),
    [
        (
            "production.jsonl",
            '"type":"toolCall"',
            '"type":"toolInvocation"',
            "fixture-production-review",
            "credits no read call",
        ),
        (
            "quota_terminal.jsonl",
            '"errorStatus":429',
            '"errorState":429',
            "fixture-quota-terminal",
            "terminal error status is None",
        ),
        (
            "upstream_auth.jsonl",
            '"errorStatus":401',
            '"errorState":401',
            "fixture-upstream-auth",
            "terminal error status is None",
        ),
        ("quota_terminal.jsonl", '"version":3', '"version":4', "fixture-quota-terminal", "session event version is 4"),
        (
            "quota_terminal.jsonl",
            '"provider":"anthropic"',
            '"vendor":"anthropic"',
            "fixture-quota-terminal",
            "provider is ''",
        ),
        ("elided_read.jsonl", "elided; re-read needed ranges", "omitted", "fixture-elided-read", "re-read footer"),
        ("elided_read.jsonl", '"summary":', '"digest":', "fixture-elided-read", "no elided read span"),
        (
            "quota_walk.jsonl",
            '"provider":"openai-codex"',
            '"provider":"anthropic"',
            "fixture-quota-walk",
            "did not move",
        ),
    ],
)
def test_a_mutated_capture_turns_the_replay_red_with_the_broken_contract_named(
    tmp_path: Path, name: str, old: str, new: str, check: str, expected: str
) -> None:
    failed = _failed(_captured(tmp_path, _rewrite(name, old, new)))
    assert check in failed, failed
    assert any(expected in item for item in failed[check])


def test_every_live_probe_passes_on_the_captured_runs() -> None:
    assert _failed(candidate_checks.live_checks(_good_runs())) == {}


def _mutated(name: str, **fields: Any) -> dict[str, ClientRun]:
    runs = _good_runs()
    runs[name] = replace(runs[name], **fields)
    return runs


@pytest.mark.parametrize(
    ("name", "fields", "check", "expected"),
    [
        ("review", {"canary_hits": ("/canary",)}, "live-overlay-behaviour", "fetched a URL"),
        ("review", {"mcp_started": True}, "live-overlay-behaviour", "MCP server started"),
        (
            "review",
            {"upstream": tuple(_request(prompt=AGENTS_TOKEN) for _ in range(5))},
            "live-overlay-behaviour",
            "context files",
        ),
        ("review", {"upstream": tuple(_request() for _ in range(6))}, "live-overlay-behaviour", "called 6 times"),
        (
            "review",
            {"upstream": tuple(_request(("read", "grep", "glob", "fetch")) for _ in range(5))},
            "live-tool-roster",
            "request 1 offered",
        ),
        ("review", {"upstream": ()}, "live-gateway-route", "no request reached the stub"),
        (
            "review",
            {"session_files": ("/home/.omp/agent/sessions/a.jsonl",)},
            "live-no-side-effects",
            "left session state",
        ),
        ("review", {"workspace_changed": True}, "live-no-side-effects", "workspace was modified"),
        ("review", {"survivors": (4242,)}, "live-review-run", "left 1 process"),
        ("review", {"exit_code": 1}, "live-review-run", "exited 1, expected 0"),
        ("review", {"timed_out": True}, "live-review-run", "timed out"),
        ("control", {"canary_hits": ()}, "live-probe-sensitivity", "URL canary is no longer observable"),
        ("control", {"mcp_started": False}, "live-probe-sensitivity", "MCP canary is no longer observable"),
        ("control", {"upstream": (_request(),)}, "live-probe-sensitivity", "context-file canary"),
        ("control", {"stdout": _read("quota_terminal.jsonl")}, "live-probe-sensitivity", "read-summary canary"),
        ("roster-breach", {"workspace_changed": True}, "live-write-refused", "changed the review workspace"),
        (
            "roster-breach",
            {"stdout": _read("roster_breach.jsonl").replace('"isError":true', '"isError":false')},
            "live-write-refused",
            "a bash call outside the read roster was not refused",
        ),
        ("quota-walk", {"exit_code": 0}, "live-quota-fallback-walk", "expected a non-zero code"),
        ("quota-terminal", {"exit_code": 0}, "live-quota-classification", "expected a non-zero code"),
        ("upstream-auth", {"stdout": _read("quota_terminal.jsonl")}, "live-auth-classification", "status is 429"),
        ("bad-token", {"upstream": (_request(),)}, "live-gateway-token", "forwarded a request"),
        ("bad-token", {"exit_code": 0}, "live-gateway-token", "expected a non-zero code"),
    ],
)
def test_a_live_contract_breach_is_red_and_named(name: str, fields: dict[str, Any], check: str, expected: str) -> None:
    failed = _failed(candidate_checks.live_checks(_mutated(name, **fields)))
    assert check in failed, failed
    assert any(expected in item for item in failed[check])


def test_a_renamed_tool_call_block_in_the_live_stream_breaks_the_stream_and_overlay_probes() -> None:
    stdout = _read("chain_review.jsonl").replace('"type":"toolCall"', '"type":"toolInvocation"')
    failed = _failed(candidate_checks.live_checks(_mutated("review", stdout=stdout)))
    assert any("credits no read call" in item for item in failed["live-review-stream"])


def test_a_dropped_error_status_in_the_live_stream_breaks_the_quota_classification() -> None:
    stdout = _read("quota_terminal.jsonl").replace('"errorStatus":429', '"errorState":429')
    failed = _failed(candidate_checks.live_checks(_mutated("quota-terminal", stdout=stdout)))
    assert any("status is None" in item for item in failed["live-quota-classification"])


def test_every_contract_row_has_a_probe_or_a_recorded_reason(tmp_path: Path) -> None:
    checks = [
        *_static(tmp_path).values(),
        *candidate_checks.fixture_checks(FIXTURES),
        *candidate_checks.live_checks(_good_runs()),
    ]
    assert candidate_checks.uncovered_rows(checks) == ()
    assert set(candidate_checks.RECORDED_REASONS) <= set(candidate_checks.CONTRACT_ROWS)
    assert candidate_checks.uncovered_rows([]) == tuple(
        row for row in candidate_checks.CONTRACT_ROWS if row not in candidate_checks.RECORDED_REASONS
    )


def test_a_frozen_capture_alone_never_satisfies_a_contract_row(tmp_path: Path) -> None:
    replayed = candidate_checks.fixture_checks(FIXTURES)
    assert candidate_checks.covered_rows(replayed) == frozenset()
    assert candidate_checks.uncovered_rows(replayed) == tuple(
        row for row in candidate_checks.CONTRACT_ROWS if row not in candidate_checks.RECORDED_REASONS
    )


def test_every_manifest_stream_names_its_origin_and_the_files_exist() -> None:
    manifest = json.loads(_read("manifest.json"))
    for name, entry in manifest["streams"].items():
        assert entry["source"].startswith("captured"), name
        assert (FIXTURES / entry["file"]).is_file(), name
        assert set(entry["rows"]) <= set(candidate_checks.CONTRACT_ROWS), name


@contextmanager
def _chain_from(runs: dict[str, ClientRun]) -> Iterator[Any]:
    class Replay:
        def run(self, scenario: candidate_chain.Scenario) -> ClientRun:
            return runs[scenario.name]

    yield Replay()


def _pin_for_host(binary: Path, *, only_host: bool = False, **overrides: str) -> omp_pin.Pin:
    digest = omp_pin.sha256_of(binary)
    host = omp_pin.Build("https://downloads.example.invalid/omp", overrides.get("host", digest))
    builds = {omp_pin.current_platform(): host}
    if not only_host:
        builds["linux-arm64"] = omp_pin.Build("https://downloads.example.invalid/other", overrides.get("other", digest))
    return omp_pin.Pin(VERSION, builds)


def _fetcher(binary: Path) -> Callable[[str, Path], None]:
    def fetch(url: str, destination: Path) -> None:
        destination.write_bytes(binary.read_bytes())

    return fetch


def test_a_candidate_whose_only_pinned_build_hashes_and_whose_probes_pass_is_green(tmp_path: Path) -> None:
    binary, _ = _fake(tmp_path)
    report = omp_candidate.check_candidate(
        _pin_for_host(binary, only_host=True),
        fetch=_fetcher(binary),
        chain_factory=lambda *_: _chain_from(_good_runs()),
    )
    assert report.ok, report.render()
    assert report.transcript == "omp/18.12.1"
    assert json.loads(json.dumps(report.to_json()))["ok"] is True
    assert "result: green" in report.render()


def test_a_platform_that_was_only_hash_checked_is_unverified_and_never_green(tmp_path: Path) -> None:
    binary, _ = _fake(tmp_path)
    report = omp_candidate.check_candidate(
        _pin_for_host(binary),
        fetch=_fetcher(binary),
        chain_factory=lambda *_: _chain_from(_good_runs()),
    )
    assert report.passed and not report.ok
    assert {build.target for build in report.builds} == {omp_pin.current_platform(), "linux-arm64"}
    assert report.unverified == ("linux-arm64",)
    assert {build.target: build.probed for build in report.builds} == {
        omp_pin.current_platform(): True,
        "linux-arm64": False,
    }
    assert "result: partial" in report.render()
    assert "linux-arm64: sha256=" in report.render()


def test_a_run_limited_to_some_platforms_is_partial_and_never_green(tmp_path: Path) -> None:
    binary, _ = _fake(tmp_path)
    report = omp_candidate.check_candidate(
        _pin_for_host(binary),
        targets=[omp_pin.current_platform()],
        fetch=_fetcher(binary),
        chain_factory=lambda *_: _chain_from(_good_runs()),
    )
    assert report.passed and not report.ok
    assert report.unverified == ("linux-arm64",)
    assert "result: partial" in report.render()
    assert report.to_json()["unverified_platforms"] == ["linux-arm64"]


def test_a_platform_build_that_does_not_hash_to_the_pin_turns_the_report_red(tmp_path: Path) -> None:
    binary, _ = _fake(tmp_path)
    report = omp_candidate.check_candidate(
        _pin_for_host(binary, other="0" * 64),
        fetch=_fetcher(binary),
        chain_factory=lambda *_: _chain_from(_good_runs()),
    )
    assert not report.ok
    assert [build.target for build in report.builds if not build.ok] == ["linux-arm64"]
    assert "MISMATCH" in report.render()


def test_a_host_build_that_does_not_hash_to_the_pin_is_never_executed(tmp_path: Path) -> None:
    binary, _ = _fake(tmp_path)
    started: list[bool] = []

    def factory(*_: Any) -> Any:
        started.append(True)
        return _chain_from(_good_runs())

    report = omp_candidate.check_candidate(
        _pin_for_host(binary, host="0" * 64), fetch=_fetcher(binary), chain_factory=factory
    )
    assert not report.ok and started == []
    assert not (binary.parent / "executed").exists()
    assert report.transcript == "not executed: the host build does not hash to the pin"
    assert "static-probes" in _failed(list(report.checks))


def test_a_candidate_failing_a_static_probe_never_starts_the_loopback_chain(tmp_path: Path) -> None:
    binary, _ = _fake(tmp_path, lambda data: data.update(version="omp/9.9.9"))
    started: list[bool] = []

    def factory(*_: Any) -> Any:
        started.append(True)
        return _chain_from(_good_runs())

    report = omp_candidate.check_candidate(_pin_for_host(binary), fetch=_fetcher(binary), chain_factory=factory)
    assert not report.ok and started == []
    failed = _failed(list(report.checks))
    assert "version-format" in failed and "live-chain" in failed


def test_a_chain_that_cannot_start_is_a_red_check_not_a_crash(tmp_path: Path) -> None:
    binary, _ = _fake(tmp_path)

    @contextmanager
    def broken(*_: Any) -> Iterator[Any]:
        raise RuntimeError("the candidate auth-gateway did not listen")
        yield

    report = omp_candidate.check_candidate(_pin_for_host(binary), fetch=_fetcher(binary), chain_factory=broken)
    assert "the candidate auth-gateway did not listen" in _failed(list(report.checks))["live-chain"][0]


def test_a_version_that_the_pin_does_not_name_is_refused_with_the_way_forward(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert omp_candidate.main(["check", "--version", "99.0.0"]) == omp_candidate.USAGE_ERROR
    assert "pass --pin with a file whose omp section names the candidate" in capsys.readouterr().err


def test_a_capture_rewrites_machine_paths_to_placeholders() -> None:
    text = omp_candidate.redact_capture('{"cwd":"/tmp/work/chain/review/scratch"}', (("/tmp/work/chain", "/scratch"),))
    assert text == '{"cwd":"/scratch/review/scratch"}'


def test_the_upstream_stub_replays_its_script_records_requests_and_repeats_the_last_turn() -> None:
    script = [
        candidate_stub.tool_turn("read", {"path": "/x"}, "toolu_1"),
        candidate_stub.error_turn(429, "rate_limit_error", "slow"),
    ]
    with candidate_stub.UpstreamStub(script) as stub:

        def post(body: dict[str, Any]) -> tuple[int, str]:
            request = urllib.request.Request(stub.url + "/v1/messages", data=json.dumps(body).encode(), method="POST")
            try:
                with urllib.request.urlopen(request, timeout=10) as response:
                    return response.status, response.read().decode()
            except urllib.error.HTTPError as error:
                return error.code, error.read().decode()

        first = post({"n": 1})
        second = post({"n": 2})
        third = post({"n": 3})
        assert first[0] == 200 and '"name": "read"' in first[1] and "input_json_delta" in first[1]
        assert second[0] == third[0] == 429 and "rate_limit_error" in second[1]
        assert [item["body"] for item in stub.requests] == [{"n": 1}, {"n": 2}, {"n": 3}]
        stub.load([candidate_stub.text_turn("done")])
        assert stub.requests == []
        assert post({})[1].count("text_delta") == 1


def test_the_canary_server_records_every_request_path() -> None:
    with candidate_stub.CanaryServer() as canary:
        with urllib.request.urlopen(canary.url, timeout=10) as response:
            assert response.read() == b"canary page body"
        assert canary.hits == ["/canary"]


def test_the_reviewer_command_accepts_an_explicit_binary_and_overlay(tmp_path: Path) -> None:
    invocation = commands.build_omp(
        selector="@default",
        effort="",
        workspace=tmp_path / "ws",
        scratch=tmp_path / "scratch",
        prompt="p",
        binary=tmp_path / "candidate-omp",
        overlay_text="{}\n",
    )
    assert invocation.argv[0] == str(tmp_path / "candidate-omp")
    overlay = Path(invocation.argv[invocation.argv.index("--config") + 1])
    assert overlay.read_text(encoding="utf-8") == "{}\n"


def test_a_disabled_provider_the_build_no_longer_names_is_red(tmp_path: Path) -> None:
    binary, _ = _fake(tmp_path)
    assert candidate_checks.check_disabled_providers(binary).ok
    renamed = candidate_checks.check_disabled_providers(
        binary, "disabledProviders:\n  - agents-md\n  - renamed-context-provider\n"
    )
    assert renamed.failures == (
        'the build no longer contains the context provider id "renamed-context-provider"; '
        "its disabledProviders entry would be ignored",
    )
