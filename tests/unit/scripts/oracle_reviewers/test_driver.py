from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from _project.scripts.oracle_reviewers import cli, driver, github, isolation, omp_pin
from _project.scripts.oracle_reviewers.driver import CiEnvironment, GitHubSink, RecordingSink

pytestmark = [pytest.mark.unit, pytest.mark.fast]

HEAD = "a" * 40
REPO = "owner/repo"


def _environment(tmp_path: Path, **overrides: str) -> CiEnvironment:
    environ = {
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_WORKSPACE": str(tmp_path),
        "GITHUB_REPOSITORY": REPO,
        "HAS_APP": "true",
        "PR_NUMBER": "7",
        "GH_TOKEN": "app-token",
        "JOB_TOKEN": "job-token",
        "HOME": str(tmp_path / "home"),
        **overrides,
    }
    return CiEnvironment.from_environ(environ, tmp_path / "policy.yml")


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _final(root: Path, *, comment: bool = False, review: bool = False, state: bool = True) -> None:
    _write(root / "status.json", {"state": "success", "context": "oracle-verdict"})
    if comment:
        _write(root / "comment.json", {"body": "comment"})
    if review:
        _write(root / "review.json", {"body": "review", "comments": []})
    if state:
        _write(root / "state" / "state.json", {"head_sha": HEAD, "updated_at": "2026-10-28T12:00:00+00:00"})
        _write(root / "attempt" / "state.json", {"head_sha": HEAD})


def _plan(layout: driver.Layout, **fields: Any) -> None:
    _write(
        layout.plan_file,
        {"pr": 7, "head_sha": HEAD, "decision": "review", "created_at": "now", **fields},
    )


@pytest.fixture
def outputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "outputs.txt"
    target.write_text("", encoding="utf-8")
    monkeypatch.setenv("GITHUB_OUTPUT", str(target))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))
    return target


class FailingSink(RecordingSink):
    def status(self, repo: str, head: str, payload: Path) -> None:
        raise github.GitHubError("the status was refused")


def test_every_phase_is_a_drive_subcommand_and_only_prepare_and_finalize_shadow() -> None:
    parser = cli._parser()
    for name in driver.PHASES:
        parsed = parser.parse_args(["drive", name])
        assert parsed.phase == name and parsed.handler is driver.command_drive
    assert parser.parse_args(["drive", "prepare", "--shadow"]).shadow is True
    assert parser.parse_args(["drive", "finalize", "--shadow"]).shadow is True
    with pytest.raises(SystemExit):
        parser.parse_args(["drive", "publish", "--shadow"])


def test_the_ci_environment_derives_its_layout_and_scan_roots_from_the_runner(tmp_path: Path) -> None:
    environment = _environment(tmp_path, TMPDIR="/custom/scratch")

    assert environment.layout.plan == tmp_path / "oracle-plan"
    assert environment.layout.workspace == tmp_path / "workspace"
    assert environment.layout.classification == tmp_path / "oracle-classification" / "classification.json"
    assert environment.layout.shadowed().final == tmp_path / "oracle-shadow" / "final"
    assert environment.layout.shadowed().attempts == environment.layout.attempts
    assert Path("/custom/scratch") in environment.scan_roots
    assert tmp_path / "trusted" in environment.scan_roots and tmp_path / "workspace" in environment.scan_roots


def test_the_scan_phase_fails_when_a_secret_value_is_on_disk_and_passes_otherwise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outputs: Path
) -> None:
    root = tmp_path / "scratch"
    root.mkdir()
    environment = replace(_environment(tmp_path), scan_roots=(root,))
    monkeypatch.setenv("TYPESAFE_API_KEY", "typesafe-secret-value")

    assert driver.scan(environment, argparse.Namespace()) == 0

    (root / "leak.txt").write_text("key=typesafe-secret-value", encoding="utf-8")
    assert driver.scan(environment, argparse.Namespace()) == 1


def test_the_isolation_phase_returns_the_adapter_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(isolation, "main", lambda: 1)
    assert driver.isolate(_environment(tmp_path), argparse.Namespace()) == 1
    monkeypatch.setattr(isolation, "main", lambda: 0)
    assert driver.isolate(_environment(tmp_path), argparse.Namespace()) == 0


