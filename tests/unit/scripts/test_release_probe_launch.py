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
        self.conclusions = {
            "reuse": "success",
            "fallback": "success",
            "guarded-change": "success",
            **(conclusions or {}),
        }
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


def test_every_variant_is_dispatched_with_the_synthetic_tag_from_the_pull_request_branch(capsys):
    fake = FakeGh()
    assert _run(fake) == 0
    assert [next(a for a in call if a.startswith("probe=")) for call in fake.dispatched] == [
        "probe=reuse",
        "probe=fallback",
        "probe=guarded-change",
    ]
    for call in fake.dispatched:
        assert "tag=v0.0.0rc0" in call
        assert call[call.index("--ref") + 1] == BRANCH
        assert call[call.index("--repo") + 1] == REPOSITORY
        assert call[2] == "release-v2.yml"
    assert "reuse probe: success" in capsys.readouterr().out


@pytest.mark.parametrize("failing", ["reuse", "fallback", "guarded-change"])
@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "timed_out", None])
def test_either_variant_failing_fails_the_job(failing, conclusion):
    assert _run(FakeGh({failing: conclusion})) == 1


def test_a_variant_that_never_appears_fails_and_names_the_commit(capsys):
    fake = FakeGh()
    fake.head_sha = "b" * 40
    with pytest.raises(RuntimeError, match="reuse probe never started: no release-v2.yml run at aaaaaaaaaa"):
        launch.launch(REPOSITORY, BRANCH, HEAD, "reuse", set(), runner=fake, sleep=lambda _: None, attempts=3)


def test_runs_that_existed_before_the_dispatch_are_never_adopted():
    fake = FakeGh()
    fake.runs[7] = {"id": 7, "head_sha": HEAD}
    run_id = launch.launch(REPOSITORY, BRANCH, HEAD, "reuse", {7}, runner=fake, sleep=lambda _: None)
    assert run_id != 7


def test_the_variants_never_share_a_run():
    fake = FakeGh()
    assert _run(fake) == 0
    assert len({*fake.variant_of}) == 3
    assert sorted(fake.polls) == sorted(fake.variant_of)


def test_a_run_that_outlives_the_timeout_fails_and_is_cancelled():
    fake = FakeGh(pending_polls=10**6)
    ticks = iter(range(0, 10**6, 100))
    with pytest.raises(RuntimeError, match="did not finish"):
        launch.run_probes(
            REPOSITORY, BRANCH, HEAD, runner=fake, sleep=lambda _: None, clock=lambda: next(ticks), timeout=250.0
        )
    assert sorted(fake.cancelled) == sorted(fake.variant_of)


def test_every_variant_is_bounded_by_one_overall_deadline(monkeypatch):
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
    assert budgets == [1000.0, 600.0, 200.0]


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
        "tests/uat/_cli.py",
        "tests/uat/config.py",
        "Makefile",
        "make/uat.mk",
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


def test_a_renamed_file_counts_under_its_previous_path_too():
    rows = [
        {"filename": "tests/uat/configs/gate-02.yaml", "previous_filename": "tests/uat/configs/release-gate-02.yaml"},
        {"filename": "README.md"},
    ]
    files = launch.changed_files(REPOSITORY, 5, lambda *args: rows)
    assert files == ["tests/uat/configs/gate-02.yaml", "tests/uat/configs/release-gate-02.yaml", "README.md"]
    assert launch.touches_probed_paths(files) == ["tests/uat/configs/release-gate-02.yaml"]


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
    body = (
        "import os, sys\n"
        "with open(os.environ['FAKE_GH_ARGV'], 'w', encoding='utf-8') as stream:\n"
        "    stream.write('\\n'.join(sys.argv[1:]) + '\\n')\n"
        "sys.stdout.write(os.environ['FAKE_GH_STDOUT'])\n"
    )
    if sys.platform == "win32":
        (bin_dir / "gh_fake.py").write_text(body, encoding="utf-8")
        executable = bin_dir / "gh.cmd"
        executable.write_bytes(f'@"{sys.executable}" "{bin_dir / "gh_fake.py"}" %*\r\n'.encode())
    else:
        executable = bin_dir / "gh"
        executable.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
        executable.chmod(0o755)
    monkeypatch.setattr(launch, "GH_EXECUTABLE", str(executable))
    monkeypatch.setenv("FAKE_GH_ARGV", str(argv_log))
    return argv_log


def test_dispatch_through_the_real_gh_wrapper_accepts_plain_text_output(fake_gh, monkeypatch):
    monkeypatch.setenv("FAKE_GH_STDOUT", "Created workflow_dispatch event for release-v2.yml at feat/x\n")
    launch.dispatch(REPOSITORY, BRANCH, "reuse")
    assert fake_gh.read_text(encoding="utf-8").split()[:3] == ["workflow", "run", "release-v2.yml"]


def test_api_calls_through_the_real_gh_wrapper_still_parse_json(fake_gh, monkeypatch):
    monkeypatch.setenv("FAKE_GH_STDOUT", '{"workflow_runs": []}')
    assert launch.gh("api", "repos/x/y") == {"workflow_runs": []}


