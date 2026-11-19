from __future__ import annotations

import pytest

from scripts import release_probe_launch as launch, release_probe_verdict as verdict

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPOSITORY = "owner/repo"
HEAD = "a" * 40
IDS = {"reuse": 101, "fallback": 102, "guarded-change": 103}
DESCRIPTION = "Probe running: reuse=101;fallback=102;guarded-change=103"
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
    def __init__(self, runs: dict[int, dict] | None = None, statuses: list[dict] | None = None):
        self.runs = {101: run_spec(), 102: run_spec(), 103: run_spec()}
        self.runs.update(runs or {})
        self.statuses = statuses if statuses is not None else [self.status()]
        self.posted: list[dict] = []
        self.overrides: dict[int, dict] = {}

    @staticmethod
    def status(
        state: str = "pending", description: str | None = DESCRIPTION, context: str = "release-v2 probe"
    ) -> dict:
        return {"context": context, "state": state, "description": description}

    def __call__(self, *args: str):
        if "--method" in args:
            assert args[args.index("--method") + 1] == "POST"
            assert args[3] == f"repos/{REPOSITORY}/statuses/{HEAD}"
            fields = {}
            rest = list(args[4:])
            while rest:
                assert rest.pop(0) == "-f"
                key, _, value = rest.pop(0).partition("=")
                fields[key] = value
            self.posted.append(fields)
            self.statuses.insert(
                0, {"context": fields["context"], "state": fields["state"], "description": fields["description"]}
            )
            return {}
        path = args[1]
        if "/statuses?" in path:
            assert path.startswith(f"repos/{REPOSITORY}/commits/{HEAD}/statuses?")
            return self.statuses
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


def test_every_substantive_job_green_in_all_three_runs_sets_the_status_to_success():
    fake = FakeGh()
    assert "success" in _report(fake)
    (patch,) = fake.posted
    assert (patch["context"], patch["state"], patch["description"]) == ("release-v2 probe", "success", "Probe passed")
    assert "target_url" not in patch


def test_two_variants_finishing_together_each_see_the_other_run_finished_and_agree():
    first, second = FakeGh(), FakeGh()
    assert "success" in _report(first, 102)
    assert "success" in _report(second, 103)
    assert [p["state"] for p in first.posted + second.posted] == ["success", "success"]
    assert first.posted[0] == second.posted[0]


def test_the_second_report_of_the_same_status_finds_it_final_and_changes_nothing():
    fake = FakeGh()
    _report(fake, 102)
    assert "ignored" in _report(fake, 103)
    assert len(fake.posted) == 1


def test_another_run_whose_substantive_jobs_are_done_counts_as_finished_while_its_report_job_runs():
    fake = FakeGh({101: run_spec(status="in_progress", rows=jobs(report="in_progress"))})
    assert "success" in _report(fake, 103)


def test_a_completed_run_counts_as_finished():
    fake = FakeGh({101: run_spec(status="completed", conclusion="success")})
    assert "success" in _report(fake)


@pytest.mark.parametrize("status", ["queued", "in_progress", "waiting", "pending"])
def test_a_substantive_job_that_has_not_completed_leaves_the_status_untouched(status):
    rows = jobs(extra=[job("uat-stage1 sqlite", status=status)])
    fake = FakeGh({102: run_spec(rows=rows)})
    assert "waiting" in _report(fake)
    assert fake.posted == []


def test_a_run_whose_release_outcome_job_has_not_completed_is_not_finished_even_if_the_listed_jobs_are():
    rows = [job("admit"), job("release outcome", status="queued")]
    fake = FakeGh({102: run_spec(rows=rows)})
    assert "waiting" in _report(fake)
    assert fake.posted == []


def test_a_run_whose_listed_jobs_lack_the_release_outcome_job_is_not_finished():
    fake = FakeGh({102: run_spec(rows=[job("admit"), job("probe report", status="in_progress")])})
    assert "waiting" in _report(fake)
    assert fake.posted == []


def test_a_run_with_no_jobs_yet_is_not_finished():
    fake = FakeGh({102: run_spec(rows=[])})
    assert "waiting" in _report(fake)
    assert fake.posted == []


@pytest.mark.parametrize("variant", ["reuse", "fallback"])
@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "timed_out"])
def test_a_failed_substantive_job_in_another_run_fails_the_status_naming_the_variant_and_job(variant, conclusion):
    rows = jobs(extra=[job("install-matrix (ubuntu)", conclusion=conclusion)])
    fake = FakeGh({IDS[variant]: run_spec(rows=rows)})
    _report(fake)
    (patch,) = fake.posted
    assert patch["state"] == "failure"
    description = patch["description"]
    assert f"{variant}: install-matrix (ubuntu): {conclusion}" in description
    assert len(description) <= 140
    assert patch["target_url"] == f"https://github.com/{REPOSITORY}/actions/runs/{IDS[variant]}"
    for other in set(IDS) - {variant}:
        assert other not in description