def test_the_harness_phase_records_the_installed_binary_and_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outputs: Path
) -> None:
    environment = _environment(tmp_path)
    environment.policy.write_text("omp: pinned", encoding="utf-8")
    binary = tmp_path / "omp"
    binary.write_text("#!/bin/sh\necho 18.12.1\n", encoding="utf-8")
    binary.chmod(0o755)
    env_file = tmp_path / "github-env.txt"
    monkeypatch.setenv("GITHUB_ENV", str(env_file))
    monkeypatch.setattr(omp_pin, "pin_from_text", lambda text: text)
    monkeypatch.setattr(omp_pin, "install", lambda pin: binary)

    assert driver.harness(environment, argparse.Namespace()) == 0

    assert env_file.read_text(encoding="utf-8") == f"ORACLE_OMP_BINARY={binary}\n"
    assert "state=installed" in outputs.read_text(encoding="utf-8")
    assert "18.12.1" in (tmp_path / "summary.md").read_text(encoding="utf-8")


def test_the_harness_phase_reports_a_failed_install_without_failing_the_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outputs: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    environment = _environment(tmp_path)
    environment.policy.write_text("omp: pinned", encoding="utf-8")

    def refuse(pin: object) -> Path:
        raise omp_pin.PinError("the omp download failed")

    monkeypatch.setattr(omp_pin, "pin_from_text", lambda text: text)
    monkeypatch.setattr(omp_pin, "install", refuse)

    assert driver.harness(environment, argparse.Namespace()) == 0

    assert "state=failed" in outputs.read_text(encoding="utf-8")
    assert "the pinned omp build could not be installed" in capsys.readouterr().out


def test_a_shadow_prepare_writes_its_own_plan_and_reports_nothing_to_the_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outputs: Path
) -> None:
    environment = _environment(tmp_path)
    seen: list[str] = []

    def plan(args: argparse.Namespace) -> int:
        seen.append(args.out)
        cli._output({"decision": "skip"})
        return 0

    monkeypatch.setattr(cli, "command_plan", plan)

    assert driver.prepare(environment, argparse.Namespace(shadow=True)) == 0

    assert seen == [str(tmp_path / "oracle-shadow" / "plan")]
    assert outputs.read_text(encoding="utf-8") == ""


def test_prepare_classifies_only_a_plan_that_will_be_reviewed_and_survives_a_classifier_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outputs: Path
) -> None:
    environment = _environment(tmp_path)
    selected: list[int] = []

    def plan(args: argparse.Namespace) -> int:
        _write(Path(args.out) / cli.PLAN_FILE, {"decision": environment_decision[0]})
        return 0

    def select(args: argparse.Namespace) -> int:
        selected.append(args.slot)
        raise RuntimeError("the classifier crashed")

    environment_decision = ["success"]
    monkeypatch.setattr(cli, "command_plan", plan)
    monkeypatch.setattr(cli, "command_select", select)

    assert driver.prepare(environment, argparse.Namespace()) == 0
    assert selected == []

    environment_decision[0] = "review"
    assert driver.prepare(environment, argparse.Namespace()) == 0
    assert selected == [1]
    assert "classification failed: RuntimeError" in (tmp_path / "summary.md").read_text(encoding="utf-8")


def test_publish_posts_status_comment_review_then_dispatches_the_gate(tmp_path: Path, outputs: Path) -> None:
    environment = _environment(tmp_path)
    _plan(environment.layout)
    _final(environment.layout.final, comment=True, review=True)
    sink = RecordingSink()

    code = driver.publish_with(environment, environment.layout, sink, guarded=False)

    assert code == 0
    assert [item["kind"] for item in sink.intents] == ["status", "comment", "review", "gate"]
    assert sink.intents[0]["target"] == f"{REPO}/statuses/{HEAD}"
    written = outputs.read_text(encoding="utf-8")
    assert "attempt_state=true" in written and "state_recorded=true" in written


