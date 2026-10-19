from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tests.utilities.posix_shell import run_posix_shell, skip_without_posix_shell

pytestmark = [pytest.mark.unit, pytest.mark.medium]

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "site-inputs.yml"

CORE_SHA = "a" * 40
NEXT_SHA = "b" * 40
RUN_URL = "https://example.test/run/123"
REPORTER_TITLE = "Site-inputs bundle build failed"
RELAY_TITLE = "Site could not deploy a core bundle"

_STUB = """#!SHEBANG
import json
import os
import subprocess
import sys


def main() -> int:
    args = sys.argv[1:]
    with open(os.environ["GH_CALLS"], "a", encoding="utf-8") as handle:
        handle.write(json.dumps(args) + "\\n")
    payload = None
    if args[:2] == ["issue", "list"]:
        number = os.environ.get("GH_OPEN_NUMBER", "")
        payload = [{"number": int(number)}] if number else []
    elif args[:2] == ["issue", "view"]:
        payload = {
            "title": os.environ.get("GH_VIEW_TITLE", ""),
            "body": os.environ.get("GH_VIEW_BODY", ""),
            "comments": json.loads(os.environ.get("GH_COMMENTS", "[]")),
        }
    if payload is None:
        return 0
    if "--jq" in args:
        filt = args[args.index("--jq") + 1]
        proc = subprocess.run(["jq", "-r", filt], input=json.dumps(payload), text=True, capture_output=True)
        sys.stdout.write(proc.stdout)
        return proc.returncode
    sys.stdout.write(json.dumps(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
"""


def _step(job: str, index: int = 0) -> str:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    return str(workflow["jobs"][job]["steps"][index]["run"])


def _marked_body(failure_class: str, sha: str) -> str:
    return f"older report\n\n<!-- site-bundle-reporter class={failure_class} sha={sha} -->"


def _run_reporter(
    tmp_path: Path, script: str, env: dict[str, str]
) -> tuple[subprocess.CompletedProcess, list[list[str]]]:
    skip_without_posix_shell()
    if shutil.which("jq") is None:
        pytest.skip("reporter execution requires jq")
    stub = tmp_path / "gh"
    stub.write_text(_STUB.replace("SHEBANG", sys.executable), encoding="utf-8")
    stub.chmod(0o755)
    calls = tmp_path / "gh-calls.jsonl"
    result = run_posix_shell(
        'set -eo pipefail\nexport PATH="$PWD:$PATH"\n' + script,
        cwd=tmp_path,
        env={**os.environ, "GH_CALLS": str(calls), **env},
        capture_output=True,
        text=True,
        check=False,
    )
    logged = []
    if calls.exists():
        logged = [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()]
    return result, logged


def _report_env(
    failure_class: str = "",
    open_number: str = "",
    comments: str = "[]",
    expected_rejection: str = "",
    core_sha: str = CORE_SHA,
    view_title: str = REPORTER_TITLE,
    view_body: str = "",
) -> dict[str, str]:
    return {
        "REPO": "example/repo",
        "CORE_SHA": core_sha,
        "RUN_URL": RUN_URL,
        "FAILURE_CLASS": failure_class,
        "EXPECTED_REJECTION": expected_rejection,
        "GH_OPEN_NUMBER": open_number,
        "GH_COMMENTS": comments,
        "GH_VIEW_TITLE": view_title,
        "GH_VIEW_BODY": view_body,
    }


def _create_body(calls: list[list[str]]) -> str:
    create = next(args for args in calls if args[:2] == ["issue", "create"])
    assert create[create.index("--label") + 1] == "site-bundle-failed"
    return create[create.index("--body") + 1]


def test_failure_without_open_issue_creates_it(tmp_path: Path) -> None:
    result, calls = _run_reporter(tmp_path, _step("report-failure"), _report_env("parent-bundle-missing"))
    assert result.returncode == 0, result.stdout + result.stderr
    assert [args[:2] for args in calls] == [["issue", "list"], ["label", "create"], ["issue", "create"]]
    body = _create_body(calls)
    assert f"class=parent-bundle-missing sha={CORE_SHA}" in body
    assert "Close this issue once a bundle succeeds again." in body


def test_failure_with_same_class_and_sha_stays_silent(tmp_path: Path) -> None:
    comments = json.dumps([{"body": _marked_body("not-trunk-certified", CORE_SHA)}])
    result, calls = _run_reporter(tmp_path, _step("report-failure"), _report_env("not-trunk-certified", "7", comments))
    assert result.returncode == 0, result.stdout + result.stderr
    assert [args[:2] for args in calls] == [["issue", "list"], ["issue", "view"]]


