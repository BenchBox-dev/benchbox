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
    def __init__(self, *, dispatch_fails_at: str | None = None, cancel_fails=False):
        self.dispatch_fails_at = dispatch_fails_at
        self.cancel_fails = cancel_fails
        self.runs: dict[int, dict] = {}
        self.dispatched: list[list[str]] = []
        self.cancelled: list[int] = []
        self.checks: list[dict] = []
        self.next_id = 100
        self.head_sha = HEAD

    def __call__(self, *args: str):
        if args[:2] == ("workflow", "run"):
            variant = next(a for a in args if a.startswith("probe=")).removeprefix("probe=")
            if variant == self.dispatch_fails_at:
                raise RuntimeError("gh workflow run failed: HTTP 422")
            self.dispatched.append(list(args))
            self.next_id += 1
            self.runs[self.next_id] = {"id": self.next_id, "head_sha": self.head_sha}
            return None
        if args[0] == "api" and "--method" in args and args[args.index("--method") + 1] == "POST":
            if args[3].endswith("/check-runs"):
                fields = {}
                rest = list(args[4:])
                while rest:
                    assert rest.pop(0) == "-f"
                    key, _, value = rest.pop(0).partition("=")
                    fields[key] = value
                self.checks.append(fields)
                return {"id": 1}
            if self.cancel_fails:
                raise RuntimeError("forbidden")
            self.cancelled.append(int(args[-1].split("/")[-2]))
            return None
        return {"workflow_runs": list(self.runs.values())}


def _report(fake: FakeGh, files: list[str]) -> int:
    return launch.report_probe(REPOSITORY, BRANCH, HEAD, files, runner=fake, sleep=lambda _: None)


def test_every_variant_is_dispatched_with_the_synthetic_tag_from_the_pull_request_branch():
    fake = FakeGh()
    assert _report(fake, ["scripts/release_flow.py"]) == 0
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


def test_a_touched_pull_request_gets_an_in_progress_check_recording_the_variant_run_ids():
    fake = FakeGh()
    assert _report(fake, ["scripts/release_flow.py"]) == 0
    (check,) = fake.checks
    assert check["name"] == "release-v2 probe"
    assert check["head_sha"] == HEAD
    assert check["status"] == "in_progress"
    assert "conclusion" not in check
    assert check["external_id"] == "reuse=101;fallback=102;guarded-change=103"
    for run_id in (101, 102, 103):
        assert f"https://github.com/{REPOSITORY}/actions/runs/{run_id}" in check["output[summary]"]


def test_an_untouched_pull_request_gets_a_completed_success_check_and_dispatches_nothing(capsys):
    fake = FakeGh()
    assert _report(fake, ["README.md"]) == 0
    assert fake.dispatched == []
    (check,) = fake.checks
    assert check["name"] == "release-v2 probe"
    assert (check["status"], check["conclusion"]) == ("completed", "success")
    assert "No release input changed" in check["output[title]"]
    assert "external_id" not in check
    assert "nothing to probe" in capsys.readouterr().out


def test_a_dispatch_error_gets_a_completed_failure_check_naming_the_error_and_cancels_started_runs():
    fake = FakeGh(dispatch_fails_at="guarded-change")
    assert _report(fake, ["scripts/release_flow.py"]) == 1
    (check,) = fake.checks
    assert (check["status"], check["conclusion"]) == ("completed", "failure")
    assert "HTTP 422" in check["output[summary]"]
    assert sorted(fake.cancelled) == [101, 102]


def test_a_failed_cancel_is_reported_without_hiding_the_dispatch_failure(capsys):
    fake = FakeGh(dispatch_fails_at="fallback", cancel_fails=True)
    assert _report(fake, ["scripts/release_flow.py"]) == 1
    assert "could not cancel" in capsys.readouterr().err
    assert fake.checks[0]["conclusion"] == "failure"


def test_a_variant_that_never_appears_fails_and_names_the_commit():
    fake = FakeGh()
    fake.head_sha = "b" * 40
    with pytest.raises(RuntimeError, match="no release-v2.yml run at aaaaaaaaaa"):
        launch.launch(REPOSITORY, BRANCH, HEAD, "reuse", set(), runner=fake, sleep=lambda _: None, attempts=3)


def test_runs_that_existed_before_the_dispatch_are_never_adopted():
    fake = FakeGh()
    fake.runs[7] = {"id": 7, "head_sha": HEAD}
    run_id = launch.launch(REPOSITORY, BRANCH, HEAD, "reuse", {7}, runner=fake, sleep=lambda _: None)
    assert run_id != 7


