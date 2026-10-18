from __future__ import annotations

import itertools
import json
import re
from pathlib import Path

import pytest
import yaml

from scripts.release_canary_sharding import (
    DEFAULT_SHARD_COUNT,
    MARKER_EXPRESSION,
    MEDIUM_MARKER_EXPRESSION,
    MEDIUM_SHARD_COUNT,
    collect_node_ids,
    parse_collection_output,
    partition_node_ids,
    verify_medium_shards,
    write_shard,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).parents[2]


def _workflow() -> dict:
    return yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "release-canary.yml").read_text(encoding="utf-8"))


def _run_text(job_name: str) -> str:
    workflow = _workflow()
    return "\n".join(str(step.get("run", "")) for step in workflow["jobs"][job_name]["steps"])


def test_parse_collection_output_sorts_and_rejects_duplicate_node_ids():
    output = """
    Using CPython 3.12.13
    tests/unit/test_b.py::test_two
    tests/unit/test_a.py::test_one
    2/10 tests collected (8 deselected) in 0.1s
    """

    assert parse_collection_output(output) == [
        "tests/unit/test_a.py::test_one",
        "tests/unit/test_b.py::test_two",
    ]
    with pytest.raises(ValueError, match="duplicates"):
        parse_collection_output("tests/unit/test_a.py::test_one\ntests/unit/test_a.py::test_one\n")


def test_partition_is_deterministic_disjoint_and_complete():
    node_ids = [f"tests/unit/test_{index:02d}.py::test_case" for index in range(11, -1, -1)]

    first = [partition_node_ids(node_ids, index, DEFAULT_SHARD_COUNT) for index in range(DEFAULT_SHARD_COUNT)]
    second = [
        partition_node_ids(list(reversed(node_ids)), index, DEFAULT_SHARD_COUNT) for index in range(DEFAULT_SHARD_COUNT)
    ]

    assert first == second
    assigned = list(itertools.chain.from_iterable(first))
    assert len(assigned) == len(set(assigned)) == len(node_ids)
    assert sorted(assigned) == sorted(node_ids)


@pytest.mark.parametrize(
    ("shard_index", "shard_count"),
    [(-1, 4), (4, 4), (0, 0), (0, -1), (0, True), (True, 4)],
)
def test_invalid_shard_parameters_fail_closed(shard_index, shard_count):
    with pytest.raises(ValueError):
        partition_node_ids(["tests/unit/test_a.py::test_one"], shard_index, shard_count)


def test_collection_and_shard_manifests_conserve_node_ids(tmp_path: Path):
    raw = tmp_path / "collect.txt"
    nodeids = tmp_path / "nodeids.txt"
    collection_summary = tmp_path / "collection-summary.json"
    raw.write_text(
        "\n".join(f"tests/unit/test_{index}.py::test_case" for index in range(7))
        + "\n7/100 tests collected (93 deselected)\n",
        encoding="utf-8",
    )

    assert (
        collect_node_ids(
            raw,
            nodeids,
            collection_summary,
            expected_count=7,
            shard_count=4,
            checked_ref="develop",
            checked_sha="abc123",
        )
        == 7
    )
    collection_payload = collection_summary.read_text(encoding="utf-8")
    assert '"total_count": 7' in collection_payload
    assert '"checked_ref": "develop"' in collection_payload

    shard_paths = []
    for index in range(4):
        shard_path = tmp_path / f"shard-{index}.txt"
        write_shard(
            nodeids,
            shard_path,
            tmp_path / f"shard-{index}.json",
            shard_index=index,
            shard_count=4,
        )
        shard_paths.append(shard_path)

    assigned = list(
        itertools.chain.from_iterable(path.read_text(encoding="utf-8").splitlines() for path in shard_paths)
    )
    assert len(assigned) == len(set(assigned)) == 7
    assert sorted(assigned) == sorted(nodeids.read_text(encoding="utf-8").splitlines())