def test_publish_posts_nothing_for_a_head_the_guard_says_is_decided(tmp_path: Path, outputs: Path) -> None:
    environment = _environment(tmp_path)
    _plan(environment.layout)
    _final(environment.layout.final)
    sink = RecordingSink()

    code = driver.publish_with(environment, environment.layout, sink, guarded=False, skip=True)

    assert code == 0 and sink.intents == []
    assert "attempt_state" not in outputs.read_text(encoding="utf-8")


def test_publish_runs_the_guard_and_reports_its_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outputs: Path
) -> None:
    environment = _environment(tmp_path)
    _plan(environment.layout)
    _final(environment.layout.final)
    monkeypatch.setattr(cli, "guard_decision", lambda plan: (0, True))
    sink = RecordingSink()

    assert driver.publish_with(environment, environment.layout, sink, guarded=True) == 0

    assert sink.intents == []
    assert "skip=true" in outputs.read_text(encoding="utf-8")


def test_a_guard_that_cannot_read_history_fails_the_phase_but_still_records_the_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outputs: Path
) -> None:
    environment = _environment(tmp_path)
    _plan(environment.layout)
    _final(environment.layout.final)
    monkeypatch.setattr(cli, "guard_decision", lambda plan: (2, False))
    sink = RecordingSink()

    assert driver.publish_with(environment, environment.layout, sink, guarded=True) == 2

    written = outputs.read_text(encoding="utf-8")
    assert sink.intents == [] and "attempt_state=true" in written and "skip=" not in written


def test_without_the_app_nothing_is_posted_and_the_attempt_is_still_recorded(
    tmp_path: Path, outputs: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    environment = _environment(tmp_path, HAS_APP="false")
    _plan(environment.layout)
    _final(environment.layout.final)
    sink = RecordingSink()

    assert driver.publish_with(environment, environment.layout, sink, guarded=True) == 0

    assert sink.intents == []
    assert "attempt_state=true" in outputs.read_text(encoding="utf-8")
    assert "is not posted because the App secrets are not set; result success" in capsys.readouterr().out


def test_a_refused_status_stops_the_phase_before_anything_else_is_posted(
    tmp_path: Path, outputs: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    environment = _environment(tmp_path)
    _plan(environment.layout)
    _final(environment.layout.final, comment=True, review=True)
    sink = FailingSink()

    assert driver.publish_with(environment, environment.layout, sink, guarded=False) == 1

    assert sink.intents == []
    assert "state_recorded" not in outputs.read_text(encoding="utf-8")
    assert "the status was refused" in capsys.readouterr().err


@pytest.mark.parametrize(("base", "dispatched"), [("develop", True), ("release", False)])
def test_the_github_sink_dispatches_the_gate_only_for_a_develop_base(
    monkeypatch: pytest.MonkeyPatch, base: str, dispatched: bool
) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(github, "pull_base_ref", lambda repo, pr, token: base)
    monkeypatch.setattr(github, "dispatch_gate", lambda repo, pr, head, token: calls.append((token, head)))

    GitHubSink("app-token", "job-token").dispatch_gate(REPO, 7, HEAD)

    assert calls == ([("job-token", HEAD)] if dispatched else [])


def test_the_github_sink_posts_each_payload_with_the_app_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    posted: list[tuple[str, str]] = []
    monkeypatch.setattr(github, "post_file", lambda path, payload, token: posted.append((path, token)))
    payload = tmp_path / "payload.json"

    sink = GitHubSink("app-token", "job-token")
    sink.status(REPO, HEAD, payload)
    sink.comment(REPO, 7, payload)
    sink.review(REPO, 7, payload)

    assert posted == [
        (f"repos/{REPO}/statuses/{HEAD}", "app-token"),
        (f"repos/{REPO}/issues/7/comments", "app-token"),
        (f"repos/{REPO}/pulls/7/reviews", "app-token"),
    ]


def _shadowed_run(tmp_path: Path, *, comment: bool = False) -> CiEnvironment:
    environment = _environment(tmp_path)
    for layout in (environment.layout, environment.layout.shadowed()):
        _plan(layout)
        _final(layout.final, comment=comment)
    return environment


def _outcomes(monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    for name in ("POSTED_STATUS", "POSTED_COMMENT", "POSTED_REVIEW", "POSTED_GATE", "GUARD_SKIP", "GUARD_OUTCOME"):
        monkeypatch.delenv(name, raising=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def _result(tmp_path: Path) -> dict[str, Any]:
    return json.loads((tmp_path / "oracle-shadow" / driver.RESULT_FILE).read_text(encoding="utf-8"))


def test_compare_reports_equal_when_the_driver_outputs_match_what_was_posted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outputs: Path
) -> None:
    environment = _shadowed_run(tmp_path, comment=True)
    _outcomes(
        monkeypatch,
        POSTED_STATUS="success",
        POSTED_COMMENT="success",
        POSTED_REVIEW="skipped",
        POSTED_GATE="success",
    )

    assert driver.compare(environment, argparse.Namespace()) == 0

    assert _result(tmp_path) == {"differences": {}, "equal": True}
    assert "equal=true" in outputs.read_text(encoding="utf-8")


def test_compare_names_the_final_file_that_differs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outputs: Path
) -> None:
    environment = _shadowed_run(tmp_path)
    _write(environment.layout.shadowed().final / "status.json", {"state": "failure", "context": "oracle-verdict"})
    _outcomes(monkeypatch, POSTED_STATUS="success", POSTED_GATE="success")

    assert driver.compare(environment, argparse.Namespace()) == 0

    result = _result(tmp_path)
    assert result["equal"] is False
    assert sorted(result["differences"]) == ["final/status.json", "publish"]
    assert "equal=false" in outputs.read_text(encoding="utf-8")
    assert "differ from the posted outputs in final/status.json, publish" in (tmp_path / "summary.md").read_text(
        encoding="utf-8"
    )


def test_compare_ignores_the_plan_creation_time_but_not_other_plan_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outputs: Path
) -> None:
    environment = _shadowed_run(tmp_path)
    _plan(environment.layout.shadowed(), created_at="later")
    _outcomes(monkeypatch, POSTED_STATUS="success", POSTED_GATE="success")
    driver.compare(environment, argparse.Namespace())
    assert _result(tmp_path)["equal"] is True

    _plan(environment.layout.shadowed(), decision="carry")
    driver.compare(environment, argparse.Namespace())
    assert list(_result(tmp_path)["differences"]) == ["plan"]


def test_compare_flags_a_missing_shadow_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outputs: Path) -> None:
    environment = _environment(tmp_path)
    _plan(environment.layout)
    _outcomes(monkeypatch)

    driver.compare(environment, argparse.Namespace())

    assert _result(tmp_path)["differences"] == {"plan": {"posted": "present", "shadow": None}}


