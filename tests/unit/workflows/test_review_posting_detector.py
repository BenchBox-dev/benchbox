from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
POSTER = "benchbox-review[bot]"
HEAD = "a" * 40
OLD_HEAD = "b" * 40
REPO = "owner/repo"

requires_tools = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("jq") is None or shutil.which("bash") is None,
    reason="needs POSIX bash and jq",
)

FAKE_GH = """#!/usr/bin/env bash
set -euo pipefail
case "$*" in
  "api repos/$FAKE_REPO/pulls/"*) cat "$FAKE_DIR/pull.json" ;;
  "api --paginate repos/$FAKE_REPO/commits/"*"/statuses?per_page=100")
    sha="${3#repos/$FAKE_REPO/commits/}"
    sha="${sha%%/*}"
    echo "$sha" >> "$FAKE_DIR/status-reads"
    cat "$FAKE_DIR/statuses-$sha.json" 2>/dev/null || echo '[]'
    ;;
  *) echo "unexpected gh call: $*" >&2; exit 9 ;;
esac
"""


@dataclass(frozen=True)
class Detector:
    workflow: str
    job: str
    callee: str
    contexts: tuple[str, ...]
    handled_inputs: tuple[str, ...]


VERDICT = Detector("review-verdict.yml", "verdict", "verdict", ("review-verdict",), ("head",))
GATE = Detector("review-gate.yml", "gate", "gate", ("review-gate",), ())
DETECTORS = pytest.mark.parametrize("detector", [VERDICT, GATE], ids=["verdict", "gate"])


def _job(detector: Detector) -> dict[str, Any]:
    return yaml.safe_load((WORKFLOWS / detector.workflow).read_text(encoding="utf-8"))["jobs"]["posting-check"]


def _script(detector: Detector) -> str:
    (step,) = _job(detector)["steps"]
    return step["run"]


def _status(context: str, creator: str = POSTER, state: str = "success") -> dict[str, Any]:
    return {"context": context, "state": state, "creator": {"login": creator}}


def _run(
    detector: Detector,
    tmp_path: Path,
    *,
    event: str = "pull_request_target",
    result: str = "success",
    statuses: dict[str, list[dict[str, Any]]] | None = None,
    pull: dict[str, Any] | None = None,
    action: str = "synchronize",
    base_changed: bool = False,
    handled: str = HEAD,
    pr: str = "7",
) -> subprocess.CompletedProcess[str]:
    fake_dir = tmp_path / "fake"
    fake_dir.mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH, encoding="utf-8")
    gh.chmod(0o755)
    (fake_dir / "pull.json").write_text(
        json.dumps({"state": "open", "draft": False, "base": {"ref": "develop"}, "head": {"sha": HEAD}} | (pull or {})),
        encoding="utf-8",
    )
    for sha, entries in (statuses or {}).items():
        (fake_dir / f"statuses-{sha}.json").write_text(json.dumps(entries), encoding="utf-8")
    env = _job(detector)["env"]
    process_env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "FAKE_DIR": str(fake_dir),
        "FAKE_REPO": REPO,
        "REPO": REPO,
        "GH_TOKEN": "token",
        "EVENT_NAME": event,
        "EVENT_ACTION": action,
        "BASE_CHANGED": "true" if base_changed else "false",
        "EVENT_PR": pr if event == "pull_request_target" else "",
        "EVENT_HEAD": handled if event == "pull_request_target" else "",
        "INPUT_PR": pr if event == "workflow_dispatch" else "",
        "INPUT_HEAD": handled if event == "workflow_dispatch" and detector.handled_inputs else "",
        "JOB_RESULT": result,
        "GATED_BASE": env["GATED_BASE"],
        "POSTER": env["POSTER"],
        "CONTEXTS": env["CONTEXTS"],
    }
    return subprocess.run(
        ["bash", "-s"],
        input=_script(detector),
        capture_output=True,
        encoding="utf-8",
        env=process_env,
        check=False,
        cwd=tmp_path,
    )


@DETECTORS
def test_the_detector_is_a_final_job_with_explicit_result_handling(detector: Detector) -> None:
    job = _job(detector)
    assert job["needs"] == [detector.callee]
    assert job["if"] == "${{ always() }}"
    assert job["env"]["JOB_RESULT"] == f"${{{{ needs.{detector.callee}.result }}}}"
    assert job["permissions"] == {"contents": "read", "pull-requests": "read", "statuses": "read"}
    assert "success()" not in (WORKFLOWS / detector.workflow).read_text(encoding="utf-8")
    assert set(job["env"]["CONTEXTS"].split()) == set(detector.contexts)
    assert job["env"]["POSTER"] == POSTER
    assert job["env"]["GATED_BASE"] == "develop,release/*"


@DETECTORS
def test_the_detector_reads_the_head_and_dispatch_inputs_the_callers_provide(detector: Detector) -> None:
    env = _job(detector)["env"]
    assert env["EVENT_PR"] == "${{ github.event.pull_request.number }}"
    assert env["EVENT_HEAD"] == "${{ github.event.pull_request.head.sha }}"
    assert env["INPUT_PR"] == "${{ inputs.pr }}"
    assert ("INPUT_HEAD" in env) == bool(detector.handled_inputs)


@requires_tools
@DETECTORS
def test_a_silent_no_op_with_no_posted_status_fails_even_though_the_callee_succeeded(
    detector: Detector, tmp_path: Path
) -> None:
    done = _run(detector, tmp_path, result="success")
    assert done.returncode == 1, done.stdout + done.stderr
    assert f"no {detector.job} status" in done.stdout
    assert HEAD in done.stdout