def test_the_variants_never_share_a_run():
    fake = FakeGh()
    started = launch.start_probes(REPOSITORY, BRANCH, HEAD, runner=fake, sleep=lambda _: None)
    assert len(set(started.values())) == 3


def test_the_check_is_reconciled_right_after_it_is_created_with_its_id_and_the_run_ids():
    fake = FakeGh()
    calls = []
    launch.report_probe(
        REPOSITORY,
        BRANCH,
        HEAD,
        ["scripts/release_flow.py"],
        runner=fake,
        sleep=lambda _: None,
        reconcile=lambda check_id, started: calls.append((check_id, started)),
    )
    assert calls == [(1, {"reuse": 101, "fallback": 102, "guarded-change": 103})]


def test_an_untouched_pull_request_is_never_reconciled():
    fake = FakeGh()
    launch.report_probe(
        REPOSITORY,
        BRANCH,
        HEAD,
        ["README.md"],
        runner=fake,
        sleep=lambda _: None,
        reconcile=lambda *a: pytest.fail("no"),
    )


def test_a_failed_reconcile_leaves_the_in_progress_check_and_does_not_fail_the_job(capsys):
    fake = FakeGh()

    def reconcile(check_id, started):
        raise RuntimeError("gh api failed: HTTP 502")

    status = launch.report_probe(
        REPOSITORY, BRANCH, HEAD, ["scripts/release_flow.py"], runner=fake, sleep=lambda _: None, reconcile=reconcile
    )
    assert status == 0
    assert [check["status"] for check in fake.checks] == ["in_progress"]
    assert "HTTP 502" in capsys.readouterr().err


def test_an_existing_probe_check_outcome_stops_the_job_before_any_dispatch(capsys):
    fake = FakeGh()
    status = launch.report_probe(
        REPOSITORY,
        BRANCH,
        HEAD,
        ["scripts/release_flow.py"],
        runner=fake,
        sleep=lambda _: None,
        existing=lambda: "completed the probe check as success",
    )
    assert status == 0
    assert fake.dispatched == [] and fake.checks == []
    assert "completed the probe check as success" in capsys.readouterr().out


def test_no_existing_probe_check_dispatches_as_before():
    fake = FakeGh()
    status = launch.report_probe(
        REPOSITORY, BRANCH, HEAD, ["scripts/release_flow.py"], runner=fake, sleep=lambda _: None, existing=lambda: None
    )
    assert status == 0
    assert len(fake.dispatched) == 3
    assert [check["status"] for check in fake.checks] == ["in_progress"]


def test_an_untouched_pull_request_never_looks_for_an_existing_check():
    fake = FakeGh()
    launch.report_probe(
        REPOSITORY, BRANCH, HEAD, ["README.md"], runner=fake, sleep=lambda _: None, existing=lambda: pytest.fail("no")
    )
    assert [check["conclusion"] for check in fake.checks] == ["success"]


def test_the_launcher_no_longer_polls_or_waits():
    assert not hasattr(launch, "wait_for")
    assert not hasattr(launch, "run_probes")


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


def test_the_main_command_hands_the_changed_files_to_the_reporter(monkeypatch):
    monkeypatch.setattr(launch, "changed_files", lambda repository, number: ["scripts/release_flow.py"])
    called = []
    monkeypatch.setattr(launch, "report_probe", lambda *a, **k: called.append(a) or 0)
    argv = ["--repository", REPOSITORY, "--pull-request", "5", "--branch", BRANCH, "--head-sha", HEAD]
    assert launch.main(argv) == 0
    assert called == [(REPOSITORY, BRANCH, HEAD, ["scripts/release_flow.py"])]


def test_the_main_command_reports_an_api_failure_as_an_error_and_a_failed_check(monkeypatch, capsys):
    def boom(repository, number):
        raise RuntimeError("gh api failed: HTTP 403")

    posted = []
    monkeypatch.setattr(launch, "changed_files", boom)
    monkeypatch.setattr(launch, "fail_check", lambda repository, head, message: posted.append((head, message)))
    argv = ["--repository", REPOSITORY, "--pull-request", "5", "--branch", BRANCH, "--head-sha", HEAD]
    assert launch.main(argv) == 1
    assert "HTTP 403" in capsys.readouterr().err
    assert posted == [(HEAD, "gh api failed: HTTP 403")]


def test_the_fail_option_records_a_failed_check_without_dispatching(monkeypatch):
    posted = []
    monkeypatch.setattr(launch, "fail_check", lambda repository, head, message: posted.append((head, message)))
    assert launch.main(["--repository", REPOSITORY, "--head-sha", HEAD, "--fail", "graph broke"]) == 0
    assert posted == [(HEAD, "graph broke")]


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
