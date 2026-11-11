from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.release_canary_sharding import (
    MEDIUM_SHARD_COUNT,
    collect_node_ids,
    partition_node_ids,
    verify_medium_shards,
)

pytestmark = [pytest.mark.unit, pytest.mark.medium]
ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("workers", [0, 2])
@pytest.mark.parametrize(
    "case",
    ["complete", "missing", "failure", "skip", "call-skip", "xfail", "xpass", "teardown-failure"],
)
def test_actual_pytest_shard_execution(tmp_path: Path, workers: int, case: str) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    body = "def test_a():\n    assert True\n\ndef test_b():\n    assert True\n"
    if case == "failure":
        body = body.replace("def test_b():\n    assert True", "def test_b():\n    assert False")
    if case == "skip":
        body = "import pytest\n" + body.replace("def test_b():", "@pytest.mark.skip(reason='fixture')\ndef test_b():")
    elif case == "call-skip":
        body = "import pytest\n" + body.replace(
            "def test_b():\n    assert True", "def test_b():\n    pytest.skip('optional runtime')"
        )
    elif case in {"xfail", "xpass"}:
        body = "import pytest\n" + body.replace(
            "def test_b():", "@pytest.mark.xfail(reason='known limitation')\ndef test_b():"
        )
        if case == "xfail":
            body = body.replace("def test_b():\n    assert True", "def test_b():\n    assert False")
    elif case == "teardown-failure":
        body = (
            "import pytest\n@pytest.fixture\ndef cleanup():\n    yield\n    raise RuntimeError('teardown failure')\n"
            + body.replace("def test_b():", "def test_b(cleanup):")
        )
    (tests / "test_cases.py").write_text(body)
    (tmp_path / "pytest.ini").write_text("[pytest]\naddopts =\n")
    assigned = ["tests/test_cases.py::test_a", "tests/test_cases.py::test_b"]
    assignment = tmp_path / "assignment.txt"
    assignment.write_text("\n".join(assigned) + "\n")
    evidence = tmp_path / "execution.json"
    selected = assigned[:1] if case == "missing" else assigned
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-n",
            str(workers),
            "-p",
            "xdist.plugin",
            "-p",
            "scripts.pytest_shard_evidence",
            "--assigned-nodeids",
            str(assignment),
            "--shard-evidence",
            str(evidence),
            "--checked-sha",
            "a" * 40,
            *selected,
        ],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(ROOT), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    payload = json.loads(evidence.read_text())
    assert result.returncode == (1 if case in {"missing", "failure", "teardown-failure"} else 0), (
        result.stdout + result.stderr
    )
    assert payload["complete"] is (case != "missing")
    assert payload["commit_sha"] == "a" * 40
    assert payload["assigned_node_ids"] == assigned
    assert len(payload["collected_node_ids"]) == (workers or 1)
    assert all(collection == selected for collection in payload["collected_node_ids"])
    assert payload["executed_node_ids"] == selected
    assert payload["pytest_exit_status"] == (1 if case in {"failure", "teardown-failure"} else 0)
    outcomes = payload["node_outcomes"]
    assert [item["node_id"] for item in outcomes] == selected
    first = outcomes[0]["reports"]
    assert [report["phase"] for report in first] == ["setup", "call", "teardown"]
    assert all(report["outcome"] == "passed" for report in first)
    if case != "missing":
        reports = {report["phase"]: report for report in outcomes[1]["reports"]}
        phase = "setup" if case == "skip" else "teardown" if case == "teardown-failure" else "call"
        expected = (
            "failed"
            if case in {"failure", "teardown-failure"}
            else "skipped"
            if case in {"skip", "call-skip", "xfail"}
            else "passed"
        )
        assert reports[phase]["outcome"] == expected
        if expected == "skipped":
            reason = "fixture" if case == "skip" else "optional runtime" if case == "call-skip" else "known limitation"
            assert reason in reports[phase]["skip_reason"]
        if case in {"xfail", "xpass"}:
            assert reports["call"]["xfail_reason"] == "known limitation"