@requires_tools
@DETECTORS
def test_a_skipped_callee_cannot_bypass_the_detector(detector: Detector, tmp_path: Path) -> None:
    done = _run(detector, tmp_path, result="skipped")
    assert done.returncode == 1, done.stdout + done.stderr


@requires_tools
@DETECTORS
@pytest.mark.parametrize("event", ["pull_request_target", "workflow_dispatch"])
def test_a_status_posted_for_the_head_keeps_the_detector_green(detector: Detector, event: str, tmp_path: Path) -> None:
    statuses = {HEAD: [_status(detector.contexts[0], state="pending")]}
    done = _run(detector, tmp_path, event=event, statuses=statuses)
    assert done.returncode == 0, done.stdout + done.stderr


@requires_tools
@DETECTORS
def test_a_status_from_another_account_or_context_does_not_count(detector: Detector, tmp_path: Path) -> None:
    statuses = {
        HEAD: [
            _status(detector.contexts[0], creator="someone"),
            _status("ci/unit"),
            _status(detector.contexts[0].replace("review", "oracle")),
        ]
    }
    assert _run(detector, tmp_path, statuses=statuses).returncode == 1


@requires_tools
@DETECTORS
def test_a_status_on_a_different_head_does_not_count(detector: Detector, tmp_path: Path) -> None:
    statuses = {OLD_HEAD: [_status(detector.contexts[0])]}
    assert _run(detector, tmp_path, statuses=statuses).returncode == 1


@requires_tools
@DETECTORS
@pytest.mark.parametrize("result", ["failure", "cancelled"])
def test_a_callee_that_failed_stays_red_even_when_a_status_exists(
    detector: Detector, result: str, tmp_path: Path
) -> None:
    statuses = {HEAD: [_status(detector.contexts[0])]}
    assert _run(detector, tmp_path, result=result, statuses=statuses).returncode == 1


@requires_tools
@DETECTORS
def test_an_unrecognised_callee_result_fails_closed(detector: Detector, tmp_path: Path) -> None:
    statuses = {HEAD: [_status(detector.contexts[0])]}
    assert _run(detector, tmp_path, result="", statuses=statuses).returncode == 1


@requires_tools
@DETECTORS
@pytest.mark.parametrize("event", ["schedule", "push", "issue_comment", "workflow_run"])
@pytest.mark.parametrize("result", ["success", "skipped"])
def test_sweeps_and_comment_runs_post_nothing_for_one_head_and_stay_green(
    detector: Detector, event: str, result: str, tmp_path: Path
) -> None:
    done = _run(detector, tmp_path, event=event, result=result)
    assert done.returncode == 0, done.stdout + done.stderr
    assert not (tmp_path / "fake" / "status-reads").exists()


@requires_tools
@DETECTORS
@pytest.mark.parametrize(
    "pull",
    [
        {"state": "closed"},
        {"base": {"ref": "feature/x"}},
        {"base": {"ref": "release"}},
        {"base": {"ref": "release/0.5.x/extra"}},
        {"draft": True},
        {"head": {"sha": OLD_HEAD}},
    ],
    ids=["closed", "feature-base", "bare-release", "nested-release", "draft", "head-moved"],
)
def test_pull_requests_the_callee_skips_by_design_stay_green(
    detector: Detector, pull: dict[str, Any], tmp_path: Path
) -> None:
    done = _run(detector, tmp_path, result="skipped", pull=pull)
    assert done.returncode == 0, done.stdout + done.stderr
    assert not (tmp_path / "fake" / "status-reads").exists()


@requires_tools
@DETECTORS
@pytest.mark.parametrize("base", ["develop", "release/0.5.x"])
def test_pull_requests_into_a_gated_base_still_require_a_posted_status(
    detector: Detector, base: str, tmp_path: Path
) -> None:
    done = _run(detector, tmp_path, result="success", pull={"base": {"ref": base}})
    assert done.returncode == 1, done.stdout + done.stderr
    assert f"no {detector.job} status" in done.stdout


@requires_tools
def test_a_dispatch_for_a_head_that_has_since_moved_skips_by_design(tmp_path: Path) -> None:
    done = _run(VERDICT, tmp_path, event="workflow_dispatch", result="success", handled=OLD_HEAD)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "moved" in done.stdout


@requires_tools
def test_a_dispatch_without_a_head_judges_the_current_head(tmp_path: Path) -> None:
    done = _run(VERDICT, tmp_path, event="workflow_dispatch", handled="")
    assert done.returncode == 1, done.stdout + done.stderr
    assert HEAD in done.stdout


@requires_tools
def test_the_gate_dispatch_judges_the_current_head(tmp_path: Path) -> None:
    done = _run(GATE, tmp_path, event="workflow_dispatch")
    assert done.returncode == 1, done.stdout + done.stderr
    done = _run(GATE, tmp_path / "again", event="workflow_dispatch", statuses={HEAD: [_status("review-gate")]})
    assert done.returncode == 0, done.stdout + done.stderr


@requires_tools
def test_a_verdict_title_edit_starts_no_review_but_a_retarget_does(tmp_path: Path) -> None:
    edited = _run(VERDICT, tmp_path / "edit", action="edited")
    assert edited.returncode == 0, edited.stdout + edited.stderr
    retargeted = _run(VERDICT, tmp_path / "retarget", action="edited", base_changed=True)
    assert retargeted.returncode == 1, retargeted.stdout + retargeted.stderr


@requires_tools
def test_a_gate_title_edit_still_requires_a_gate_status(tmp_path: Path) -> None:
    assert _run(GATE, tmp_path, action="edited").returncode == 1
