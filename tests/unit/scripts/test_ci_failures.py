from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts/ci_failures.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("ci_failures", SCRIPT)
assert SPEC and SPEC.loader
ci_failures = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ci_failures
SPEC.loader.exec_module(ci_failures)

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO = "BenchBox-dev/BenchBox"
HEAD = "a" * 40
STAMP = "2026-11-11T14:11:01.5715702Z "

PYTEST_LOG = "\n".join(
    STAMP + line
    for line in [
        "##[group]Run uv run -- python -m pytest",
        "collected 3 items",
        "\x1b[31mE   AssertionError: assert 1 == 2\x1b[0m",
        *[f"E   detail {index}" for index in range(40)],
        "=========================== short test summary info ============================",
        "FAILED tests/unit/test_thing.py::test_value - AssertionError: assert 1 == 2",
        "==== 1 failed, 2 passed in 1.00s ====",
        "##[error]Process completed with exit code 1.",
    ]
)
LINT_LOG = "\n".join(
    STAMP + line
    for line in [
        "uv run -- python scripts/doc_reference_inventory.py --check",
        "quality/docs-reference-inventory.json is stale; run make docs-reference-inventory",
        "make: *** [make/documentation.mk:65: docs-reference-inventory-check] Error 1",
        "## Lint guard summary",
        "FAILED: guard-doc-contracts (failure)",
        "One or more lint guards failed -- see the table above and the per-step logs for details.",
        "##[error]Process completed with exit code 1.",
    ]
)
HOSTILE_LOG = "\n".join(
    STAMP + line
    for line in [
        "::error file=a.py::injected annotation",
        "##[error]real error \x07 with a bell and \x1b[2J a clear-screen",
        "FAILED ##[group]fake group",
    ]
)


class FakeGh:
    def __init__(
        self,
        runs: list[tuple[int, str, str, str, str]],
        jobs: dict[int, list[dict]],
        logs: dict[int, str | None],
        head: str = HEAD,
    ) -> None:
        self.runs = runs
        self.jobs = jobs
        self.logs = logs
        self.head = head
        self.calls: list[list[str]] = []
        self.workflows: dict[str, int] = {}

    def _line(self, run: tuple[int, str, str, str, str]) -> str:
        workflow = self.workflows.setdefault(run[1].split(" (")[0], 900 + len(self.workflows))
        return "\t".join(map(str, (run[0], workflow, *run[1:])))

    def __call__(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)
        target = command[-1]
        if command[:3] == ["gh", "pr", "view"]:
            return self._ok(self.head + "\n")
        if "/actions/runs?head_sha=" in target:
            return self._ok("\n".join(self._line(run) for run in self.runs))
        if target.endswith("/logs"):
            body = self.logs.get(int(target.split("/")[-2]))
            if body is None:
                return subprocess.CompletedProcess(command, 1, "", "gh: Not Found (HTTP 404)")
            return self._ok(body)
        if "/jobs?" in target:
            run_id = int(target.split("/actions/runs/")[1].split("/")[0])
            return self._ok("\n".join(json.dumps(job) for job in self.jobs.get(run_id, [])))
        if "/actions/runs/" in target:
            run_id = int(target.rsplit("/", 1)[1])
            return self._ok(self._line(next(run for run in self.runs if run[0] == run_id)))
        raise AssertionError(f"unexpected command {command}")

    @staticmethod
    def _ok(stdout: str) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], 0, stdout, "")


def _job(job_id: int, name: str, step: str, conclusion: str = "failure") -> dict:
    return {"id": job_id, "name": name, "status": "completed", "conclusion": conclusion, "steps": [step]}


def test_a_failed_test_job_prints_the_failed_step_and_the_failed_test_id() -> None:
    gh = FakeGh(
        [(11, "CI", "fix: thing", "completed", "failure")],
        {11: [_job(101, "test", "Run fast tests")]},
        {101: PYTEST_LOG},
    )

    lines = ci_failures.report(gh, REPO, pr=7, run_id=None)

    text = "\n".join(lines)
    assert lines[0] == f"ci-failures: PR #7 head {HEAD[:9]}"
    assert "job test [failure] failed step: Run fast tests" in text
    assert "FAILED tests/unit/test_thing.py::test_value" in text
    assert "E   AssertionError: assert 1 == 2" in text
    assert "\x1b" not in text
    assert STAMP.strip() not in text


