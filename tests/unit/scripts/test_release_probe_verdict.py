from __future__ import annotations

import pytest

from scripts import release_probe_verdict as verdict

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPOSITORY = "owner/repo"
HEAD = "a" * 40
IDS = {"reuse": 101, "fallback": 102, "guarded-change": 103}
EXTERNAL_ID = "reuse=101;fallback=102;guarded-change=103"


class FakeGh:
    def __init__(self, runs: dict[int, tuple[str, str | None]] | None = None, checks: list[dict] | None = None):
        self.runs = {101: ("completed", "success"), 102: ("completed", "success"), 103: ("completed", "success")}
        self.runs.update(runs or {})
        self.checks = checks if checks is not None else [self.check()]
        self.patched: list[dict] = []
        self.event = "workflow_dispatch"
        self.overrides: dict[int, dict] = {}

    @staticmethod
    def check(status: str = "in_progress", external_id: str | None = EXTERNAL_ID, check_id: int = 9) -> dict:
        return {"id": check_id, "name": "release-v2 probe", "status": status, "external_id": external_id}

    def __call__(self, *args: str):
        if "--method" in args:
            assert args[args.index("--method") + 1] == "PATCH"
            fields = {}
            rest = list(args[4:])
            while rest:
                assert rest.pop(0) == "-f"
                key, _, value = rest.pop(0).partition("=")
                fields[key] = value
            self.patched.append({"path": args[3], **fields})
            return {}
        path = args[1]
        if "/check-runs" in path:
            assert f"commits/{HEAD}/check-runs" in path and "check_name=release-v2%20probe" in path
            return {"check_runs": self.checks}
        run_id = int(path.rsplit("/", 1)[1])
        status, conclusion = self.runs[run_id]
        return {
            "id": run_id,
            "status": status,
            "conclusion": conclusion,
            "event": self.event,
            "head_sha": HEAD,
            "path": ".github/workflows/release-v2.yml",
            "html_url": f"https://github.com/{REPOSITORY}/actions/runs/{run_id}",
            **self.overrides.get(run_id, {}),
        }


@pytest.mark.parametrize(
    "override",
    [
        {"path": ".github/workflows/ci.yml"},
        {"event": "push"},
        {"head_sha": "b" * 40},
    ],
)
def test_a_recorded_run_that_is_not_a_release_v2_dispatch_at_the_check_head_fails_the_check(override):
    fake = FakeGh()
    fake.overrides[102] = override
    _settle(fake)
    (patch,) = fake.patched
    assert patch["conclusion"] == "failure"
    assert f"- fallback: https://github.com/{REPOSITORY}/actions/runs/102" in patch["output[summary]"]


def test_a_recorded_run_path_carrying_its_ref_still_counts():
    fake = FakeGh()
    fake.overrides[102] = {"path": ".github/workflows/release-v2.yml@refs/heads/feat/x"}
    _settle(fake)
    assert fake.patched[0]["conclusion"] == "success"


def _settle(fake: FakeGh, run_id: int = 103) -> str:
    return verdict.settle(REPOSITORY, run_id, fake)


def test_all_three_variants_succeeding_completes_the_check_as_success():
    fake = FakeGh()
    assert "success" in _settle(fake)
    (patch,) = fake.patched
    assert patch["path"] == f"repos/{REPOSITORY}/check-runs/9"
    assert (patch["status"], patch["conclusion"]) == ("completed", "success")


@pytest.mark.parametrize("variant", sorted(IDS))
@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "timed_out", "skipped", None])
def test_one_non_successful_variant_completes_the_check_as_failure_naming_it(variant, conclusion):
    fake = FakeGh({IDS[variant]: ("completed", conclusion)})
    _settle(fake, IDS[variant])
    (patch,) = fake.patched
    assert patch["conclusion"] == "failure"
    assert (
        f"- {variant}: {conclusion} (https://github.com/{REPOSITORY}/actions/runs/{IDS[variant]})"
        in patch["output[summary]"]
    )
    for other in set(IDS) - {variant}:
        assert f"- {other}:" not in patch["output[summary]"]


@pytest.mark.parametrize("status", ["queued", "in_progress", "waiting", "pending"])
def test_a_variant_still_running_leaves_the_check_untouched(status):
    fake = FakeGh({102: (status, None)})
    assert "waiting" in _settle(fake)
    assert fake.patched == []


def test_a_queued_variant_never_counts_as_success_even_when_the_others_failed_fast():
    fake = FakeGh({101: ("completed", "failure"), 102: ("queued", None)})
    _settle(fake, 101)
    assert fake.patched == []


def test_a_check_that_is_already_completed_is_left_alone():
    fake = FakeGh(checks=[FakeGh.check(status="completed")])
    assert "ignored" in _settle(fake)
    assert fake.patched == []


def test_a_run_that_belongs_to_no_probe_check_is_ignored():
    fake = FakeGh(
        checks=[FakeGh.check(external_id="reuse=1;fallback=2;guarded-change=3"), FakeGh.check(external_id=None)]
    )
    assert "ignored" in _settle(fake)
    assert fake.patched == []


