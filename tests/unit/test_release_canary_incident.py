from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.release_canary_incident import (
    INCIDENT_TITLE,
    collect_failures,
    is_truncated,
    main,
    parse_failures,
    parse_job_results,
    render,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]

GREEN_JOBS = {
    "collect-credential-free-non-fast": "success",
    "credential-free-non-fast": "success",
    "ruleset-drift": "success",
    "pypi-latest-installability": "success",
    "release-canary-result": "success",
}

SHARD_LOG = """\
tests/integration/test_a.py::test_one PASSED
FAILED tests/integration/test_a.py::test_two - AssertionError: ETL failed
ERROR tests/unit/test_b.py::TestB::test_setup - FileNotFoundError: missing
FAILED tests/integration/test_a.py::test_two - AssertionError: ETL failed
!!!!!!!!!!!!!!!!!!!!!!!!!! stopping after 5 failures !!!!!!!!!!!!!!!!!!!!!!!!!!!
"""


def _render(job_results: dict[str, str], failures: list[str] | None = None, **kwargs: object) -> dict:
    defaults = {
        "failures": failures or [],
        "truncated_shards": [],
        "run_url": "https://github.com/o/r/actions/runs/1",
        "checked_sha": "b" * 40,
        "last_green_sha": "a" * 40,
        "repo_url": "https://github.com/o/r",
    }
    defaults.update(kwargs)
    return render(job_results=job_results, **defaults)


def test_parse_failures_keeps_unique_failed_and_errored_node_ids() -> None:
    assert parse_failures(SHARD_LOG) == [
        "tests/integration/test_a.py::test_two",
        "tests/unit/test_b.py::TestB::test_setup",
    ]


def test_is_truncated_detects_maxfail_stop() -> None:
    assert is_truncated(SHARD_LOG)
    assert not is_truncated("FAILED tests/x.py::t - boom\n")


def test_collect_failures_reads_downloaded_shard_directories(tmp_path: Path) -> None:
    shard = tmp_path / "release-canary-non-fast-shard-3"
    shard.mkdir()
    (shard / "shard-3-pytest.log").write_text(SHARD_LOG, encoding="utf-8")
    other = tmp_path / "release-canary-non-fast-shard-4"
    other.mkdir()
    (other / "shard-4-pytest.log").write_text("FAILED tests/c.py::t - x\n", encoding="utf-8")

    failures, truncated = collect_failures(tmp_path)

    assert failures == [
        "tests/c.py::t",
        "tests/integration/test_a.py::test_two",
        "tests/unit/test_b.py::TestB::test_setup",
    ]
    assert truncated == [3]


def test_parse_job_results_rejects_malformed_pairs() -> None:
    assert parse_job_results(["a=success", "b="]) == {"a": "success", "b": "unknown"}
    with pytest.raises(ValueError):
        parse_job_results(["no-separator"])
    with pytest.raises(ValueError):
        parse_job_results([])


def test_green_run_closes_without_body() -> None:
    update = _render(GREEN_JOBS)
    assert update["state"] == "green"
    assert update["body"] == ""
    assert "green again" in update["comment"]


@pytest.mark.parametrize("result", ["failure", "cancelled", "skipped", "unknown"])
def test_any_non_success_job_is_red(result: str) -> None:
    update = _render({**GREEN_JOBS, "ruleset-drift": result})
    assert update["state"] == "red"
    assert update["failed_jobs"] == ["ruleset-drift"]


def test_aggregate_failure_is_red_when_component_jobs_succeed() -> None:
    update = _render({**GREEN_JOBS, "release-canary-result": "failure"})
    assert update["state"] == "red"
    assert update["failed_jobs"] == ["release-canary-result"]


def test_red_run_names_failures_changes_and_owner_action() -> None:
    update = _render(
        {**GREEN_JOBS, "credential-free-non-fast": "failure"},
        failures=["tests/integration/test_a.py::test_two"],
        truncated_shards=[3],
    )
    assert update["title"] == INCIDENT_TITLE
    assert "`tests/integration/test_a.py::test_two`" in update["body"]
    assert f"https://github.com/o/r/compare/{'a' * 40}...{'b' * 40}" in update["body"]
    assert "does not block a tag by itself" in update["body"]
    assert "Shards 3 stopped at `--maxfail`" in update["body"]
    assert "`tests/integration/test_a.py::test_two`" in update["comment"]


def test_red_run_without_a_prior_green_run_says_so() -> None:
    update = _render({**GREEN_JOBS, "credential-free-non-fast": "failure"}, last_green_sha="")
    assert "no green run found" in update["body"]


def test_red_run_with_unrecorded_checked_sha_has_no_broken_compare_link() -> None:
    update = _render({**GREEN_JOBS, "collect-credential-free-non-fast": "failure"}, checked_sha="")
    assert "/compare/" not in update["body"]
    assert "checked SHA was not recorded" in update["body"]


def test_main_writes_json_even_when_artifacts_are_missing(tmp_path: Path) -> None:
    output = tmp_path / "incident.json"
    rc = main(
        [
            "--artifacts-dir",
            str(tmp_path / "missing"),
            "--job-result",
            "credential-free-non-fast=failure",
            "--run-url",
            "https://github.com/o/r/actions/runs/1",
            "--repo-url",
            "https://github.com/o/r/",
            "--output",
            str(output),
        ]
    )
    assert rc == 0
    update = json.loads(output.read_text(encoding="utf-8"))
    assert update["state"] == "red"
    assert "none parsed from the shard logs" in update["body"]


def test_main_fails_closed_on_bad_job_result(tmp_path: Path) -> None:
    rc = main(
        [
            "--artifacts-dir",
            str(tmp_path),
            "--job-result",
            "bogus",
            "--run-url",
            "u",
            "--repo-url",
            "r",
            "--output",
            str(tmp_path / "o.json"),
        ]
    )
    assert rc == 1
