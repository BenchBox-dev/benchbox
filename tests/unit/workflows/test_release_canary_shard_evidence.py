from __future__ import annotations

import json
import os
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import yaml

from tests.utilities.posix_shell import posix_shell, run_posix_shell, skip_without_posix_shell

pytestmark = [pytest.mark.unit, pytest.mark.medium]

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "release-canary.yml"

_REAL_JUNIT = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<testsuite tests="1" errors="0" failures="1" skipped="0">'
    '<testcase classname="tests.unit.test_a" name="test_one" time="2.5"/></testsuite>'
)


def _steps() -> dict[str, dict]:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    return {step.get("name"): step for step in workflow["jobs"]["credential-free-non-fast"]["steps"]}


def _shell_has_mapfile() -> bool:
    if posix_shell() is None:
        return False
    probe = run_posix_shell('mapfile -t probe <<< "a b c"', capture_output=True, text=True, encoding="utf-8")
    return probe.returncode == 0


_MAPFILE_SHIM = r"""\
if ! declare -F mapfile >/dev/null 2>&1; then
  mapfile() {
    local _name="" _line _arr=()
    while [ "$#" -gt 0 ]; do
      case "$1" in
        -t) shift ;;
        *) _name="$1"; shift; break ;;
      esac
    done
    while IFS= read -r _line || [ -n "$_line" ]; do
      _arr[${#_arr[@]}]="$_line"
    done
    if [ -n "$_name" ]; then
      eval "$_name=(\"\${_arr[@]}\")"
    fi
  }
fi
"""


def _shard_prelude() -> str:
    return "" if _shell_has_mapfile() else _MAPFILE_SHIM


