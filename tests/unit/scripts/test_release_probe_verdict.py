from __future__ import annotations

import pytest

from scripts import release_probe_verdict as verdict

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPOSITORY = "owner/repo"
HEAD = "a" * 40
IDS = {"reuse": 101, "fallback": 102, "guarded-change": 103}
EXTERNAL_ID = "reuse=101;fallback=102;guarded-change=103"
NEEDS_OK = {
    "admit": {"result": "success", "outputs": {}},
    "publish": {"result": "skipped", "outputs": {}},
    "release-outcome": {"result": "success", "outputs": {}},
}


def job(name: str, status: str = "completed", conclusion: str | None = "success") -> dict:
    return {"name": name, "status": status, "conclusion": conclusion if status == "completed" else None}


def jobs(*, outcome: str = "success", report: str = "in_progress", extra: list[dict] | None = None) -> list[dict]:
    return [
        job("admit"),
        job("publish", conclusion="skipped"),
        *(extra or []),
        job("release outcome", conclusion=outcome),
        job("probe report", status=report),
    ]


def run_spec(*, status: str = "in_progress", conclusion: str | None = None, rows: list[dict] | None = None) -> dict:
    return {"status": status, "conclusion": conclusion, "jobs": rows if rows is not None else jobs()}


class FakeGh:
    def __init__(self, runs: dict[int, dict] | None = None, checks: list[dict] | None = None):
        self.runs = {101: run_spec(), 102: run_spec(), 103: run_spec()}
        self.runs.update(runs or {})
        self.checks = checks if checks is not None else [self.check()]
        self.patched: list[dict] = []
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
            for check in self.checks:
                if args[3].endswith(f"/{check['id']}"):
                    check["status"] = "completed"
            return {}
        path = args[1]
        if "/check-runs" in path:
            assert f"commits/{HEAD}/check-runs" in path and "check_name=release-v2%20probe" in path
            return {"check_runs": self.checks}
        if "/jobs?" in path:
            run_id = int(path.split("/actions/runs/")[1].split("/")[0])
            assert "filter=latest" in path
            return {"jobs": self.runs[run_id]["jobs"]}
        run_id = int(path.rsplit("/", 1)[1])
        spec = self.runs[run_id]
        return {
            "id": run_id,
            "status": spec["status"],
            "conclusion": spec["conclusion"],
            "event": "workflow_dispatch",
            "head_sha": HEAD,
            "path": ".github/workflows/release-v2.yml",
            "html_url": f"https://github.com/{REPOSITORY}/actions/runs/{run_id}",
            **self.overrides.get(run_id, {}),
        }


def _report(fake: FakeGh, run_id: int = 103, needs: dict | None = None) -> str:
    return verdict.report(REPOSITORY, run_id, HEAD, NEEDS_OK if needs is None else needs, fake)


def test_every_substantive_job_green_in_all_three_runs_completes_the_check_as_success():
    fake = FakeGh()
    assert "success" in _report(fake)
    (patch,) = fake.patched
    assert patch["path"] == f"repos/{REPOSITORY}/check-runs/9"
    assert (patch["status"], patch["conclusion"]) == ("completed", "success")
    for run_id in IDS.values():
        assert f"actions/runs/{run_id}" in patch["output[summary]"]


def test_two_variants_finishing_together_each_see_the_other_run_finished_and_agree():
    first, second = FakeGh(), FakeGh()
    assert "success" in _report(first, 102)
    assert "success" in _report(second, 103)
    assert [p["conclusion"] for p in first.patched + second.patched] == ["success", "success"]
    assert first.patched[0]["output[summary]"] == second.patched[0]["output[summary]"]


def test_the_second_report_of_the_same_check_finds_it_completed_and_changes_nothing():
    fake = FakeGh()
    _report(fake, 102)
    assert "ignored" in _report(fake, 103)
    assert len(fake.patched) == 1


def test_another_run_whose_substantive_jobs_are_done_counts_as_finished_while_its_report_job_runs():
    fake = FakeGh({101: run_spec(status="in_progress", rows=jobs(report="in_progress"))})
    assert "success" in _report(fake, 103)


def test_a_completed_run_counts_as_finished():
    fake = FakeGh({101: run_spec(status="completed", conclusion="success")})
    assert "success" in _report(fake)


@pytest.mark.parametrize("status", ["queued", "in_progress", "waiting", "pending"])
def test_a_substantive_job_that_has_not_completed_leaves_the_check_untouched(status):
    rows = jobs(extra=[job("uat-stage1 sqlite", status=status)])
    fake = FakeGh({102: run_spec(rows=rows)})
    assert "waiting" in _report(fake)
    assert fake.patched == []