class ScriptedRun:
    def __init__(self, statuses: list[tuple[str, str | None]]):
        self.statuses = statuses
        self.polls = 0

    def __call__(self, *args: str):
        status, conclusion = self.statuses[min(self.polls, len(self.statuses) - 1)]
        self.polls += 1
        return {"id": 1, "status": status, "conclusion": conclusion, "html_url": "url/1"}


class TickClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = 0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps += 1
        self.now += seconds


def _wait(statuses, *, timeout=1000.0, interval=20.0):
    clock = TickClock()
    runner = ScriptedRun(statuses)
    result = launch.wait_for(
        REPOSITORY, 1, runner=runner, sleep=clock.sleep, clock=clock, timeout=timeout, interval=interval
    )
    return result, clock


def test_a_run_queued_for_the_whole_wait_reports_still_queued_not_a_run_failure():
    with pytest.raises(RuntimeError) as caught:
        _wait([("queued", None)])
    message = str(caught.value)
    assert "still queued after 1000s" in message and "never began running" in message
    assert "not a release-v2 failure" in message
    assert "did not finish" not in message


def test_a_never_started_wait_is_finite():
    clock = TickClock()
    runner = ScriptedRun([("queued", None)])
    with pytest.raises(RuntimeError):
        launch.wait_for(REPOSITORY, 1, runner=runner, sleep=clock.sleep, clock=clock, timeout=1000.0, interval=20.0)
    assert clock.now == 1000.0
    assert runner.polls == 51


def test_a_run_that_starts_but_never_ends_reports_queue_and_run_time_separately():
    statuses = [("queued", None)] * 10 + [("in_progress", None)]
    with pytest.raises(RuntimeError) as caught:
        _wait(statuses, timeout=1000.0)
    message = str(caught.value)
    assert "started after 200s queued" in message
    assert "800s of run time" in message
    assert "1000s total wait" in message
    assert "never began running" not in message


@pytest.mark.parametrize("conclusion", ["success", "failure", "cancelled"])
def test_a_run_that_queues_then_starts_then_finishes_returns_its_conclusion(conclusion):
    statuses = [("queued", None)] * 30 + [("in_progress", None)] * 5 + [("completed", conclusion)]
    run, clock = _wait(statuses, timeout=1000.0)
    assert run["conclusion"] == conclusion
    assert clock.now == 700.0


@pytest.mark.parametrize("pending", ["waiting", "pending", "requested"])
def test_every_pre_start_status_counts_as_queued(pending):
    with pytest.raises(RuntimeError, match=f"still {pending} after"):
        _wait([(pending, None)], timeout=100.0)


def test_a_dispatched_run_that_is_not_listed_at_first_is_found_on_a_later_poll():
    class Delayed(FakeGh):
        def __init__(self):
            super().__init__()
            self.list_calls = 0

        def __call__(self, *args):
            if len(args) > 1 and "/actions/workflows/release-v2.yml/runs" in args[1]:
                self.list_calls += 1
                if self.list_calls < 4:
                    return {"workflow_runs": []}
            return super().__call__(*args)

    fake = Delayed()
    clock = TickClock()
    run_id = launch.launch(REPOSITORY, BRANCH, HEAD, "reuse", set(), runner=fake, sleep=clock.sleep)
    assert run_id == 101
    assert clock.now == 15.0


def test_a_run_that_fails_after_a_long_queue_still_fails_the_job(capsys):
    class Queued(FakeGh):
        def __call__(self, *args):
            result = super().__call__(*args)
            if isinstance(result, dict) and result.get("status") == "in_progress":
                return {**result, "status": "queued"}
            return result

    fake = Queued({"fallback": "failure"}, pending_polls=3)
    assert _run(fake) == 1
    assert "fallback probe: failure" in capsys.readouterr().out


def test_a_run_still_queued_at_the_deadline_fails_the_job_and_cancels_the_probes():
    fake = FakeGh(pending_polls=10**6)

    def queued(*args):
        result = fake(*args)
        if isinstance(result, dict) and result.get("status") == "in_progress":
            return {**result, "status": "queued"}
        return result

    ticks = iter(range(0, 10**6, 100))
    with pytest.raises(RuntimeError, match="still queued after 200s of waiting and never began running"):
        launch.run_probes(
            REPOSITORY, BRANCH, HEAD, runner=queued, sleep=lambda _: None, clock=lambda: next(ticks), timeout=250.0
        )
    assert sorted(fake.cancelled) == sorted(fake.variant_of)


def test_a_dispatch_that_never_becomes_visible_gives_up_after_a_finite_wait():
    fake = FakeGh()
    fake.head_sha = "b" * 40
    clock = TickClock()
    with pytest.raises(RuntimeError, match="never started"):
        launch.launch(
            REPOSITORY, BRANCH, HEAD, "reuse", set(), runner=fake, sleep=clock.sleep, attempts=30, interval=5.0
        )
    assert clock.now == 150.0