def _run_under_errexit(script: str, *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    assert posix_shell() is not None, "a real POSIX shell is required to execute workflow blocks"
    return run_posix_shell("set -e\n" + script, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8")


def _stub_uv(write_report: bool) -> str:
    report = ""
    if write_report:
        report = f"printf '%s' '{_REAL_JUNIT}' > \"$junit\"; "
    return (
        "uv() {\n"
        '  local junit=""\n'
        '  for a in "$@"; do case "$a" in --junitxml=*) junit="${a#--junitxml=}";; esac; done\n'
        f"  {report}"
        '  exit "$STUB_PYTEST_EXIT"\n'
        "}\n"
    )


def _shard_workspace(tmp_path: Path, *, node_ids: list[str], stamps: dict[str, str] | None = None) -> Path:
    workspace = tmp_path / "workspace"
    artifacts = workspace / "release-canary-artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "shard-0-nodeids.txt").write_text(
        "\n".join(node_ids) + ("\n" if node_ids else ""), encoding="utf-8", newline="\n"
    )
    (workspace / "gh_output.txt").touch()
    for name, value in (stamps or {}).items():
        (workspace / name).write_text(value, encoding="utf-8")
    return workspace


def _shard_env(workspace: Path, **extra: str) -> dict[str, str]:
    env = {
        "PATH": os.environ["PATH"],
        "HOME": os.environ.get("HOME", "/root"),
        "GITHUB_WORKSPACE": str(workspace),
        "GITHUB_OUTPUT": str(workspace / "gh_output.txt"),
        "SHARD_INDEX": "0",
        "SHARD_COUNT": "6",
        "CHECKED_SHA": "deadbeef" * 5,
        "COLLECTED_COUNT": "42",
    }
    env.update(extra)
    return env


def _run_shard_step(tmp_path: Path, *, pytest_exit: int, write_report: bool, node_ids: list[str]) -> tuple[int, Path]:
    workspace = _shard_workspace(tmp_path, node_ids=node_ids)
    script = _shard_prelude() + _stub_uv(write_report) + _steps()["Run credential-free non-fast shard"]["run"]
    result = _run_under_errexit(
        script,
        cwd=workspace,
        env=_shard_env(workspace, STUB_PYTEST_EXIT=str(pytest_exit)),
    )
    assert result.returncode == pytest_exit, (
        f"the shard step must exit with pytest's status ({pytest_exit}), got {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    return result.returncode, workspace


def _summary_json(workspace: Path, *, pytest_exit_code: str = "") -> dict:
    script = _steps()["Write shard summary"]["run"]
    result = _run_under_errexit(
        script,
        cwd=workspace,
        env=_shard_env(workspace, PYTEST_EXIT_CODE=pytest_exit_code),
    )
    assert result.returncode == 0, (
        f"the summary step must succeed regardless of the shard outcome; stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
    return json.loads((workspace / "release-canary-artifacts" / "shard-0-summary.json").read_text(encoding="utf-8"))


def test_failing_shard_still_records_its_evidence(tmp_path: Path) -> None:
    skip_without_posix_shell()

    _status, workspace = _run_shard_step(
        tmp_path, pytest_exit=1, write_report=True, node_ids=["tests/unit/test_a.py::test_one"]
    )

    output = (workspace / "gh_output.txt").read_text(encoding="utf-8")
    assert "pytest_exit_code=1" in output, "a failing shard must still publish its pytest exit code"
    assert (workspace / ".canary-tests-end-mono").exists(), "a failing shard must still stamp its test end time"
    assert (workspace / ".canary-junit-captured").read_text(encoding="utf-8").strip() == "true", (
        "pytest wrote a real JUnit report, so capture state must be true; a missing marker would "
        "make the summary claim no per-test durations exist when they do"
    )


def test_failing_shard_summary_reports_captured_durations(tmp_path: Path) -> None:
    skip_without_posix_shell()

    _status, workspace = _run_shard_step(
        tmp_path, pytest_exit=1, write_report=True, node_ids=["tests/unit/test_a.py::test_one"]
    )
    summary = _summary_json(workspace, pytest_exit_code="1")

    assert summary["pytest_exit_code"] == "1"
    assert summary["junit_present"] is True
    assert summary["junit_captured"] is True, "the report holds real durations; the summary must say so"


def test_missing_junit_falls_back_to_a_marked_empty_report(tmp_path: Path) -> None:
    skip_without_posix_shell()

    _status, workspace = _run_shard_step(
        tmp_path, pytest_exit=1, write_report=False, node_ids=["tests/unit/test_a.py::test_one"]
    )

    assert (workspace / ".canary-junit-captured").read_text(encoding="utf-8").strip() == "false"
    summary = _summary_json(workspace, pytest_exit_code="1")
    assert summary["junit_present"] is True
    assert summary["junit_captured"] is False


def test_shard_with_no_assigned_tests_reports_no_capture(tmp_path: Path) -> None:
    skip_without_posix_shell()

    status, workspace = _run_shard_step(tmp_path, pytest_exit=0, write_report=False, node_ids=[])

    assert status == 0
    summary = _summary_json(workspace, pytest_exit_code="0")
    assert summary["junit_present"] is True
    assert summary["junit_captured"] is False
    assert summary["assigned_count"] == "0"


def test_passing_shard_still_reports_its_status(tmp_path: Path) -> None:
    skip_without_posix_shell()

    status, workspace = _run_shard_step(
        tmp_path, pytest_exit=0, write_report=True, node_ids=["tests/unit/test_a.py::test_one"]
    )

    assert status == 0
    assert "pytest_exit_code=0" in (workspace / "gh_output.txt").read_text(encoding="utf-8")
    summary = _summary_json(workspace, pytest_exit_code="0")
    assert summary["junit_present"] is True
    assert summary["junit_captured"] is True


def test_summary_degrades_when_the_test_step_never_ran(tmp_path: Path) -> None:
    skip_without_posix_shell()

    workspace = _shard_workspace(tmp_path, node_ids=["tests/unit/test_a.py::test_one"])
    summary = _summary_json(workspace)

    assert summary["setup_seconds"] == ""
    assert summary["test_seconds"] == ""
    assert summary["junit_present"] is False
    assert summary["junit_captured"] is False


def test_durations_come_from_monotonic_stamps(tmp_path: Path) -> None:
    skip_without_posix_shell()

    workspace = _shard_workspace(
        tmp_path,
        node_ids=["tests/unit/test_a.py::test_one"],
        stamps={
            ".canary-setup-start-mono": "100000.75",
            ".canary-tests-start-mono": "100055.25",
            ".canary-tests-end-mono": "100355.10",
        },
    )
    summary = _summary_json(workspace)

    assert summary["setup_seconds"] == "54", "54.50 elapsed truncates to 54 whole seconds"
    assert summary["test_seconds"] == "299", "299.85 elapsed truncates to 299 whole seconds"
    assert summary["setup_start_mono"] == "100000.75"
    assert summary["tests_end_mono"] == "100355.10"


def test_a_partial_stamp_leaves_only_the_missing_duration_empty(tmp_path: Path) -> None:
    skip_without_posix_shell()

    workspace = _shard_workspace(
        tmp_path,
        node_ids=["tests/unit/test_a.py::test_one"],
        stamps={
            ".canary-setup-start-mono": "100000.75",
            ".canary-tests-start-mono": "100055.25",
        },
    )
    summary = _summary_json(workspace)

    assert summary["setup_seconds"] == "54", "setup is bounded by two present stamps"
    assert summary["test_seconds"] == "", "the end stamp is missing, so no duration may be inferred"


def test_a_non_true_capture_marker_degrades_to_false(tmp_path: Path) -> None:
    skip_without_posix_shell()

    workspace = _shard_workspace(tmp_path, node_ids=["tests/unit/test_a.py::test_one"])
    (workspace / ".canary-junit-captured").write_text("yes\n", encoding="utf-8")
    summary = _summary_json(workspace)

    assert summary["junit_captured"] is False


def test_summary_json_is_valid_and_typed(tmp_path: Path) -> None:
    skip_without_posix_shell()

    _status, workspace = _run_shard_step(
        tmp_path, pytest_exit=0, write_report=True, node_ids=["tests/unit/test_a.py::test_one"]
    )
    _summary_json(workspace)
    raw = (workspace / "release-canary-artifacts" / "shard-0-summary.json").read_text(encoding="utf-8")
    summary = json.loads(raw)

    assert summary["shard_index"] == 0
    assert isinstance(summary["junit_present"], bool)
    assert isinstance(summary["junit_captured"], bool)
    assert '"setup_start_epoch"' not in raw, "wall-clock epoch naming must not return"


_FIXTURE_MARKED = """\
import time

import pytest


@pytest.mark.slow
def test_marked():
    time.sleep(0.4)
    assert True
"""

_FIXTURE_UNMARKED = """\
import pytest


def test_deselected_by_the_marker_expression():
    assert True
"""


def _real_uv_shim() -> str:
    return 'uv() {\n  if [ "$1" = "run" ] && [ "$2" = "--" ]; then shift 2; fi\n  "$@"\n}\n'


def _junit_testcases(xml_path: Path) -> list[tuple[str, str | None]]:
    root = ET.parse(xml_path).getroot()
    return [(node.get("name", ""), node.get("time")) for node in root.iter("testcase")]


def _run_real_shard(tmp_path: Path, *, fixture: str, node_id: str) -> tuple[int, Path]:
    workspace = _shard_workspace(tmp_path, node_ids=[node_id])
    (workspace / "fixture.py").write_text(fixture, encoding="utf-8")
    (workspace / "pytest.ini").write_text(
        "[pytest]\nmarkers =\n    slow: canary fixture marker\naddopts = -p no:cacheprovider\n",
        encoding="utf-8",
    )
    script = _shard_prelude() + _real_uv_shim() + _steps()["Run credential-free non-fast shard"]["run"]
    result = _run_under_errexit(script, cwd=workspace, env=_shard_env(workspace))
    return result.returncode, workspace


def test_a_real_pytest_run_publishes_real_per_test_durations(tmp_path: Path) -> None:
    skip_without_posix_shell()
    pytest.importorskip("pytest", reason="the real shard block drives a real pytest")

    status, workspace = _run_real_shard(tmp_path, fixture=_FIXTURE_MARKED, node_id="fixture.py::test_marked")
    assert status == 0

    junit_xml = workspace / "release-canary-artifacts" / "shard-0-junit.xml"
    assert junit_xml.is_file(), "the real pytest must write the report the step asked for"

    testcases = _junit_testcases(junit_xml)
    assert [name for name, _ in testcases] == ["test_marked"]
    durations = [time for _, time in testcases]
    assert all(time is not None for time in durations), "every testcase must carry a duration"
    assert float(durations[0]) >= 0.3, f"a real duration must reflect the work done: {durations[0]}"

    summary = _summary_json(workspace, pytest_exit_code="0")
    assert summary["junit_present"] is True
    assert summary["junit_captured"] is True
    assert summary["assigned_count"] == "1"


def test_a_real_pytest_that_collected_nothing_is_not_published_as_a_capture(
    tmp_path: Path,
) -> None:
    skip_without_posix_shell()
    pytest.importorskip("pytest", reason="the real shard block drives a real pytest")

    status, workspace = _run_real_shard(
        tmp_path,
        fixture=_FIXTURE_UNMARKED,
        node_id="fixture.py::test_deselected_by_the_marker_expression",
    )
    assert status == 5, "pytest exits 5 when it collected nothing to run"

    junit_xml = workspace / "release-canary-artifacts" / "shard-0-junit.xml"
    assert junit_xml.stat().st_size > 0, "a real zero-test report is still a non-empty file"
    assert _junit_testcases(junit_xml) == [], "and it holds no durations"

    summary = _summary_json(workspace, pytest_exit_code="5")
    assert summary["pytest_exit_code"] == "5", "the shard result must still be published"
    assert summary["junit_present"] is True, "the real report is genuine evidence pytest ran"
    assert summary["junit_captured"] is False, "but it carries no durations to size a shard from"


def test_the_real_setup_stamp_step_survives_errexit_without_a_kernel_file(
    tmp_path: Path,
) -> None:
    skip_without_posix_shell()

    workspace = _shard_workspace(tmp_path, node_ids=[])
    script = _steps()["Record setup start"]["run"]
    result = _run_under_errexit(script, cwd=workspace, env=_shard_env(workspace))
    assert result.returncode == 0, f"the stamp step must not fail the job: {result.stderr}"

    stamp_file = workspace / ".canary-setup-start-mono"
    assert stamp_file.is_file(), "the stamp file must exist so the summary can degrade gracefully"
    stamp = stamp_file.read_text(encoding="utf-8").strip()

    if run_posix_shell("[ -r /proc/uptime ]", check=False, capture_output=True).returncode == 0:
        assert stamp, "/proc/uptime is readable, so a real stamp must be recorded"
        value = float(stamp)
        assert value > 0.0, "seconds since boot must be positive"
        assert "." in stamp, f"the stamp must keep its fractional part: {stamp}"
    else:
        assert stamp == "", f"with no kernel file the stamp degrades to empty, got {stamp!r}"
        summary = _summary_json(workspace)
        assert summary["setup_seconds"] == "", "an empty stamp must not invent a duration"