def test_a_run_whose_release_outcome_job_has_not_completed_is_not_finished_even_if_the_listed_jobs_are():
    rows = [job("admit"), job("release outcome", status="queued")]
    fake = FakeGh({102: run_spec(rows=rows)})
    assert "waiting" in _report(fake)
    assert fake.patched == []


def test_a_run_whose_listed_jobs_lack_the_release_outcome_job_is_not_finished():
    fake = FakeGh({102: run_spec(rows=[job("admit"), job("probe report", status="in_progress")])})
    assert "waiting" in _report(fake)
    assert fake.patched == []


def test_a_run_with_no_jobs_yet_is_not_finished():
    fake = FakeGh({102: run_spec(rows=[])})
    assert "waiting" in _report(fake)
    assert fake.patched == []


@pytest.mark.parametrize("variant", ["reuse", "fallback"])
@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "timed_out"])
def test_a_failed_substantive_job_in_another_run_fails_the_check_naming_the_variant_and_job(variant, conclusion):
    rows = jobs(extra=[job("install-matrix (ubuntu)", conclusion=conclusion)])
    fake = FakeGh({IDS[variant]: run_spec(rows=rows)})
    _report(fake)
    (patch,) = fake.patched
    assert patch["conclusion"] == "failure"
    summary = patch["output[summary]"]
    assert (
        f"- {variant}: install-matrix (ubuntu): {conclusion} (https://github.com/{REPOSITORY}/actions/runs/" in summary
    )
    for other in set(IDS) - {variant}:
        assert f"- {other}:" not in summary


@pytest.mark.parametrize("result", ["failure", "cancelled"])
def test_a_failed_job_of_the_current_run_fails_the_check_through_its_needs(result):
    needs = {**NEEDS_OK, "release-uat": {"result": result, "outputs": {}}}
    fake = FakeGh()
    _report(fake, 103, needs)
    (patch,) = fake.patched
    assert patch["conclusion"] == "failure"
    assert f"- guarded-change: release-uat: {result}" in patch["output[summary]"]


def test_skipped_jobs_never_fail_the_check():
    needs = {**NEEDS_OK, "uat-stage1": {"result": "skipped", "outputs": {}}}
    fake = FakeGh({101: run_spec(rows=jobs(extra=[job("uat-stage1 x", conclusion="skipped")]))})
    _report(fake, 103, needs)
    assert fake.patched[0]["conclusion"] == "success"


def test_the_report_job_of_another_run_is_never_judged():
    rows = jobs(report="completed")
    rows[-1]["conclusion"] = "failure"
    fake = FakeGh({101: run_spec(rows=rows)})
    _report(fake)
    assert fake.patched[0]["conclusion"] == "success"


def test_a_run_that_completed_without_any_job_and_did_not_succeed_fails_the_check():
    fake = FakeGh({101: run_spec(status="completed", conclusion="cancelled", rows=[])})
    _report(fake)
    assert "- reuse: run: cancelled" in fake.patched[0]["output[summary]"]


@pytest.mark.parametrize("conclusion", ["cancelled", "failure", "timed_out", "startup_failure"])
def test_a_completed_run_whose_conclusion_is_not_success_fails_the_check_even_when_every_listed_job_is_green(
    conclusion,
):
    fake = FakeGh({101: run_spec(status="completed", conclusion=conclusion, rows=jobs(report="completed"))})
    _report(fake)
    assert f"- reuse: run: {conclusion}" in fake.patched[0]["output[summary]"]


def test_a_run_that_failed_only_because_its_own_report_job_failed_is_judged_on_its_substantive_jobs():
    rows = jobs(report="completed")
    rows[-1]["conclusion"] = "failure"
    fake = FakeGh({101: run_spec(status="completed", conclusion="failure", rows=rows)})
    _report(fake)
    assert fake.patched[0]["conclusion"] == "success"


def test_a_check_that_is_already_completed_is_left_alone():
    fake = FakeGh(checks=[FakeGh.check(status="completed")])
    assert "ignored" in _report(fake)
    assert fake.patched == []


def test_a_run_that_belongs_to_no_probe_check_is_ignored():
    fake = FakeGh(
        checks=[FakeGh.check(external_id="reuse=1;fallback=2;guarded-change=3"), FakeGh.check(external_id=None)]
    )
    assert "ignored" in _report(fake)
    assert fake.patched == []


def test_a_run_with_no_check_on_its_head_is_ignored():
    fake = FakeGh(checks=[])
    assert "ignored" in _report(fake)
    assert fake.patched == []


