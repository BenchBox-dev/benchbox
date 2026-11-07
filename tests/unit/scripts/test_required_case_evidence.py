from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import scripts.check_make_targets as make_targets
from scripts.release_canary_sharding import verify_required_cases

pytestmark = [pytest.mark.unit, pytest.mark.medium]
ROOT = Path(__file__).resolve().parents[3]
SHA = "b" * 40
REQUIRED = ["tests/test_required.py::test_a", "tests/test_required.py::test_b"]

MODULES = {
    "pass": "def test_a():\n    assert True\n\ndef test_b():\n    assert True\n",
    "failure": "def test_a():\n    assert True\n\ndef test_b():\n    assert False\n",
    "mark-skip": "import pytest\n\ndef test_a():\n    assert True\n\n@pytest.mark.skip(reason='not available')\ndef test_b():\n    pass\n",
    "call-skip": "import pytest\n\ndef test_a():\n    assert True\n\ndef test_b():\n    pytest.skip('optional runtime')\n",
    "xfail": "import pytest\n\ndef test_a():\n    assert True\n\n@pytest.mark.xfail(reason='known limitation')\ndef test_b():\n    assert False\n",
    "xpass": "import pytest\n\ndef test_a():\n    assert True\n\n@pytest.mark.xfail(reason='known limitation')\ndef test_b():\n    assert True\n",
}


def _run(tmp_path: Path, body: str, *extra: str, selected: list[str] | None = None) -> tuple[Path, Path, int]:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_required.py").write_text(body)
    (tmp_path / "pytest.ini").write_text("[pytest]\naddopts =\n")
    nodeids = tmp_path / "required.txt"
    nodeids.write_text("\n".join(REQUIRED) + "\n")
    evidence = tmp_path / "evidence.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "scripts.pytest_shard_evidence",
            "--assigned-nodeids",
            str(nodeids),
            "--shard-evidence",
            str(evidence),
            "--checked-sha",
            SHA,
            *extra,
            *(selected if selected is not None else REQUIRED),
        ],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(ROOT), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
        capture_output=True,
        text=True,
        timeout=60,
    )
    return evidence, nodeids, result.returncode


def test_a_run_that_passes_every_required_case_is_accepted(tmp_path: Path) -> None:
    evidence, nodeids, status = _run(tmp_path, MODULES["pass"])
    assert status == 0
    verify_required_cases(evidence, SHA, nodeids)


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("failure", "not all collected and executed successfully"),
        ("mark-skip", "did not run setup, call and teardown"),
        ("call-skip", "did not pass"),
        ("xfail", "did not pass"),
        ("xpass", "skipped or expected to fail"),
    ],
)
def test_a_required_case_that_skips_fails_or_is_expected_to_fail_is_rejected(
    tmp_path: Path, case: str, message: str
) -> None:
    evidence, nodeids, _ = _run(tmp_path, MODULES[case])
    with pytest.raises(ValueError, match=message):
        verify_required_cases(evidence, SHA, nodeids)


def test_a_deselected_required_case_is_rejected(tmp_path: Path) -> None:
    evidence, nodeids, status = _run(tmp_path, MODULES["pass"], "-k", "not test_b")
    assert status != 0
    with pytest.raises(ValueError, match="not all collected and executed successfully"):
        verify_required_cases(evidence, SHA, nodeids)


def test_a_missing_required_case_is_rejected(tmp_path: Path) -> None:
    evidence, nodeids, status = _run(tmp_path, MODULES["pass"], selected=REQUIRED[:1])
    assert status != 0
    with pytest.raises(ValueError, match="not all collected and executed successfully"):
        verify_required_cases(evidence, SHA, nodeids)


def test_evidence_for_another_commit_is_rejected(tmp_path: Path) -> None:
    evidence, nodeids, _ = _run(tmp_path, MODULES["pass"])
    with pytest.raises(ValueError, match="checked SHA"):
        verify_required_cases(evidence, "c" * 40, nodeids)


def test_missing_evidence_and_an_empty_required_list_are_rejected(tmp_path: Path) -> None:
    evidence, nodeids, _ = _run(tmp_path, MODULES["pass"])
    with pytest.raises(OSError):
        verify_required_cases(tmp_path / "absent.json", SHA, nodeids)
    nodeids.write_text("")
    with pytest.raises(ValueError, match="empty"):
        verify_required_cases(evidence, SHA, nodeids)


def test_the_command_line_exits_non_zero_for_a_skipped_required_case(tmp_path: Path) -> None:
    def verify(evidence: Path, nodeids: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "release_canary_sharding.py"),
                "verify-required",
                "--evidence",
                str(evidence),
                "--nodeids",
                str(nodeids),
                "--checked-sha",
                SHA,
            ],
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": str(ROOT)},
            capture_output=True,
            text=True,
            timeout=60,
        )

    passing = tmp_path / "pass"
    passing.mkdir()
    ok = verify(*_run(passing, MODULES["pass"])[:2])
    assert ok.returncode == 0, ok.stderr
    skipping = tmp_path / "skip"
    skipping.mkdir()
    bad = verify(*_run(skipping, MODULES["call-skip"])[:2])
    assert bad.returncode == 1
    assert "did not pass" in bad.stderr


def test_the_make_target_runs_the_required_cases_under_the_evidence_plugin() -> None:
    makefile = make_targets.makefile_sources(ROOT)
    body = makefile.split("\ntest-required-local-cases:", 1)[1].split("\n\n", 1)[0]
    assert "REQUIRED_LOCAL_CASES" in body
    assert '-m ""' in body
    for needle in ("-p scripts.pytest_shard_evidence", "--assigned-nodeids", "--shard-evidence", "--basetemp"):
        assert needle in body
    assert "release_canary_sharding.py verify-required" in body
    assert "exit $$STATUS" in body