@pytest.mark.parametrize("workers", [0, 2])
@pytest.mark.parametrize("case", ["skip", "xfail-no-reason", "xfail-whitespace", "xpass-no-reason"])
def test_real_medium_receipts_reject_missing_or_inconsistent_outcomes(tmp_path: Path, workers: int, case: str) -> None:
    project = tmp_path / "project"
    tests = project / "tests"
    tests.mkdir(parents=True)
    (project / "pytest.ini").write_text("[pytest]\naddopts =\n")
    decorator = "@pytest.mark.skip(reason='optional engine absent')"
    assertion = "assert True"
    if case != "skip":
        decorator = "@pytest.mark.xfail(reason='  ')" if case == "xfail-whitespace" else "@pytest.mark.xfail"
        assertion = "assert True" if case == "xpass-no-reason" else "assert False"
    (tests / "test_cases.py").write_text(
        "import pytest\n"
        "def test_a():\n    assert True\n"
        f"{decorator}\ndef test_b():\n    {assertion}\n"
        "def test_c():\n    assert True\n"
        "def test_d():\n    assert True\n"
    )
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    collection_result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert collection_result.returncode == 0, collection_result.stdout + collection_result.stderr
    sha = "a" * 40
    artifacts = tmp_path / "artifacts"
    collection = artifacts / f"t2-medium-nodeids-{sha}"
    collection.mkdir(parents=True)
    output = collection / "collection.txt"
    output.write_text(collection_result.stdout)
    collect_node_ids(
        output,
        collection / "medium-nodeids.txt",
        collection / "medium-collection.json",
        expected_count=4,
        shard_count=MEDIUM_SHARD_COUNT,
        checked_sha=sha,
        workflow="ci.yml",
        job="medium-collect",
        marker_expression="medium and not (slow or stress or resource_heavy or live_integration)",
    )
    ids = (collection / "medium-nodeids.txt").read_text().splitlines()
    for index in range(MEDIUM_SHARD_COUNT):
        assigned = partition_node_ids(ids, index, MEDIUM_SHARD_COUNT)
        shard = artifacts / f"t2-medium-shard-{index}-{sha}"
        shard.mkdir()
        assignment = shard / "assignment.txt"
        assignment.write_text("\n".join(assigned) + "\n")
        evidence = shard / f"shard-{index}-execution.json"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-n",
                str(workers),
                "-p",
                "xdist.plugin",
                "-p",
                "scripts.pytest_shard_evidence",
                "--assigned-nodeids",
                str(assignment),
                "--shard-evidence",
                str(evidence),
                "--checked-sha",
                sha,
                *assigned,
            ],
            cwd=project,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    verify_medium_shards(artifacts, sha)
    receipt = json.loads((artifacts / f"t2-medium-shard-1-{sha}" / "shard-1-execution.json").read_text())
    reports = {report["phase"]: report for report in receipt["node_outcomes"][0]["reports"]}
    if case != "skip":
        assert reports["call"]["xfail_reason"] == ("  " if case == "xfail-whitespace" else "")
        assert reports["call"]["outcome"] == ("passed" if case == "xpass-no-reason" else "skipped")
    defects = [
        "absent",
        "not-list",
        "missing-node",
        "duplicate-node",
        "wrong-node",
        "missing-reports",
        "missing-phase",
        "duplicate-phase",
        "bad-phase",
        "bad-outcome",
        "missing-reason",
        "bad-xfail",
        "failed-with-zero-exit",
    ]
    for defect in defects:
        damaged = tmp_path / defect
        shutil.copytree(artifacts, damaged)
        path = damaged / f"t2-medium-shard-1-{sha}" / "shard-1-execution.json"
        payload = json.loads(path.read_text())
        if defect == "absent":
            payload.pop("node_outcomes", None)
        elif defect == "not-list":
            payload["node_outcomes"] = {}
        else:
            outcomes = payload.get("node_outcomes", [])
            if outcomes:
                reports = outcomes[0]["reports"]
                if defect == "missing-node":
                    outcomes.pop()
                elif defect == "duplicate-node":
                    outcomes.append(outcomes[0])
                elif defect == "wrong-node":
                    outcomes[0]["node_id"] = ids[0]
                elif defect == "missing-reports":
                    outcomes[0].pop("reports")
                elif defect == "missing-phase":
                    reports.pop()
                elif defect == "duplicate-phase":
                    reports.append(reports[0])
                elif defect == "bad-phase":
                    reports[0]["phase"] = "collection"
                elif defect == "bad-outcome":
                    reports[0]["outcome"] = "complete"
                elif defect == "missing-reason":
                    skipped = next((report for report in reports if report["outcome"] == "skipped"), reports[0])
                    skipped["outcome"] = "skipped"
                    skipped["skip_reason"] = None
                elif defect == "bad-xfail":
                    reports[0]["xfail_reason"] = []
                else:
                    reports[0]["outcome"] = "failed"
        path.write_text(json.dumps(payload))
        with pytest.raises(ValueError, match="outcome"):
            verify_medium_shards(damaged, sha)


