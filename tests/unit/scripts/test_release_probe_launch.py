from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

SPEC = importlib.util.spec_from_file_location(
    "release_probe_launch_under_test", Path(__file__).resolve().parents[3] / "scripts" / "release_probe_launch.py"
)
assert SPEC is not None and SPEC.loader is not None
launch = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = launch
SPEC.loader.exec_module(launch)

REPOSITORY = "owner/repo"
BRANCH = "feat/x"
HEAD = "a" * 40


class FakeGh:
    def __init__(self, conclusions: dict[str, str] | None = None, *, pending_polls: int = 1, cancel_fails=False):
        self.conclusions = {"reuse": "success", "fallback": "success", **(conclusions or {})}
        self.pending_polls = pending_polls
        self.cancel_fails = cancel_fails
        self.runs: dict[int, dict] = {}
        self.variant_of: dict[int, str] = {}
        self.polls: dict[int, int] = {}
        self.dispatched: list[list[str]] = []
        self.cancelled: list[int] = []
        self.next_id = 100
        self.head_sha = HEAD

    def __call__(self, *args: str):
        if args[:2] == ("workflow", "run"):
            self.dispatched.append(list(args))
            variant = next(a for a in args if a.startswith("probe=")).removeprefix("probe=")
            self.next_id += 1
            self.variant_of[self.next_id] = variant
            self.runs[self.next_id] = {"id": self.next_id, "head_sha": self.head_sha}
            return None
        if args[0] == "api" and "--method" in args:
            run_id = int(args[-1].split("/")[-2])
            if self.cancel_fails:
                raise RuntimeError("forbidden")
            self.cancelled.append(run_id)
            return None
        path = args[1]
        if "/actions/workflows/release-v2.yml/runs" in path:
            return {"workflow_runs": list(self.runs.values())}
        run_id = int(path.rsplit("/", 1)[1])
        self.polls[run_id] = self.polls.get(run_id, 0) + 1
        if self.polls[run_id] <= self.pending_polls:
            return {"id": run_id, "status": "in_progress"}
        conclusion = self.conclusions[self.variant_of[run_id]]
        return {"id": run_id, "status": "completed", "conclusion": conclusion, "html_url": f"url/{run_id}"}


def _run(fake: FakeGh, **kwargs) -> int:
    return launch.run_probes(REPOSITORY, BRANCH, HEAD, runner=fake, sleep=lambda _: None, **kwargs)


def test_both_variants_are_dispatched_with_the_synthetic_tag_from_the_pull_request_branch(capsys):
    fake = FakeGh()
    assert _run(fake) == 0
    assert [next(a for a in call if a.startswith("probe=")) for call in fake.dispatched] == [
        "probe=reuse",
        "probe=fallback",
    ]
    for call in fake.dispatched:
        assert "tag=v0.0.0rc0" in call
        assert call[call.index("--ref") + 1] == BRANCH
        assert call[call.index("--repo") + 1] == REPOSITORY
        assert call[2] == "release-v2.yml"
    assert "reuse probe: success" in capsys.readouterr().out


@pytest.mark.parametrize("failing", ["reuse", "fallback"])
@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "timed_out", None])
def test_either_variant_failing_fails_the_job(failing, conclusion):
    assert _run(FakeGh({failing: conclusion})) == 1


def test_a_variant_that_never_appears_fails_and_names_the_commit(capsys):
    fake = FakeGh()
    fake.head_sha = "b" * 40
    with pytest.raises(RuntimeError, match="no release-v2.yml run at aaaaaaaaaa"):
        launch.launch(REPOSITORY, BRANCH, HEAD, "reuse", set(), runner=fake, sleep=lambda _: None, attempts=3)


def test_runs_that_existed_before_the_dispatch_are_never_adopted():
    fake = FakeGh()
    fake.runs[7] = {"id": 7, "head_sha": HEAD}
    run_id = launch.launch(REPOSITORY, BRANCH, HEAD, "reuse", {7}, runner=fake, sleep=lambda _: None)
    assert run_id != 7


def test_the_two_variants_never_share_a_run():
    fake = FakeGh()
    assert _run(fake) == 0
    assert len({*fake.variant_of}) == 2
    assert sorted(fake.polls) == sorted(fake.variant_of)


def test_a_run_that_outlives_the_timeout_fails_and_is_cancelled():
    fake = FakeGh(pending_polls=10**6)
    ticks = iter(range(0, 10**6, 100))
    with pytest.raises(RuntimeError, match="did not finish"):
        launch.run_probes(
            REPOSITORY, BRANCH, HEAD, runner=fake, sleep=lambda _: None, clock=lambda: next(ticks), timeout=250.0
        )
    assert sorted(fake.cancelled) == sorted(fake.variant_of)


def test_both_variants_are_bounded_by_one_overall_deadline(monkeypatch):
    fake = FakeGh()
    budgets: list[float] = []
    clock_now = [0.0]

    def waiting(repository, run_id, **kwargs):
        budgets.append(kwargs["timeout"])
        clock_now[0] += 400.0
        return {"status": "completed", "conclusion": "success", "html_url": ""}

    monkeypatch.setattr(launch, "wait_for", waiting)
    status = launch.run_probes(
        REPOSITORY, BRANCH, HEAD, runner=fake, sleep=lambda _: None, clock=lambda: clock_now[0], timeout=1000.0
    )
    assert status == 0
    assert budgets == [1000.0, 600.0]