def test_compare_expects_nothing_posted_when_the_guard_failed_or_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outputs: Path
) -> None:
    environment = _shadowed_run(tmp_path)
    _outcomes(monkeypatch, GUARD_OUTCOME="failure", POSTED_STATUS="skipped", POSTED_GATE="skipped")
    driver.compare(environment, argparse.Namespace())
    assert _result(tmp_path)["equal"] is True

    _outcomes(monkeypatch, GUARD_SKIP="true", POSTED_STATUS="skipped", POSTED_GATE="skipped")
    driver.compare(environment, argparse.Namespace())
    assert _result(tmp_path)["equal"] is True


def test_the_shadow_finalize_reuses_the_posted_runs_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outputs: Path
) -> None:
    environment = _environment(tmp_path)
    _final(environment.layout.final)
    seen: list[Any] = []

    def finalize(args: argparse.Namespace) -> int:
        seen.append((args.plan, args.out_dir, args.now))
        return 0

    monkeypatch.setattr(cli, "command_finalize", finalize)
    monkeypatch.setattr(cli, "command_redact_check", lambda args: 0)

    assert driver.finalize(environment, argparse.Namespace(shadow=True)) == 0

    plan, out_dir, now = seen[0]
    assert plan == str(tmp_path / "oracle-shadow" / "plan" / cli.PLAN_FILE)
    assert out_dir == str(tmp_path / "oracle-shadow" / "final")
    assert now.isoformat() == "2026-10-28T12:00:00+00:00"