def test_a_run_with_no_check_on_its_head_is_ignored():
    fake = FakeGh(checks=[])
    assert "ignored" in _settle(fake)
    assert fake.patched == []


def test_a_run_that_was_not_dispatched_is_ignored():
    fake = FakeGh()
    fake.event = "push"
    assert "ignored" in _settle(fake)
    assert fake.patched == []


def test_a_run_that_has_not_completed_is_ignored():
    fake = FakeGh({103: ("in_progress", None)})
    assert "ignored" in _settle(fake)
    assert fake.patched == []


def test_only_the_check_recording_this_run_is_completed():
    other = FakeGh.check(external_id="reuse=1;fallback=2;guarded-change=3", check_id=5)
    fake = FakeGh(checks=[other, FakeGh.check(check_id=9)])
    _settle(fake)
    assert [patch["path"] for patch in fake.patched] == [f"repos/{REPOSITORY}/check-runs/9"]


@pytest.mark.parametrize(
    "external_id",
    [None, "", "reuse=1", "reuse=1;fallback=x;guarded-change=3", "reuse=1;fallback=2;other=3"],
)
def test_a_malformed_external_id_records_nothing(external_id):
    assert verdict.parse_external_id(external_id) == {}


def test_one_run_id_recorded_for_every_variant_records_nothing():
    assert verdict.parse_external_id("reuse=7;fallback=7;guarded-change=7") == {}
    assert verdict.parse_external_id("reuse=7;fallback=8;guarded-change=7") == {}


def test_reconcile_completes_a_check_whose_runs_all_finished_before_it_existed():
    fake = FakeGh()
    assert "success" in verdict.reconcile(REPOSITORY, 9, IDS, HEAD, fake)
    (patch,) = fake.patched
    assert patch["path"] == f"repos/{REPOSITORY}/check-runs/9"
    assert patch["conclusion"] == "success"


def test_reconcile_head_completes_an_in_progress_check_whose_runs_all_finished():
    fake = FakeGh()
    assert "success" in verdict.reconcile_head(REPOSITORY, HEAD, fake)
    (patch,) = fake.patched
    assert (patch["path"], patch["conclusion"]) == (f"repos/{REPOSITORY}/check-runs/9", "success")


def test_reconcile_head_fails_the_check_naming_a_failed_variant():
    fake = FakeGh({102: ("completed", "failure")})
    assert "failure" in verdict.reconcile_head(REPOSITORY, HEAD, fake)
    assert "- fallback: failure" in fake.patched[0]["output[summary]"]


def test_reconcile_head_leaves_the_check_while_a_run_is_unfinished():
    fake = FakeGh({103: ("in_progress", None)})
    assert "waiting" in verdict.reconcile_head(REPOSITORY, HEAD, fake)
    assert fake.patched == []


def test_reconcile_head_reports_no_check_so_the_caller_dispatches():
    fake = FakeGh(checks=[])
    assert verdict.reconcile_head(REPOSITORY, HEAD, fake) is None
    assert fake.patched == []


def test_reconcile_head_ignores_a_completed_check_so_a_deliberate_rerun_dispatches_again():
    fake = FakeGh(checks=[FakeGh.check(status="completed")])
    assert verdict.reconcile_head(REPOSITORY, HEAD, fake) is None
    assert fake.patched == []


def test_reconcile_head_ignores_a_check_without_recorded_runs():
    fake = FakeGh(checks=[FakeGh.check(external_id=None)])
    assert verdict.reconcile_head(REPOSITORY, HEAD, fake) is None


def test_reconcile_head_applies_the_guards_to_the_recorded_runs():
    fake = FakeGh()
    fake.overrides[101] = {"path": ".github/workflows/ci.yml"}
    assert "failure" in verdict.reconcile_head(REPOSITORY, HEAD, fake)


def test_reconcile_leaves_the_check_while_a_run_is_unfinished():
    fake = FakeGh({102: ("queued", None)})
    assert "waiting" in verdict.reconcile(REPOSITORY, 9, IDS, HEAD, fake)
    assert fake.patched == []


def test_the_external_id_the_launcher_writes_round_trips():
    assert verdict.parse_external_id(EXTERNAL_ID) == IDS


def test_the_check_pages_are_read_until_a_short_page(monkeypatch):
    monkeypatch.setattr(verdict, "CHECKS_PAGE", 2)
    pages = {1: [FakeGh.check(check_id=1), FakeGh.check(check_id=2)], 2: [FakeGh.check(check_id=3)]}
    seen = []

    def runner(*args):
        page = int(args[1].split("&page=")[1].split("&")[0])
        seen.append(page)
        return {"check_runs": pages[page]}

    assert len(verdict.probe_checks(REPOSITORY, HEAD, runner)) == 3
    assert seen == [1, 2]


def test_main_reports_an_api_error(monkeypatch, capsys):
    def boom(repository, run_id, runner=None):
        raise RuntimeError("gh api failed: HTTP 403")

    monkeypatch.setattr(verdict, "settle", boom)
    assert verdict.main(["--repository", REPOSITORY, "--run-id", "103"]) == 1
    assert "HTTP 403" in capsys.readouterr().err