def test_assertion_detail_is_capped_so_the_failed_test_id_survives() -> None:
    lines = ci_failures.excerpt(PYTEST_LOG, limit=40)

    assert sum(line.startswith("E   ") for line in lines) == ci_failures.DETAIL_LINES
    assert any(line.startswith("FAILED tests/unit/test_thing.py::test_value") for line in lines)


def test_a_lint_aggregator_failure_names_the_inner_guard_and_its_cause() -> None:
    gh = FakeGh(
        [(11, "CI", "fix: thing", "completed", "failure")],
        {11: [_job(102, "lint", "lint-guard-summary")]},
        {102: LINT_LOG},
    )

    text = "\n".join(ci_failures.report(gh, REPO, pr=7, run_id=None))

    assert "FAILED: guard-doc-contracts (failure)" in text
    assert "quality/docs-reference-inventory.json is stale" in text


def test_a_run_in_progress_reports_its_finished_failed_jobs_and_says_more_may_follow() -> None:
    gh = FakeGh(
        [(11, "CI", "fix: thing", "in_progress", "")],
        {
            11: [
                _job(101, "test", "Run fast tests"),
                {"id": 103, "name": "medium", "status": "in_progress", "conclusion": "", "steps": []},
            ]
        },
        {101: PYTEST_LOG},
    )

    lines = ci_failures.report(gh, REPO, pr=7, run_id=None)

    assert any("job test [failure]" in line for line in lines)
    assert not any("job medium" in line for line in lines)
    assert lines[-1] == "1 run(s) still in progress; later jobs may still fail"


def test_a_missing_log_is_reported_without_stopping_the_other_jobs() -> None:
    gh = FakeGh(
        [(11, "CI", "fix: thing", "in_progress", "")],
        {11: [_job(101, "test", "Run fast tests"), _job(102, "lint", "lint-guard-summary")]},
        {101: None, 102: LINT_LOG},
    )

    text = "\n".join(ci_failures.report(gh, REPO, pr=7, run_id=None))

    assert "log not available: gh api failed: gh: Not Found (HTTP 404)" in text
    assert "FAILED: guard-doc-contracts (failure)" in text


def test_a_lone_text_edit_placeholder_run_is_named_and_its_jobs_are_never_read() -> None:
    title = ci_failures.ci_text_edit_verdict.TEXT_EDIT_RUN_NAME
    gh = FakeGh([(10, "CI", title, "completed", "failure")], {10: [_job(101, "core", "Aggregate core result")]}, {})

    lines = ci_failures.report(gh, REPO, pr=7, run_id=None)

    assert any("run 10" in line and "placeholder" in line for line in lines)
    assert "no failed job found" in lines
    assert not any("/jobs?" in call[-1] for call in gh.calls)


def test_a_placeholder_never_hides_the_full_run_whichever_is_newer() -> None:
    title = ci_failures.ci_text_edit_verdict.TEXT_EDIT_RUN_NAME
    gh = FakeGh(
        [(11, "CI", "fix: thing", "completed", "failure"), (12, "CI", title, "completed", "failure")],
        {11: [_job(102, "lint", "lint-guard-summary")], 12: [_job(103, "core", "Aggregate core result")]},
        {102: LINT_LOG},
    )

    text = "\n".join(ci_failures.report(gh, REPO, pr=7, run_id=None))

    assert "FAILED: guard-doc-contracts (failure)" in text
    assert "run 12" not in text


def test_only_the_newest_run_of_each_workflow_is_reported() -> None:
    gh = FakeGh(
        [
            (20, "pr-hold (opened pr=7)", "hold", "completed", "cancelled"),
            (21, "pr-hold (edited pr=7)", "hold", "completed", "success"),
            (11, "CI", "fix: thing", "completed", "failure"),
        ],
        {20: [_job(201, "gate / guard", "guard", "cancelled")], 11: [_job(102, "lint", "lint-guard-summary")]},
        {102: LINT_LOG},
    )

    text = "\n".join(ci_failures.report(gh, REPO, pr=7, run_id=None))

    assert "gate / guard" not in text
    assert "job lint [failure]" in text


def test_a_green_head_reports_no_failed_job() -> None:
    gh = FakeGh([(11, "CI", "fix: thing", "completed", "success")], {}, {})

    assert ci_failures.report(gh, REPO, pr=7, run_id=None) == [
        f"ci-failures: PR #7 head {HEAD[:9]}",
        "no failed job found",
    ]