def test_failure_with_new_class_comments(tmp_path: Path) -> None:
    comments = json.dumps([{"body": _marked_body("parent-bundle-missing", CORE_SHA)}])
    result, calls = _run_reporter(tmp_path, _step("report-failure"), _report_env("other", "7", comments))
    assert result.returncode == 0, result.stdout + result.stderr
    comment = next(args for args in calls if args[:2] == ["issue", "comment"])
    assert comment[2] == "7"
    assert f"class=other sha={CORE_SHA}" in comment[comment.index("--body") + 1]
    assert not [args for args in calls if args[:2] == ["issue", "create"]]


def test_failure_with_new_sha_comments(tmp_path: Path) -> None:
    comments = json.dumps([{"body": _marked_body("other", CORE_SHA)}])
    result, calls = _run_reporter(
        tmp_path, _step("report-failure"), _report_env("other", "7", comments, core_sha=NEXT_SHA)
    )
    assert result.returncode == 0, result.stdout + result.stderr
    comment = next(args for args in calls if args[:2] == ["issue", "comment"])
    assert f"class=other sha={NEXT_SHA}" in comment[comment.index("--body") + 1]


def test_failure_with_unmarked_history_comments_once(tmp_path: Path) -> None:
    comments = json.dumps([{"body": "The site-inputs bundle build failed for core commit old."}])
    result, calls = _run_reporter(tmp_path, _step("report-failure"), _report_env("other", "7", comments))
    assert result.returncode == 0, result.stdout + result.stderr
    assert len([args for args in calls if args[:2] == ["issue", "comment"]]) == 1


def test_unknown_failure_class_falls_back_to_other(tmp_path: Path) -> None:
    result, calls = _run_reporter(tmp_path, _step("report-failure"), _report_env("bogus"))
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"class=other sha={CORE_SHA}" in _create_body(calls)


def test_expected_rejection_probe_creates_no_incident(tmp_path: Path) -> None:
    result, calls = _run_reporter(
        tmp_path, _step("report-failure"), _report_env("not-trunk-certified", expected_rejection="true")
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == []


def test_success_without_open_issue_changes_nothing(tmp_path: Path) -> None:
    result, calls = _run_reporter(tmp_path, _step("close-failure"), _report_env())
    assert result.returncode == 0, result.stdout + result.stderr
    assert [args[:2] for args in calls] == [["issue", "list"]]


def test_success_with_open_issue_comments_and_closes(tmp_path: Path) -> None:
    result, calls = _run_reporter(tmp_path, _step("close-failure"), _report_env(open_number="9"))
    assert result.returncode == 0, result.stdout + result.stderr
    assert [args[:2] for args in calls] == [
        ["issue", "list"],
        ["issue", "view"],
        ["issue", "comment"],
        ["issue", "close"],
    ]
    comment = calls[2]
    assert comment[2] == "9" and calls[3][2] == "9"
    body = comment[comment.index("--body") + 1]
    assert f"succeeded for core commit {CORE_SHA}" in body
    assert RUN_URL in body


def test_success_with_relay_titled_issue_closes_nothing(tmp_path: Path) -> None:
    env = _report_env(open_number="9", view_title=RELAY_TITLE)
    result, calls = _run_reporter(tmp_path, _step("close-failure"), env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert [args[:2] for args in calls] == [["issue", "list"], ["issue", "view"]]


def test_expected_rejection_probe_closes_no_incident(tmp_path: Path) -> None:
    result, calls = _run_reporter(tmp_path, _step("close-failure"), _report_env("", "9", expected_rejection="true"))
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == []


def test_failure_matching_issue_body_marker_stays_silent(tmp_path: Path) -> None:
    env = _report_env("other", "7", view_body=_marked_body("other", CORE_SHA))
    result, calls = _run_reporter(tmp_path, _step("report-failure"), env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert [args[:2] for args in calls] == [["issue", "list"], ["issue", "view"]]


def test_failure_after_newer_marked_comment_uses_last_marker(tmp_path: Path) -> None:
    comments = json.dumps(
        [{"body": _marked_body("other", CORE_SHA)}, {"body": _marked_body("parent-bundle-missing", CORE_SHA)}]
    )
    result, calls = _run_reporter(tmp_path, _step("report-failure"), _report_env("other", "7", comments))
    assert result.returncode == 0, result.stdout + result.stderr
    assert len([args for args in calls if args[:2] == ["issue", "comment"]]) == 1


def test_empty_failure_class_falls_back_to_other(tmp_path: Path) -> None:
    result, calls = _run_reporter(tmp_path, _step("report-failure"), _report_env())
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"class=other sha={CORE_SHA}" in _create_body(calls)


def test_reporter_case_arms_match_the_classifier_vocabulary() -> None:
    spec = importlib.util.spec_from_file_location(
        "site_inputs_parent_cases", ROOT / "scripts" / "site_inputs_parent.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    match = re.search(r"^\s*([\w|-]+)\) CLASS=\"\$FAILURE_CLASS\"", _step("report-failure"), re.MULTILINE)
    assert match is not None
    assert set(match.group(1).split("|")) == set(module.FAILURE_CLASSES)