def test_a_failed_cancel_is_reported_without_hiding_the_original_failure(capsys):
    fake = FakeGh(pending_polls=10**6, cancel_fails=True)
    ticks = iter(range(0, 10**6, 100))
    with pytest.raises(RuntimeError, match="did not finish"):
        launch.run_probes(
            REPOSITORY, BRANCH, HEAD, runner=fake, sleep=lambda _: None, clock=lambda: next(ticks), timeout=250.0
        )
    assert "could not cancel" in capsys.readouterr().err


@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/release-v2.yml",
        ".github/workflows/release-v2-probe.yml",
        ".github/workflows/trunk-uat.yml",
        ".github/workflows/trunk.yml",
        ".github/actions/release-uat-env/action.yml",
        "scripts/release_artifact_consumer.py",
        "scripts/release_reuse_uat_evidence.py",
        "scripts/release_probe_graph.py",
        "scripts/release_requirements.txt",
        "scripts/update_version.py",
        "tests/uat/gate_summary.py",
        "tests/uat/configs/release-gate-02-docker-nonoltp.yaml",
    ],
)
def test_every_release_input_named_by_the_probe_contract_triggers_the_probe(path):
    assert launch.touches_probed_paths(["README.md", path]) == [path]


@pytest.mark.parametrize(
    "path",
    ["README.md", ".github/workflows/ci.yml", "scripts/trunk_uat.py", "benchbox/core.py", "docs/release.md"],
)
def test_unrelated_files_do_not_trigger_the_probe(path):
    assert launch.touches_probed_paths([path]) == []


def test_changed_files_pages_through_the_whole_pull_request():
    pages = {1: [{"filename": f"f{i}"} for i in range(100)], 2: [{"filename": "last"}]}
    seen = []

    def runner(*args):
        page = int(args[1].rsplit("page=", 1)[1])
        seen.append(page)
        return pages[page]

    files = launch.changed_files(REPOSITORY, 5, runner)
    assert len(files) == 101 and files[-1] == "last"
    assert seen == [1, 2]


def test_the_main_command_skips_the_probe_for_a_pull_request_that_touches_no_release_input(monkeypatch, capsys):
    monkeypatch.setattr(launch, "changed_files", lambda repository, number: ["README.md"])
    monkeypatch.setattr(launch, "run_probes", lambda *a, **k: pytest.fail("the probe must not run"))
    argv = ["--repository", REPOSITORY, "--pull-request", "5", "--branch", BRANCH, "--head-sha", HEAD]
    assert launch.main(argv) == 0
    assert "nothing to probe" in capsys.readouterr().out


def test_the_main_command_probes_when_a_release_input_changed(monkeypatch):
    monkeypatch.setattr(launch, "changed_files", lambda repository, number: ["scripts/release_flow.py"])
    called = []
    monkeypatch.setattr(launch, "run_probes", lambda *a, **k: called.append(a) or 0)
    argv = ["--repository", REPOSITORY, "--pull-request", "5", "--branch", BRANCH, "--head-sha", HEAD]
    assert launch.main(argv) == 0
    assert called == [(REPOSITORY, BRANCH, HEAD)]


def test_the_main_command_reports_an_api_failure_as_an_error(monkeypatch, capsys):
    def boom(repository, number):
        raise RuntimeError("gh api failed: HTTP 403")

    monkeypatch.setattr(launch, "changed_files", boom)
    argv = ["--repository", REPOSITORY, "--pull-request", "5", "--branch", BRANCH, "--head-sha", HEAD]
    assert launch.main(argv) == 1
    assert "HTTP 403" in capsys.readouterr().err


@pytest.fixture
def fake_gh(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    argv_log = tmp_path / "argv.txt"
    script = bin_dir / "gh"
    script.write_text('#!/bin/sh\nprintf \'%s\\n\' "$@" > "$FAKE_GH_ARGV"\nprintf \'%s\' "$FAKE_GH_STDOUT"\n')
    script.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("FAKE_GH_ARGV", str(argv_log))
    return argv_log


def test_dispatch_through_the_real_gh_wrapper_accepts_plain_text_output(fake_gh, monkeypatch):
    monkeypatch.setenv("FAKE_GH_STDOUT", "Created workflow_dispatch event for release-v2.yml at feat/x\n")
    launch.dispatch(REPOSITORY, BRANCH, "reuse")
    assert fake_gh.read_text(encoding="utf-8").split()[:3] == ["workflow", "run", "release-v2.yml"]


def test_api_calls_through_the_real_gh_wrapper_still_parse_json(fake_gh, monkeypatch):
    monkeypatch.setenv("FAKE_GH_STDOUT", '{"workflow_runs": []}')
    assert launch.gh("api", "repos/x/y") == {"workflow_runs": []}