def test_only_the_check_recording_this_run_is_completed():
    other = FakeGh.check(external_id="reuse=1;fallback=2;guarded-change=3", check_id=5)
    fake = FakeGh(checks=[other, FakeGh.check(check_id=9)])
    _report(fake)
    assert [patch["path"] for patch in fake.patched] == [f"repos/{REPOSITORY}/check-runs/9"]


@pytest.mark.parametrize(
    "override",
    [{"path": ".github/workflows/ci.yml"}, {"event": "push"}, {"head_sha": "b" * 40}],
)
def test_a_recorded_run_that_is_not_a_release_v2_dispatch_at_the_check_head_fails_the_check(override):
    fake = FakeGh()
    fake.overrides[102] = override
    _report(fake)
    (patch,) = fake.patched
    assert patch["conclusion"] == "failure"
    assert f"- fallback: https://github.com/{REPOSITORY}/actions/runs/102" in patch["output[summary]"]


def test_a_recorded_run_path_carrying_its_ref_still_counts():
    fake = FakeGh()
    fake.overrides[102] = {"path": ".github/workflows/release-v2.yml@refs/heads/feat/x"}
    _report(fake)
    assert fake.patched[0]["conclusion"] == "success"


def test_reconcile_head_completes_an_in_progress_check_whose_runs_all_finished():
    fake = FakeGh()
    assert "success" in verdict.reconcile_head(REPOSITORY, HEAD, fake)
    (patch,) = fake.patched
    assert (patch["path"], patch["conclusion"]) == (f"repos/{REPOSITORY}/check-runs/9", "success")


def test_reconcile_head_uses_the_same_job_based_finished_rule():
    fake = FakeGh({102: run_spec(rows=jobs(extra=[job("uat-stage2 x", status="in_progress")]))})
    assert "waiting" in verdict.reconcile_head(REPOSITORY, HEAD, fake)
    assert fake.patched == []


def test_reconcile_head_fails_the_check_naming_a_failed_variant():
    fake = FakeGh({102: run_spec(rows=jobs(outcome="failure"))})
    assert "failure" in verdict.reconcile_head(REPOSITORY, HEAD, fake)
    assert "- fallback: release outcome: failure" in fake.patched[0]["output[summary]"]


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


@pytest.mark.parametrize(
    "external_id",
    [None, "", "reuse=1", "reuse=1;fallback=x;guarded-change=3", "reuse=1;fallback=2;other=3"],
)
def test_a_malformed_external_id_records_nothing(external_id):
    assert verdict.parse_external_id(external_id) == {}


def test_one_run_id_recorded_for_every_variant_records_nothing():
    assert verdict.parse_external_id("reuse=7;fallback=7;guarded-change=7") == {}
    assert verdict.parse_external_id("reuse=7;fallback=8;guarded-change=7") == {}


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


def test_the_job_pages_are_read_until_a_short_page(monkeypatch):
    monkeypatch.setattr(verdict, "JOBS_PAGE", 2)
    pages = {1: [job("a"), job("b")], 2: [job("c")]}
    seen = []

    def runner(*args):
        page = int(args[1].split("&page=")[1])
        seen.append(page)
        return {"jobs": pages[page]}

    assert [row["name"] for row in verdict.run_jobs(REPOSITORY, 5, runner)] == ["a", "b", "c"]
    assert seen == [1, 2]


def test_main_completes_the_check_from_the_needs_in_the_environment(monkeypatch, capsys):
    monkeypatch.setenv("NEEDS_JSON", '{"admit": {"result": "success", "outputs": {}}}')
    seen = []
    monkeypatch.setattr(
        verdict, "report", lambda repository, run_id, head, needs: seen.append((run_id, head, needs)) or "ok"
    )
    assert verdict.main(["--repository", REPOSITORY, "--run", "103", "--head-sha", HEAD]) == 0
    assert seen == [(103, HEAD, {"admit": {"result": "success", "outputs": {}}})]
    assert capsys.readouterr().out.strip() == "ok"


def test_main_reports_an_api_error(monkeypatch, capsys):
    def boom(repository, run_id, head, needs):
        raise RuntimeError("gh api failed: HTTP 403")

    monkeypatch.setenv("NEEDS_JSON", "{}")
    monkeypatch.setattr(verdict, "report", boom)
    assert verdict.main(["--repository", REPOSITORY, "--run", "103", "--head-sha", HEAD]) == 1
    assert "HTTP 403" in capsys.readouterr().err


def test_main_rejects_unreadable_needs(monkeypatch, capsys):
    monkeypatch.setenv("NEEDS_JSON", "not json")
    assert verdict.main(["--repository", REPOSITORY, "--run", "103", "--head-sha", HEAD]) == 1
    assert "::error::" in capsys.readouterr().err