def test_a_single_run_is_read_without_looking_up_a_pull_request() -> None:
    gh = FakeGh(
        [(11, "CI", "fix: thing", "completed", "failure")],
        {11: [_job(102, "lint", "lint-guard-summary")]},
        {102: LINT_LOG},
    )

    lines = ci_failures.report(gh, REPO, pr=None, run_id=11)

    assert lines[0] == "ci-failures: run 11"
    assert not any(call[:3] == ["gh", "pr", "view"] for call in gh.calls)


def test_log_text_cannot_inject_workflow_commands_or_terminal_controls() -> None:
    lines = ci_failures.excerpt(HOSTILE_LOG)

    assert lines
    for line in lines:
        assert not line.lstrip().startswith("::")
        assert "##[" not in line
        assert all(ord(char) >= 32 and ord(char) != 127 for char in line)


def test_a_job_excerpt_never_exceeds_the_line_limit() -> None:
    noisy = "\n".join(f"{STAMP}FAILED tests/unit/test_many.py::test_{index}" for index in range(500))

    for limit in (ci_failures.MIN_LINES, 40, ci_failures.MAX_LINES):
        lines = ci_failures.excerpt(noisy, limit)
        assert len(lines) == limit
        assert any("more matching line(s) omitted" in line for line in lines)


def test_a_log_with_no_recognised_line_falls_back_to_its_tail() -> None:
    log = "\n".join(f"{STAMP}line {index}" for index in range(30))

    assert ci_failures.excerpt(log) == [f"line {index}" for index in range(22, 30)]


@pytest.mark.parametrize(
    "environ",
    [
        {},
        {"CI_FAILURES_PR": "7", "CI_FAILURES_RUN": "11"},
        {"CI_FAILURES_PR": "7; touch x"},
        {"CI_FAILURES_RUN": "abc"},
        {"CI_FAILURES_PR": "0"},
        {"CI_FAILURES_PR": "7", "CI_FAILURES_REPO": "not a repo"},
        {"CI_FAILURES_PR": "7", "CI_FAILURES_LINES": "4"},
        {"CI_FAILURES_PR": "7", "CI_FAILURES_LINES": "201"},
    ],
)
def test_invalid_input_is_a_usage_error_before_any_command(
    environ: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    gh = FakeGh([], {}, {})

    assert ci_failures.main([], environ, gh) == 2
    assert gh.calls == []
    assert "usage: make pr-status" in capsys.readouterr().err


def test_main_prints_the_report_and_exits_zero_even_when_jobs_failed(capsys: pytest.CaptureFixture[str]) -> None:
    gh = FakeGh(
        [(11, "CI", "fix: thing", "completed", "failure")],
        {11: [_job(102, "lint", "lint-guard-summary")]},
        {102: LINT_LOG},
    )

    assert ci_failures.main([], {"CI_FAILURES_PR": "7", "CI_FAILURES_REPO": REPO}, gh) == 0
    assert "FAILED: guard-doc-contracts (failure)" in capsys.readouterr().out


def test_an_api_failure_exits_one_with_the_reason(capsys: pytest.CaptureFixture[str]) -> None:
    def broken(command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, "", "gh: HTTP 502")

    assert ci_failures.main([], {"CI_FAILURES_RUN": "11"}, broken) == 1
    assert "gh: HTTP 502" in capsys.readouterr().err


@pytest.mark.parametrize("conclusion", ["failure", "startup_failure", "timed_out"])
def test_a_failed_run_with_no_failed_job_is_still_reported_as_failed(conclusion: str) -> None:
    gh = FakeGh([(11, "CI", "fix: thing", "completed", conclusion)], {}, {})

    lines = ci_failures.report(gh, REPO, pr=7, run_id=None)

    assert "no failed job found" not in lines
    assert any("run 11" in line and conclusion in line for line in lines)
    assert any("no failed job recorded" in line for line in lines)


def test_a_run_cancelled_before_any_job_ran_is_not_reported_as_a_failure() -> None:
    gh = FakeGh([(21, "review-gate (pull_request_target pr=7)", "gate", "completed", "cancelled")], {}, {})

    assert ci_failures.report(gh, REPO, pr=7, run_id=None)[1:] == ["no failed job found"]