def _medium_artifacts(root: Path, sha: str, workflow: str = "ci.yml", prefix: str = "t2-medium") -> list[str]:
    collection = root / f"{prefix}-nodeids-{sha}"
    collection.mkdir()
    raw = collection / "raw.txt"
    ids = [f"tests/unit/test_a.py::test_{index}" for index in range(6)]
    raw.write_text("\n".join(ids) + "\n6/100 tests collected\n")
    collect_node_ids(
        raw,
        collection / "medium-nodeids.txt",
        collection / "medium-collection.json",
        expected_count=6,
        shard_count=MEDIUM_SHARD_COUNT,
        checked_sha=sha,
        workflow=workflow,
        job="medium-collect",
        marker_expression=MEDIUM_MARKER_EXPRESSION,
    )
    for index in range(MEDIUM_SHARD_COUNT):
        shard = root / f"{prefix}-shard-{index}-{sha}"
        shard.mkdir()
        write_shard(
            collection / "medium-nodeids.txt",
            shard / "assigned.txt",
            shard / "summary.json",
            shard_index=index,
            shard_count=MEDIUM_SHARD_COUNT,
            workflow=workflow,
            job="medium-test",
            marker_expression=MEDIUM_MARKER_EXPRESSION,
            collection_summary=collection / "medium-collection.json",
            checked_sha=sha,
        )
        assigned = partition_node_ids(ids, index, MEDIUM_SHARD_COUNT)
        (shard / f"shard-{index}-execution.json").write_text(
            json.dumps(
                {
                    "commit_sha": sha,
                    "complete": True,
                    "pytest_exit_status": 0,
                    "assigned_node_ids": assigned,
                    "executed_node_ids": assigned,
                    "collected_node_ids": [assigned, assigned],
                    "node_outcomes": [
                        {
                            "node_id": node_id,
                            "reports": [
                                {"phase": phase, "outcome": "passed", "skip_reason": None, "xfail_reason": None}
                                for phase in ("setup", "call", "teardown")
                            ],
                        }
                        for node_id in assigned
                    ],
                }
            )
        )
    return ids


def test_medium_artifact_union_covers_the_exact_collection(tmp_path: Path) -> None:
    _medium_artifacts(tmp_path, "a" * 40)
    verify_medium_shards(tmp_path, "a" * 40)


def test_release_medium_artifacts_verify_under_their_own_workflow_and_prefix(tmp_path: Path) -> None:
    sha = "a" * 40
    _medium_artifacts(tmp_path, sha, workflow="test.yml", prefix="release-medium")

    verify_medium_shards(tmp_path, sha, "test.yml", "release-medium")
    with pytest.raises((ValueError, FileNotFoundError)):
        verify_medium_shards(tmp_path, sha)
    with pytest.raises(ValueError):
        verify_medium_shards(tmp_path, sha, "ci.yml", "release-medium")


@pytest.mark.parametrize(
    "defect", ["missing", "wrong-sha", "failure", "incomplete", "deselected", "duplicated", "wrong-collection"]
)
def test_medium_shard_evidence_refuses_missing_or_changed_coverage(tmp_path: Path, defect: str) -> None:
    sha = "a" * 40
    _medium_artifacts(tmp_path, sha)
    path = tmp_path / f"t2-medium-shard-1-{sha}" / "shard-1-execution.json"
    data = json.loads(path.read_text())
    if defect == "missing":
        path.unlink()
    else:
        if defect == "wrong-sha":
            data["commit_sha"] = "b" * 40
        elif defect == "failure":
            data["pytest_exit_status"] = 1
        elif defect == "incomplete":
            data["complete"] = False
        elif defect == "deselected":
            data["executed_node_ids"].pop()
        elif defect == "duplicated":
            data["executed_node_ids"].append(data["executed_node_ids"][0])
        else:
            data["collected_node_ids"][1].pop()
        path.write_text(json.dumps(data))
    with pytest.raises((ValueError, FileNotFoundError)):
        verify_medium_shards(tmp_path, sha)


@pytest.mark.parametrize(
    "field,value",
    [
        ("commit_sha", "b" * 40),
        ("node_ids_sha256", "wrong"),
        ("shard_count", 3),
        ("total_count", 5),
        ("marker_expression", MARKER_EXPRESSION),
        ("workflow", "other.yml"),
    ],
)
def test_medium_partition_refuses_a_changed_collection(tmp_path: Path, field: str, value: object) -> None:
    sha = "a" * 40
    _medium_artifacts(tmp_path, sha)
    collection = tmp_path / f"t2-medium-nodeids-{sha}"
    summary = collection / "medium-collection.json"
    data = json.loads(summary.read_text())
    data[field] = value
    summary.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="does not match"):
        write_shard(
            collection / "medium-nodeids.txt",
            tmp_path / "ids",
            tmp_path / "summary",
            shard_index=0,
            shard_count=MEDIUM_SHARD_COUNT,
            workflow="ci.yml",
            job="medium-test",
            marker_expression=MEDIUM_MARKER_EXPRESSION,
            collection_summary=summary,
            checked_sha=sha,
        )


