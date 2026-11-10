from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.utilities.posix_shell import run_posix_shell, skip_without_posix_shell

pytestmark = [pytest.mark.unit, pytest.mark.fast]

WORKFLOW = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "omp-pin-bump.yml"


def load() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def job() -> dict[str, Any]:
    return load()["jobs"]["bump"]


def opener_job() -> dict[str, Any]:
    return load()["jobs"]["open-pr"]


def steps() -> dict[str, dict[str, Any]]:
    return {step["name"]: step for step in job()["steps"]}


def opener_steps() -> dict[str, dict[str, Any]]:
    return {step["name"]: step for step in opener_job()["steps"]}


def all_steps() -> list[dict[str, Any]]:
    return [*job()["steps"], *opener_job()["steps"]]


def run_text() -> str:
    return "\n".join(str(step.get("run", "")) for step in all_steps())


def test_the_bump_check_runs_daily_and_on_demand_and_only_in_the_main_repository() -> None:
    triggers = load()[True]
    assert set(triggers) == {"schedule", "workflow_dispatch"}
    assert re.fullmatch(r"\d+ \d+ \* \* \*", triggers["schedule"][0]["cron"])
    assert job()["if"] == "github.repository == 'BenchBox-dev/BenchBox'"


def test_the_opener_holds_only_actions_contents_and_pull_request_write_and_the_workflow_defaults_to_read() -> None:
    assert load()["permissions"] == {"contents": "read"}
    assert job()["permissions"] == {"contents": "read"}
    assert opener_job()["permissions"] == {"actions": "write", "contents": "write", "pull-requests": "write"}


def test_every_action_is_pinned_to_a_commit_and_develop_is_what_gets_checked_out() -> None:
    for step in all_steps():
        if "uses" in step:
            assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", step["uses"]), step["uses"]
    assert steps()["Checkout develop"]["with"]["ref"] == "develop"
    assert opener_steps()["Checkout develop"]["with"]["ref"] == "develop"


def test_the_preparation_runs_the_verified_theseus_install_from_the_checked_out_develop_commit() -> None:
    prepare = steps()["Check the latest stable omp build against the pin"]
    command = " ".join(str(prepare["run"]).split())
    assert 'THESEUS="$(python3 scripts/theseus_cli.py --trusted-ref "$(git rev-parse HEAD)" path)"' in command
    assert '"$THESEUS" omp-bump prepare' in command
    assert "--policy .github/review.yml" in command
    assert "--omp-config .github/review-omp-config.yml" in command
    assert "--fixtures tests/fixtures/review_candidates/omp_candidate" in command
    assert '--out-dir "$OUT_DIR"' in command
    assert "uv run" not in WORKFLOW.read_text(encoding="utf-8")
    assert "oracle_reviewers" not in command


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=root, encoding="utf-8", errors="strict", stderr=subprocess.PIPE
    ).strip()