@pytest.mark.parametrize("result", ["failure", "cancelled"])
def test_a_failed_job_of_the_current_run_fails_the_status_through_its_needs(result):
    needs = {**NEEDS_OK, "release-uat": {"result": result, "outputs": {}}}
    fake = FakeGh()
    _report(fake, 103, needs)
    (patch,) = fake.posted
    assert patch["state"] == "failure"
    assert f"guarded-change: release-uat: {result}" in patch["description"]


def test_skipped_jobs_never_fail_the_status():
    needs = {**NEEDS_OK, "uat-stage1": {"result": "skipped", "outputs": {}}}
    fake = FakeGh({101: run_spec(rows=jobs(extra=[job("uat-stage1 x", conclusion="skipped")]))})
    _report(fake, 103, needs)
    assert fake.posted[0]["state"] == "success"


def test_the_report_job_of_another_run_is_never_judged():
    rows = jobs(report="completed")
    rows[-1]["conclusion"] = "failure"
    fake = FakeGh({101: run_spec(rows=rows)})
    _report(fake)
    assert fake.posted[0]["state"] == "success"


def test_a_run_that_completed_without_any_job_and_did_not_succeed_fails_the_status():
    fake = FakeGh({101: run_spec(status="completed", conclusion="cancelled", rows=[])})
    _report(fake)
    assert "reuse: run: cancelled" in fake.posted[0]["description"]


@pytest.mark.parametrize("conclusion", ["cancelled", "failure", "timed_out", "startup_failure"])
def test_a_completed_run_whose_conclusion_is_not_success_fails_the_status_even_when_every_listed_job_is_green(
    conclusion,
):
    fake = FakeGh({101: run_spec(status="completed", conclusion=conclusion, rows=jobs(report="completed"))})
    _report(fake)
    assert f"reuse: run: {conclusion}" in fake.posted[0]["description"]


def test_a_run_that_failed_only_because_its_own_report_job_failed_is_judged_on_its_substantive_jobs():
    rows = jobs(report="completed")
    rows[-1]["conclusion"] = "failure"
    fake = FakeGh({101: run_spec(status="completed", conclusion="failure", rows=rows)})
    _report(fake)
    assert fake.posted[0]["state"] == "success"


@pytest.mark.parametrize("state", ["success", "failure", "error"])
def test_a_status_that_is_already_final_is_left_alone(state):
    fake = FakeGh(statuses=[FakeGh.status(state=state, description="Probe passed"), FakeGh.status()])
    assert "ignored" in _report(fake)
    assert fake.posted == []


def test_a_run_that_belongs_to_no_pending_probe_status_is_ignored():
    fake = FakeGh(
        statuses=[
            FakeGh.status(description="Probe running: reuse=1;fallback=2;guarded-change=3"),
            FakeGh.status(description=None),
        ]
    )
    assert "ignored" in _report(fake)
    assert fake.posted == []


def test_a_run_with_no_probe_status_on_its_head_is_ignored():
    fake = FakeGh(statuses=[FakeGh.status(context="review-gate")])
    assert "ignored" in _report(fake)
    assert fake.posted == []


def test_the_latest_probe_status_wins_over_an_older_pending_one_that_records_this_run():
    newer = FakeGh.status(description="Probe running: reuse=1;fallback=2;guarded-change=3")
    fake = FakeGh(statuses=[newer, FakeGh.status()])
    assert "ignored" in _report(fake)
    assert fake.posted == []


def test_other_contexts_never_hide_the_probe_status():
    fake = FakeGh(statuses=[FakeGh.status(context="review-gate", state="success"), FakeGh.status()])
    assert "success" in _report(fake)
    assert [patch["context"] for patch in fake.posted] == ["release-v2 probe"]


def test_a_status_made_final_while_the_runs_were_judged_is_not_overwritten():
    fake = FakeGh()

    def racing(*args):
        result = fake(*args)
        if "/jobs?" in args[1]:
            fake.statuses.insert(0, FakeGh.status(state="failure", description="Probe failed: elsewhere"))
        return result

    assert "ignored" in verdict.report(REPOSITORY, 103, HEAD, NEEDS_OK, racing)
    assert fake.posted == []