def test_release_canary_workflow_uses_collection_artifact_and_six_single_threaded_shards():
    workflow = _workflow()
    jobs = workflow["jobs"]

    assert set(jobs) == {
        "collect-credential-free-non-fast",
        "credential-free-non-fast",
        "ruleset-drift",
        "pypi-latest-installability",
        "release-canary-result",
        "release-canary-incident",
    }

    collection = jobs["collect-credential-free-non-fast"]
    assert collection["timeout-minutes"] == 15
    assert collection["steps"][0]["with"]["ref"] == "${{ env.RELEASE_CANARY_REF }}"
    collection_text = _run_text("collect-credential-free-non-fast")
    assert collection_text.count(MARKER_EXPRESSION) == 1
    assert "--collect-only" in collection_text
    assert "release-canary-nodeids" in "\n".join(str(step) for step in collection["steps"])
    assert "release_canary_sharding.py" in collection_text

    shards = jobs["credential-free-non-fast"]
    assert shards["needs"] == ["collect-credential-free-non-fast"]
    assert shards["strategy"]["fail-fast"] is False
    assert [entry["shard_index"] for entry in shards["strategy"]["matrix"]["include"]] == [0, 1, 2, 3, 4, 5]
    assert shards["timeout-minutes"] == 75
    assert shards["strategy"]["matrix"]["include"][-1]["shard_number"] == DEFAULT_SHARD_COUNT
    assert workflow["env"]["RELEASE_CANARY_SHARD_COUNT"] == DEFAULT_SHARD_COUNT
    shard_text = _run_text("credential-free-non-fast")
    assert shard_text.count(f'-m "{MARKER_EXPRESSION}"') == 1
    assert any(re.search(r"actions/download-artifact@[0-9a-f]{40}", str(step)) for step in shards["steps"]), (
        "canary shards must pin actions/download-artifact to an immutable SHA"
    )
    assert "release_canary_sharding.py" in shard_text
    assert "mapfile -t node_ids" in shard_text
    assert "-n 0" in shard_text
    assert "--maxfail=5" in shard_text
    assert '--junitxml="$junit_xml"' in shard_text
    assert "shard-${SHARD_INDEX}-junit.xml" in shard_text
    assert ".canary-setup-start-mono" in shard_text
    assert ".canary-tests-start-mono" in shard_text
    assert ".canary-tests-end-mono" in shard_text
    assert '"setup_seconds": "${setup_seconds}"' in shard_text
    assert '"test_seconds": "${test_seconds}"' in shard_text
    assert '"junit_present": ${junit_present}' in shard_text
    assert "/proc/uptime" in shard_text
    assert "date +%s" not in shard_text
    assert "set +e" in shard_text
    assert 'junit_captured="true"' in shard_text
    assert 'junit_captured="false"' in shard_text
    assert ".canary-junit-captured" in shard_text
    assert '"junit_captured": ${junit_captured}' in shard_text
    assert '[ "$junit_captured" != "true" ]' in shard_text
    assert 'name="junit_captured" value="false"' in shard_text
    assert 'grep -q "<testcase" "$junit_xml"' in shard_text
    assert 'exit "$rc"' in shard_text


def test_release_canary_aggregation_fails_closed_and_preserves_summary_contract():
    workflow = _workflow()
    result_job = workflow["jobs"]["release-canary-result"]
    assert result_job["if"] == "always()"
    assert set(result_job["needs"]) == {
        "collect-credential-free-non-fast",
        "credential-free-non-fast",
        "ruleset-drift",
        "pypi-latest-installability",
    }
    result_text = _run_text("release-canary-result")
    for field in (
        '"checked_ref": "develop"',
        '"commit_sha": "${CHECKED_SHA}"',
        '"non_fast_result": "${NON_FAST_RESULT}"',
        '"ruleset_drift_result": "${RULESET_DRIFT_RESULT}"',
        '"pypi_latest_installability_result": "${PYPI_LATEST_RESULT}"',
        '"non_fast_collected_count": "${NON_FAST_COUNT}"',
    ):
        assert field in result_text
    for result_name in ("COLLECTION_RESULT", "NON_FAST_RESULT", "RULESET_DRIFT_RESULT", "PYPI_LATEST_RESULT"):
        assert f"${result_name}" in result_text
    assert 'if [ "$result" != "success" ]' in result_text
    assert "Release canary passed." in result_text
