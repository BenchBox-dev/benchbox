from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ROOT = Path(__file__).resolve().parents[3]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"scripts/{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    import sys

    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


trunk_revert = _load("trunk_revert")
trunk_red_disarm = _load("trunk_red_disarm")

REPLAY = json.loads((ROOT / "tests/fixtures/trunk_red_20261024.json").read_text(encoding="utf-8"))
FIRST_RED_SHA = "24bfcf8a0fb048c9ad554ef2e9e99066f7027167"
LATER_RED_SHA = "c962e3e8733e9107988ba05145d09a27717a8fda"
FIRST_RED_RUN = 40071232825
FIX_HEAD = REPLAY["pr"]["headRefOid"]
FIRST_RED_COMPLETED = datetime(2026, 10, 24, 7, 2, 1, tzinfo=UTC)
CULPRIT_URL = f"https://github.com/BenchBox-dev/BenchBox/actions/runs/{FIRST_RED_RUN}"


def _replayed_runs() -> list[dict]:
    runs = []
    for item in REPLAY["runs"]:
        created = datetime.fromisoformat(item["createdAt"].replace("Z", "+00:00"))
        runs.append(
            {
                **item,
                "status": "completed",
                "updatedAt": (created + timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "url": f"https://github.com/BenchBox-dev/BenchBox/actions/runs/{item['databaseId']}",
            }
        )
    return runs


def _view(branch: str, number: int, head: str, messages: list[tuple[str, str]] | None = None) -> dict:
    commits = [{"messageHeadline": headline, "messageBody": body} for headline, body in (messages or [("change", "")])]
    return {"headRefName": branch, "commits": commits, "number": number, "headRefOid": head}


def _armed(number: int, branch: str, node: str | None = None, armed: bool = True) -> dict:
    return {
        "number": number,
        "headRefName": branch,
        "autoMergeRequest": {"enabledAt": "2026-10-24T07:00:00Z"} if armed else None,
        "id": node or f"PR_node_{number}",
    }


class DisarmRun:
    def __init__(
        self,
        runs: list[dict],
        *,
        armed: list[dict] | None = None,
        list_out: str | None = None,
        views: dict[str, dict] | None = None,
        pulls_by_sha: dict[str, list[dict]] | None = None,
        develop: list[dict] | None = None,
        comments: dict[int, list[dict]] | None = None,
        files: list[str] | None = None,
        compare: list[str] | None = None,
        runs_code: int = 0,
        list_code: int = 0,
        develop_code: int = 0,
        view_fail: tuple[str, ...] = (),
        mutation_code: int = 0,
    ) -> None:
        self.runs = runs
        self.armed = armed if armed is not None else []
        self.list_out = list_out
        self.views = views if views is not None else {}
        self.pulls_by_sha = pulls_by_sha if pulls_by_sha is not None else {}
        self.develop = develop if develop is not None else [{"commit": {"message": "feat: unrelated"}}]
        self.comments = comments if comments is not None else {}
        self.files = files
        self.compare = compare
        self.runs_code = runs_code
        self.list_code = list_code
        self.develop_code = develop_code
        self.view_fail = set(view_fail)
        self.mutation_code = mutation_code
        self.disarmed: set[int] = set()
        self.mutations: list[str] = []
        self.calls: list[list[str]] = []

    def _pr_view(self, cmd: list[str]) -> tuple[int, str]:
        ref = cmd[3]
        if ref in self.view_fail:
            return 1, "boom"
        fields = cmd[cmd.index("--json") + 1].split(",")
        if fields == ["files"]:
            if self.files is None:
                return 1, "boom"
            return 0, json.dumps({"files": [{"path": path} for path in self.files]})
        if "autoMergeRequest" in fields:
            number = int(ref)
            state_armed = number not in self.disarmed and next(
                (item["autoMergeRequest"] for item in self.armed if item["number"] == number), None
            )
            return 0, json.dumps({"number": number, "state": "OPEN", "autoMergeRequest": state_armed})
        return 0, json.dumps(self.views[ref])

    def _graphql(self, cmd: list[str]) -> tuple[int, str]:
        node = next(part.split("=", 1)[1] for part in cmd if part.startswith("pullRequestId="))
        self.mutations.append(node)
        if self.mutation_code != 0:
            return self.mutation_code, "boom"
        for item in self.armed:
            if item["id"] == node:
                self.disarmed.add(item["number"])
        return 0, json.dumps({"data": {"disablePullRequestAutoMerge": {"pullRequest": {"number": 1}}}})

    def _rest_api(self, cmd: list[str]) -> tuple[int, str]:
        if "/pulls" in cmd[2]:
            sha = cmd[2].split("/commits/")[1].split("/")[0]
            return 0, json.dumps(self.pulls_by_sha.get(sha, []))
        if cmd[2] == "--paginate":
            number = int(cmd[3].split("/issues/")[1].split("/")[0])
            return 0, "\n".join(json.dumps(comment) for comment in self.comments.get(number, []))
        if "/compare/" in cmd[2]:
            head = cmd[2].split("...")[1].split("?")[0]
            shas = self.compare if self.compare is not None else [head]
            return 0, json.dumps({"total_commits": len(shas), "commits": [{"sha": sha} for sha in shas]})
        if self.develop_code != 0:
            return self.develop_code, "boom"
        return 0, json.dumps(self.develop)

    def __call__(self, cmd: list[str]) -> tuple[int, str]:
        self.calls.append(cmd)
        if cmd[:3] == ["gh", "run", "list"]:
            if self.runs_code != 0:
                return self.runs_code, "boom"
            ordered = sorted(self.runs, key=lambda item: (item["createdAt"], item["databaseId"]), reverse=True)
            return 0, json.dumps(ordered[: int(cmd[cmd.index("--limit") + 1])])
        if cmd[:3] == ["gh", "pr", "list"]:
            if self.list_code != 0:
                return self.list_code, "boom"
            return 0, self.list_out if self.list_out is not None else json.dumps(self.armed)
        if cmd[:3] == ["gh", "pr", "view"]:
            return self._pr_view(cmd)
        if cmd[:3] == ["gh", "api", "graphql"]:
            return self._graphql(cmd)
        if cmd[:2] == ["gh", "api"]:
            return self._rest_api(cmd)
        if cmd[:2] == ["git", "merge-base"]:
            return 0, ""
        if cmd[:2] == ["git", "fetch"]:
            return 0, ""
        if cmd[:2] == ["git", "log"]:
            assert self.files is not None
            return 0, "\n\n".join(f"@@{item['commit']}\n\n" + "\n".join(item["files"]) for item in REPLAY["streak"])
        raise AssertionError(f"unexpected command: {cmd}")


def _red_run() -> DisarmRun:
    views = {"3385": _view(REPLAY["pr"]["headRefName"], 3385, FIX_HEAD)}
    pulls = {
        FIRST_RED_SHA: [{"number": 3378, "merged_at": "2026-10-24T06:30:00Z", "base": {"ref": "develop"}}],
        LATER_RED_SHA: [{"number": 3383, "merged_at": "2026-10-24T06:40:00Z", "base": {"ref": "develop"}}],
    }
    return DisarmRun(
        _replayed_runs(), armed=[_armed(3385, REPLAY["pr"]["headRefName"])], views=views, pulls_by_sha=pulls
    )


def _selected(run: DisarmRun) -> list[dict]:
    selected, failed = trunk_red_disarm.select_for_disarm(run, trunk_revert.REPOSITORY)
    assert failed is not None
    assert failed["databaseId"] == FIRST_RED_RUN
    return selected


def test_red_streak_selects_armed_ordinary_pr() -> None:
    selected = _selected(_red_run())
    assert [item["number"] for item in selected] == [3385]


def test_pr_arm_gate_admits_is_not_selected() -> None:
    run = DisarmRun(
        _replayed_runs(),
        armed=[_armed(3385, REPLAY["pr"]["headRefName"])],
        views={"3385": _view(REPLAY["pr"]["headRefName"], 3385, FIX_HEAD)},
        pulls_by_sha={
            FIRST_RED_SHA: [{"number": 3378, "merged_at": "2026-10-24T06:30:00Z", "base": {"ref": "develop"}}],
            LATER_RED_SHA: [{"number": 3383, "merged_at": "2026-10-24T06:40:00Z", "base": {"ref": "develop"}}],
        },
        comments={3385: [_authorization()]},
        files=list(REPLAY["pr"]["files"]),
    )
    selected, failed = trunk_red_disarm.select_for_disarm(run, trunk_revert.REPOSITORY)
    assert selected == []
    assert failed is not None


def test_unarmed_pr_is_not_selected() -> None:
    run = _red_run()
    run.armed.append(_armed(3390, "fix/other", armed=False))
    run.views["3390"] = _view("fix/other", 3390, "d" * 40)
    assert [item["number"] for item in _selected(run)] == [3385]


def test_genuine_revert_is_never_selected() -> None:
    run = _red_run()
    run.armed.append(_armed(3391, "fix/revert-3378"))
    run.views["3391"] = _view(
        "fix/revert-3378",
        3391,
        "e" * 40,
        [('Revert "export" (#3378)', f"This reverts commit {FIRST_RED_SHA}.")],
    )
    assert [item["number"] for item in _selected(run)] == [3385]


def _authorization(head: str = FIX_HEAD) -> dict:
    created = (FIRST_RED_COMPLETED + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "login": "joeharris76",
        "user_type": "User",
        "body": f"Fix-forward authorized: {head} for trunk run {FIRST_RED_RUN}",
        "created_at": created,
        "updated_at": created,
    }


def test_green_selects_nothing() -> None:
    green = dict(_replayed_runs()[0])
    green["conclusion"] = "success"
    run = DisarmRun([green], armed=[_armed(3385, "fix/thing")], views={"3385": _view("fix/thing", 3385, "a" * 40)})
    selected, failed = trunk_red_disarm.select_for_disarm(run, trunk_revert.REPOSITORY)
    assert (selected, failed) == ([], None)
    assert trunk_red_disarm.disarm_all(run, trunk_revert.REPOSITORY) == 0
    assert run.mutations == []


def test_no_trunk_runs_selects_nothing() -> None:
    run = DisarmRun([], armed=[_armed(3385, "fix/thing")])
    selected, failed = trunk_red_disarm.select_for_disarm(run, trunk_revert.REPOSITORY)
    assert (selected, failed) == ([], None)


def _runs_with_an_older_unfinished_run() -> list[dict]:
    def entry(index: int, status: str, conclusion: str | None, hour: int) -> dict:
        stamp = f"2026-10-24T{hour:02d}:00:00Z"
        return {
            "status": status,
            "conclusion": conclusion,
            "createdAt": stamp,
            "updatedAt": stamp,
            "databaseId": 500 + index,
            "url": f"https://github.com/BenchBox-dev/BenchBox/actions/runs/{500 + index}",
            "headSha": f"{index:040x}",
        }

    return [
        entry(3, "completed", "failure", 9),
        entry(2, "in_progress", None, 8),
        entry(1, "completed", "success", 7),
    ]


def test_undetermined_culprit_disarms_every_armed_ordinary_pr_without_naming_a_commit(
    capsys: pytest.CaptureFixture[str],
) -> None:
    run = DisarmRun(
        _runs_with_an_older_unfinished_run(),
        armed=[_armed(3385, "fix/thing"), _armed(3386, "fix/revert-3200")],
    )
    with pytest.raises(trunk_revert.UndeterminedCulpritError):
        trunk_red_disarm.select_for_disarm(run, trunk_revert.REPOSITORY)
    assert trunk_red_disarm.disarm_all(run, trunk_revert.REPOSITORY) == 0
    assert run.mutations == ["PR_node_3385"]
    out = capsys.readouterr().out
    assert "undetermined" in out
    assert "PR #3385" in out
    assert "3386" not in out
    assert "PR #3200" not in out


def test_streak_lookup_failure_disarms_nothing_and_fails(capsys: pytest.CaptureFixture[str]) -> None:
    run = DisarmRun(_replayed_runs(), armed=[_armed(3385, "fix/thing")], runs_code=1)
    with pytest.raises(trunk_revert.TrunkError):
        trunk_red_disarm.select_for_disarm(run, trunk_revert.REPOSITORY)
    assert trunk_red_disarm.disarm_all(run, trunk_revert.REPOSITORY) == 1
    assert run.mutations == []
    assert "disarming nothing" in capsys.readouterr().err


def test_develop_lookup_failure_disarms_nothing_and_fails() -> None:
    run = _red_run()
    run.develop_code = 1
    with pytest.raises(trunk_revert.TrunkError):
        trunk_red_disarm.select_for_disarm(run, trunk_revert.REPOSITORY)
    assert trunk_red_disarm.disarm_all(run, trunk_revert.REPOSITORY) == 1
    assert run.mutations == []


def test_malformed_listing_disarms_nothing_and_fails() -> None:
    run = DisarmRun(_replayed_runs(), list_out="{}")
    with pytest.raises(trunk_revert.TrunkError):
        trunk_red_disarm.select_for_disarm(run, trunk_revert.REPOSITORY)
    assert trunk_red_disarm.disarm_all(run, trunk_revert.REPOSITORY) == 1
    assert run.mutations == []


def test_per_pr_evaluation_failure_selects_that_pr() -> None:
    run = _red_run()
    run.views = {}
    run.view_fail = ("3385",)
    selected = _selected(run)
    assert [item["number"] for item in selected] == [3385]
    assert "fail-closed" in selected[0]["reason"]


def test_per_pr_evaluation_failure_never_selects_a_revert_branch() -> None:
    run = DisarmRun(
        _replayed_runs(),
        armed=[_armed(3391, "fix/revert-3378")],
        views={},
        view_fail=("3391",),
        pulls_by_sha={FIRST_RED_SHA: [{"number": 3378, "merged_at": "x", "base": {"ref": "develop"}}]},
    )
    selected, failed = trunk_red_disarm.select_for_disarm(run, trunk_revert.REPOSITORY)
    assert selected == []
    assert failed is not None


def test_replay_disarms_ordinary_armed_and_names_culprit_3378() -> None:
    run = _red_run()
    run.armed.append(_armed(3391, "fix/revert-3378"))
    run.views["3391"] = _view(
        "fix/revert-3378",
        3391,
        "e" * 40,
        [('Revert "export" (#3378)', f"This reverts commit {FIRST_RED_SHA}.")],
    )
    run.armed.append(_armed(3392, "fix/other", armed=False))
    selected, failed = trunk_red_disarm.select_for_disarm(run, trunk_revert.REPOSITORY)
    assert failed is not None
    assert [item["number"] for item in selected] == [3385]
    named, hint = trunk_revert.describe_culprit(run, trunk_revert.REPOSITORY, failed)
    assert named == "PR #3378"
    assert hint == "make trunk-revert PR=3378"


def test_reverted_culprit_selects_nothing() -> None:
    run = _red_run()
    run.develop = [{"commit": {"message": f"Revert x\n\nThis reverts commit {FIRST_RED_SHA}."}}]
    selected, failed = trunk_red_disarm.select_for_disarm(run, trunk_revert.REPOSITORY)
    assert selected == []
    assert failed is not None


MID_SHA = "d" * 40


def _batch_red_run() -> DisarmRun:
    run = _red_run()
    run.compare = [MID_SHA, FIRST_RED_SHA]
    return run


def test_a_revert_of_one_covered_commit_does_not_clear_the_disarm() -> None:
    run = _batch_red_run()
    run.develop = [{"commit": {"message": f"Revert x\n\nThis reverts commit {FIRST_RED_SHA}."}}]
    selected, failed = trunk_red_disarm.select_for_disarm(run, trunk_revert.REPOSITORY)
    assert [item["number"] for item in selected] == [3385]
    assert failed is not None


def test_a_revert_branch_for_any_covered_commit_is_never_selected() -> None:
    run = _batch_red_run()
    run.armed.append(_armed(3391, "fix/revert-3370"))
    run.views["3391"] = _view(
        "fix/revert-3370", 3391, "e" * 40, [('Revert "x" (#3370)', f"This reverts commit {MID_SHA}.")]
    )
    assert [item["number"] for item in _selected(run)] == [3385]


def test_an_unreadable_covered_range_fails_the_job_loudly_and_disarms_nothing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    run = _batch_red_run()
    run.compare = []
    assert trunk_red_disarm.disarm_all(run, trunk_revert.REPOSITORY) == 1
    assert run.mutations == []
    assert "could not establish the red streak" in capsys.readouterr().err


def test_disarm_disables_each_selected_and_logs_culprit_and_run_url(capsys: pytest.CaptureFixture[str]) -> None:
    run = _red_run()
    assert trunk_red_disarm.disarm_all(run, trunk_revert.REPOSITORY) == 0
    assert run.mutations == ["PR_node_3385"]
    out = capsys.readouterr().out
    assert "disarmed PR #3385" in out
    assert "PR #3378" in out
    assert CULPRIT_URL in out


def test_disarm_failure_fails_the_run() -> None:
    run = _red_run()
    run.mutation_code = 1
    assert trunk_red_disarm.disarm_all(run, trunk_revert.REPOSITORY) == 1


def test_merged_race_is_tolerated() -> None:
    inner = _red_run()
    inner.mutation_code = 1

    class Raced:
        def __call__(self, cmd: list[str]) -> tuple[int, str]:
            if cmd[:3] == ["gh", "pr", "view"] and cmd[3] == "3385" and "--json" in cmd:
                fields = cmd[cmd.index("--json") + 1].split(",")
                if fields != ["headRefName", "commits", "number", "headRefOid"]:
                    return 0, json.dumps({"number": 3385, "state": "MERGED", "autoMergeRequest": None})
            return inner(cmd)

    assert trunk_red_disarm.disarm_all(Raced(), trunk_revert.REPOSITORY) == 0


def test_still_armed_after_mutation_fails_the_run() -> None:
    inner = _red_run()

    class Stuck:
        def __call__(self, cmd: list[str]) -> tuple[int, str]:
            if cmd[:3] == ["gh", "api", "graphql"]:
                return 0, json.dumps({"data": {"disablePullRequestAutoMerge": {"pullRequest": {"number": 3385}}}})
            return inner(cmd)

    assert trunk_red_disarm.disarm_all(Stuck(), trunk_revert.REPOSITORY) == 1


def test_main_rejects_a_malformed_repo(capsys: pytest.CaptureFixture[str]) -> None:
    assert trunk_red_disarm.main(["--repo", "not a repo"], run=_red_run()) == 2
    assert "owner/name" in capsys.readouterr().err


def _twin(label: str, pr_number: int, branch: str, head: str, messages: list[tuple[str, str]], **kwargs) -> None:
    def build() -> DisarmRun:
        return DisarmRun(
            _replayed_runs(),
            armed=[_armed(pr_number, branch)],
            views={str(pr_number): _view(branch, pr_number, head, messages)},
            pulls_by_sha={
                FIRST_RED_SHA: [{"number": 3378, "merged_at": "2026-10-24T06:30:00Z", "base": {"ref": "develop"}}],
            },
            **kwargs,
        )

    first, second = build(), build()
    expected = trunk_revert.arm_gate(str(pr_number), run=first)
    pr = trunk_revert.pr_head(str(pr_number), second, trunk_revert.REPOSITORY)
    try:
        failed = trunk_revert.red_streak_start(second, trunk_revert.REPOSITORY)
    except trunk_revert.NoTrunkRunsError:
        failed = None
    assert trunk_revert.arm_refusal(str(pr_number), pr, failed, second, trunk_revert.REPOSITORY) == expected, label


@pytest.mark.parametrize(
    ("label", "number", "branch", "messages"),
    [
        ("ordinary", 3385, "fix/thing", [("change", "")]),
        ("genuine-revert", 3391, "fix/revert-3378", [("Revert x", f"This reverts commit {FIRST_RED_SHA}.")]),
        (
            "wrong-revert",
            3392,
            "fix/revert-9",
            [("Revert y", "This reverts commit deadbeefdeadbeefdeadbeefdeadbeefdeadbeef.")],
        ),
    ],
)
def test_refusal_matches_arm_gate(label: str, number: int, branch: str, messages: list[tuple[str, str]]) -> None:
    _twin(label, number, branch, "a" * 40, messages)