@pytest.mark.parametrize(
    "override",
    [{"path": ".github/workflows/ci.yml"}, {"event": "push"}, {"head_sha": "b" * 40}],
)
def test_a_recorded_run_that_is_not_a_release_v2_dispatch_at_the_status_head_fails_the_status(override):
    fake = FakeGh()
    fake.overrides[102] = override
    _report(fake)
    (patch,) = fake.posted
    assert patch["state"] == "failure"
    assert "foreign runs" in patch["description"] and "fallback" in patch["description"]
    assert patch["target_url"] == f"https://github.com/{REPOSITORY}/actions/runs/102"


def test_a_recorded_run_path_carrying_its_ref_still_counts():
    fake = FakeGh()
    fake.overrides[102] = {"path": ".github/workflows/release-v2.yml@refs/heads/feat/x"}
    _report(fake)
    assert fake.posted[0]["state"] == "success"


def test_reconcile_head_sets_a_pending_status_whose_runs_all_finished():
    fake = FakeGh()
    assert "success" in verdict.reconcile_head(REPOSITORY, HEAD, fake)
    (patch,) = fake.posted
    assert (patch["context"], patch["state"]) == ("release-v2 probe", "success")


def test_reconcile_head_uses_the_same_job_based_finished_rule():
    fake = FakeGh({102: run_spec(rows=jobs(extra=[job("uat-stage2 x", status="in_progress")]))})
    assert "waiting" in verdict.reconcile_head(REPOSITORY, HEAD, fake)
    assert fake.posted == []


def test_reconcile_head_fails_the_status_naming_a_failed_variant():
    fake = FakeGh({102: run_spec(rows=jobs(outcome="failure"))})
    assert "failure" in verdict.reconcile_head(REPOSITORY, HEAD, fake)
    assert "fallback: release outcome: failure" in fake.posted[0]["description"]


def test_reconcile_head_reports_no_status_so_the_caller_dispatches():
    fake = FakeGh(statuses=[])
    assert verdict.reconcile_head(REPOSITORY, HEAD, fake) is None
    assert fake.posted == []


def test_reconcile_head_ignores_a_final_status_so_a_deliberate_rerun_dispatches_again():
    fake = FakeGh(statuses=[FakeGh.status(state="failure", description="Probe failed: x"), FakeGh.status()])
    assert verdict.reconcile_head(REPOSITORY, HEAD, fake) is None
    assert fake.posted == []


def test_reconcile_head_ignores_a_pending_status_without_recorded_runs():
    fake = FakeGh(statuses=[FakeGh.status(description="Probe running")])
    assert verdict.reconcile_head(REPOSITORY, HEAD, fake) is None


@pytest.mark.parametrize(
    "description",
    [
        None,
        "",
        "reuse=101;fallback=102;guarded-change=103",
        "Probe running: reuse=1",
        "Probe running: reuse=1;fallback=x;guarded-change=3",
        "Probe running: reuse=1;fallback=2;other=3",
    ],
)
def test_a_malformed_description_records_nothing(description):
    assert verdict.parse_recorded_runs(description) == {}


def test_one_run_id_recorded_for_every_variant_records_nothing():
    assert verdict.parse_recorded_runs("Probe running: reuse=7;fallback=7;guarded-change=7") == {}
    assert verdict.parse_recorded_runs("Probe running: reuse=7;fallback=8;guarded-change=7") == {}


def test_the_description_the_launcher_writes_round_trips():
    assert verdict.parse_recorded_runs(DESCRIPTION) == IDS
    assert verdict.parse_recorded_runs(launch.pending_description(IDS)) == IDS
    assert len(launch.pending_description({variant: 10**12 + n for n, variant in enumerate(IDS)})) <= 140


def test_the_status_pages_are_read_until_the_probe_context_or_a_short_page(monkeypatch):
    monkeypatch.setattr(verdict, "STATUSES_PAGE", 2)
    other = FakeGh.status(context="review-gate")
    pages = {1: [other, other], 2: [other, FakeGh.status()], 3: []}
    seen = []

    def runner(*args):
        page = int(args[1].split("&page=")[1])
        seen.append(page)
        return pages[page]

    assert verdict.latest_status(REPOSITORY, HEAD, runner)["context"] == "release-v2 probe"
    assert seen == [1, 2]
    seen.clear()
    pages[2] = [other]
    assert verdict.latest_status(REPOSITORY, HEAD, runner) is None
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


def test_main_completes_the_status_from_the_needs_in_the_environment(monkeypatch, capsys):
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