@pytest.fixture
def lifecycle(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    skip_without_posix_shell()
    remote, checkout, artifacts, bin_dir = (tmp_path / name for name in ("remote.git", "checkout", "artifacts", "bin"))
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "clone", str(remote), str(checkout)], check=True, capture_output=True)
    git(checkout, "config", "user.name", "Fixture Owner")
    git(checkout, "config", "user.email", "fixture@example.invalid")
    git(checkout, "config", "core.autocrlf", "false")
    git(checkout, "checkout", "-b", "develop")
    policy = checkout / ".github" / "review.yml"
    policy.parent.mkdir()
    policy.write_text("omp:\n  version: 18.13.3\nclassifier:\n  mode: previous\n", encoding="utf-8", newline="\n")
    git(checkout, "add", ".github/review.yml")
    git(checkout, "commit", "-m", "Initial policy")
    git(checkout, "push", "origin", "develop")
    git(checkout, "checkout", "-b", "chore/omp-pin-18.16.0")
    policy.write_text("omp:\n  version: 18.16.0\nclassifier:\n  mode: previous\n", encoding="utf-8", newline="\n")
    git(checkout, "commit", "-am", "Candidate")
    git(checkout, "push", "origin", "HEAD")
    git(checkout, "checkout", "develop")
    git(checkout, "branch", "-D", "chore/omp-pin-18.16.0")
    policy.write_text(
        "omp:\n  version: 18.13.3\nclassifier:\n  mode: current\npromotion:\n  enabled: true\n",
        encoding="utf-8",
        newline="\n",
    )
    git(checkout, "commit", "-am", "Update classifier and promotion")
    git(checkout, "push", "origin", "develop")
    artifacts.mkdir()
    (artifacts / "policy.yml").write_text(
        policy.read_text(encoding="utf-8").replace("18.13.3", "18.16.0"), encoding="utf-8", newline="\n"
    )
    (artifacts / "pr-body.md").write_text("Validated candidate", encoding="utf-8", newline="\n")
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, subprocess, sys\n"
        "from pathlib import Path\n"
        "from urllib.parse import parse_qs, urlsplit\n"
        "sys.stdout.reconfigure(encoding='utf-8', newline='\\n')\n"
        "path = Path(os.environ['GH_FIXTURE'])\n"
        "state = json.loads(path.read_text())\n"
        "args = sys.argv[1:]\n"
        "if args[0] == 'api':\n"
        "    endpoint = next(arg for arg in args if arg.startswith('repos/'))\n"
        "    query = parse_qs(urlsplit(endpoint).query)\n"
        "    if endpoint.endswith('/statuses?per_page=100'):\n"
        "        print('42' if state.get('verdict') else '')\n"
        "    elif '/runs?' in endpoint:\n"
        "        print('')\n"
        "    elif endpoint.endswith('/pulls') and 'POST' in args:\n"
        "        state['pulls'].append(state['candidate'])\n"
        "        state['created'] = state.get('created', 0) + 1\n"
        "        print(state['candidate']['number'])\n"
        "    elif '/pulls?' in endpoint:\n"
        "        pulls = state['pulls']\n"
        "        if 'head' in query:\n"
        "            branch = query['head'][0].split(':', 1)[1]\n"
        "            pulls = [p for p in pulls if p['head']['ref'] == branch]\n"
        "        else:\n"
        "            pulls = [p for p in pulls if p['state'] == 'open']\n"
        "        print(json.dumps([pulls[:2], pulls[2:]]))\n"
        "    else:\n"
        "        branch = state['candidate']['head']['ref']\n"
        "        print(state.get('reported_head') or subprocess.check_output(['git', 'ls-remote', 'origin', branch], text=True).split()[0])\n"
        "elif args[:2] == ['pr', 'close']:\n"
        "    number = int(args[2])\n"
        "    state.setdefault('closed', []).append(number)\n"
        "    next(p for p in state['pulls'] if p['number'] == number)['state'] = 'closed'\n"
        "elif args[:2] == ['workflow', 'run']:\n"
        "    state.setdefault('reviews', []).append(next(a for a in args if a.startswith('head=')))\n"
        "else:\n"
        "    raise SystemExit('Unexpected gh call: ' + repr(args))\n"
        "path.write_text(json.dumps(state))\n",
        encoding="utf-8",
        newline="\n",
    )
    gh.chmod(0o755)
    candidate = pull(3931, "18.16.0")
    candidate["head"]["sha"] = git(checkout, "rev-parse", "origin/chore/omp-pin-18.16.0")
    state_path = tmp_path / "github.json"
    state_path.write_text(json.dumps({"pulls": [candidate], "candidate": candidate}), encoding="utf-8", newline="\n")
    environment = {
        **os.environ,
        "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
        "GH_FIXTURE": str(state_path),
        "GITHUB_REPOSITORY": "BenchBox-dev/BenchBox",
        "GH_TOKEN": "fixture-token",
        "OUT_DIR": str(artifacts),
        "VERSION": "18.16.0",
        "PREPARED_BASE": git(checkout, "rev-parse", "HEAD"),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    return checkout, state_path, environment


def pull(number: int, version: str, *, held: bool = False, author: str = "github-actions[bot]") -> dict[str, Any]:
    return {
        "number": number,
        "state": "open",
        "user": {"login": author},
        "labels": [{"name": "no-auto-merge"}] if held else [],
        "head": {"ref": f"chore/omp-pin-{version}", "repo": {"full_name": "BenchBox-dev/BenchBox"}},
    }


def run_opener(lifecycle: tuple[Path, Path, dict[str, str]]) -> subprocess.CompletedProcess[str]:
    checkout, _, environment = lifecycle
    return run_posix_shell(
        opener_steps()["Open the bump pull request"]["run"],
        cwd=checkout,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_existing_candidate_imports_current_policy_without_changing_its_pin_and_gets_a_new_review(lifecycle) -> None:
    checkout, state_path, environment = lifecycle
    previous = git(checkout, "rev-parse", "origin/chore/omp-pin-18.16.0")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["reported_head"] = previous
    state_path.write_text(json.dumps(state), encoding="utf-8", newline="\n")
    result = run_opener(lifecycle)
    assert result.returncode == 0, result.stderr
    head = git(checkout, "rev-parse", "HEAD")
    policy = ".github/review.yml"
    expected = Path(environment["OUT_DIR"], "policy.yml").read_text(encoding="utf-8")
    assert git(checkout, "show", f"HEAD:{policy}") == expected.strip()
    assert git(checkout, "diff", "--name-only", environment["PREPARED_BASE"], "HEAD") == policy
    assert git(checkout, "merge-base", previous, head) == previous
    assert git(checkout, "ls-remote", "origin", "chore/omp-pin-18.16.0").split()[0] == head
    assert git(checkout, "ls-remote", "origin", "develop").split()[0] == environment["PREPARED_BASE"]
    assert json.loads(state_path.read_text(encoding="utf-8"))["reviews"] == [f"head={head}"]
    git(checkout, "checkout", "develop")
    git(checkout, "branch", "-D", "chore/omp-pin-18.16.0")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["pulls"][0]["head"]["sha"] = head
    state["verdict"] = True
    state_path.write_text(json.dumps(state), encoding="utf-8", newline="\n")
    result = run_opener(lifecycle)
    assert result.returncode == 0, result.stderr
    assert git(checkout, "rev-parse", "HEAD") == head
    assert json.loads(state_path.read_text(encoding="utf-8"))["reviews"] == [f"head={head}"]


def test_new_candidate_closes_only_older_unheld_automation_prs_across_pages(lifecycle) -> None:
    checkout, state_path, environment = lifecycle
    git(checkout, "push", "origin", "--delete", "chore/omp-pin-18.16.0")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    fork = pull(8, "18.11.0")
    fork["head"]["repo"]["full_name"] = "someone/BenchBox"
    state["pulls"] = [
        pull(1, "18.9.0"),
        pull(2, "18.14.1"),
        pull(3, "18.15.0", held=True),
        pull(4, "18.17.0"),
        pull(5, "18.12.0", author="person"),
        fork,
    ]
    state_path.write_text(json.dumps(state), encoding="utf-8", newline="\n")
    result = run_opener(lifecycle)
    assert result.returncode == 0, result.stderr
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["created"] == 1
    assert state["closed"] == [1, 2]
    assert git(checkout, "ls-remote", "origin", "develop").split()[0] == environment["PREPARED_BASE"]
    assert git(checkout, "diff", "--name-only", environment["PREPARED_BASE"], "HEAD") == ".github/review.yml"
    assert state["reviews"] == [f"head={git(checkout, 'rev-parse', 'HEAD')}"]


@pytest.mark.parametrize(
    "failure", ["held", "closed", "edited", "extra-file", "advanced-base", "conflict", "changed-head"]
)
def test_refused_candidates_do_not_push_close_other_prs_or_start_reviews(lifecycle, failure: str) -> None:
    checkout, state_path, environment = lifecycle
    state = json.loads(state_path.read_text(encoding="utf-8"))
    candidate = state["pulls"][0]
    if failure == "held":
        candidate["labels"] = [{"name": "no-auto-merge"}]
    elif failure == "closed":
        candidate["state"] = "closed"
    elif failure == "changed-head":
        candidate["head"]["sha"] = "0" * 40
    elif failure in {"edited", "conflict", "extra-file"}:
        git(checkout, "checkout", "chore/omp-pin-18.16.0")
        if failure == "extra-file":
            (checkout / "unexpected.txt").write_text("Unrelated candidate edit\n", encoding="utf-8")
            git(checkout, "add", "unexpected.txt")
        else:
            policy = checkout / ".github" / "review.yml"
            policy.write_text(
                policy.read_text(encoding="utf-8").replace("18.16.0", "18.16.1")
                if failure == "edited"
                else "invalid policy\n",
                encoding="utf-8",
                newline="\n",
            )
        git(checkout, "commit", "-am", "Manual candidate changes")
        git(checkout, "push", "origin", "HEAD")
        candidate["head"]["sha"] = git(checkout, "rev-parse", "HEAD")
        git(checkout, "checkout", "develop")
        git(checkout, "branch", "-D", "chore/omp-pin-18.16.0")
    else:
        environment["PREPARED_BASE"] = git(checkout, "rev-parse", "HEAD~1")
    state_path.write_text(json.dumps(state), encoding="utf-8", newline="\n")
    previous = git(checkout, "ls-remote", "origin", "chore/omp-pin-18.16.0").split()[0]
    result = run_opener(lifecycle)
    assert result.returncode == (0 if failure == "closed" else 1), result.stderr
    assert git(checkout, "ls-remote", "origin", "chore/omp-pin-18.16.0").split()[0] == previous
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert not state.get("closed") and not state.get("reviews") and not state.get("created")


def test_the_only_secret_the_job_reads_is_the_job_token() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert set(re.findall(r"secrets\.(\w+)", text)) == {"GITHUB_TOKEN"}


def test_a_red_candidate_fails_the_run_and_still_reports_its_suite_output() -> None:
    report = steps()["Report the candidate"]
    assert report["if"] == "always() && steps.prepare.outputs.state != ''"
    assert "detail.txt" in report["run"] and "GITHUB_STEP_SUMMARY" in report["run"]
    assert "continue-on-error" not in steps()["Check the latest stable omp build against the pin"]


def test_no_write_token_is_present_while_the_candidate_build_runs() -> None:
    for step in job()["steps"]:
        assert "secrets.GITHUB_TOKEN" not in str(step) and "github.token" not in str(step), step["name"]
    for step in all_steps():
        if step.get("uses", "").startswith("actions/checkout@"):
            assert step["with"]["persist-credentials"] is False
    opener = opener_steps()["Open the bump pull request"]
    assert opener["env"]["GH_TOKEN"] == "${{ secrets.GITHUB_TOKEN }}"
    assert "omp-bump" not in run_text_of(opener_job())


def run_text_of(target: dict[str, Any]) -> str:
    return "\n".join(str(step.get("run", "")) for step in target["steps"])
