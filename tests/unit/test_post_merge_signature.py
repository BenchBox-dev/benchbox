from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = str(Path(__file__).resolve().parents[2] / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from post_merge_signature import (
    SignatureError,
    attribution_action,
    build_incident_artifact,
    build_signature_from_job_failure,
    build_signature_from_junit,
    classify_attribution,
    diff_signatures,
    failure_id_test_paths,
    imported_module_paths,
    incident_key,
    load_signature,
    main,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


JUNIT_WITH_FAILURES = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" tests="4" failures="1" errors="1" skipped="1">
    <testcase classname="tests.unit.test_foo" name="test_pass" file="tests/unit/test_foo.py" line="3" time="0.01" />
    <testcase classname="tests.unit.test_foo" name="test_fail" file="tests/unit/test_foo.py" line="8" time="0.01">
      <failure message="assert 1 == 2">AssertionError</failure>
    </testcase>
    <testcase classname="tests.unit.test_foo" name="test_error" file="tests/unit/test_foo.py" line="14" time="0.01">
      <error message="boom">RuntimeError</error>
    </testcase>
    <testcase classname="tests.unit.test_foo" name="test_skip" file="tests/unit/test_foo.py" line="20" time="0.0">
      <skipped message="not today" />
    </testcase>
  </testsuite>
</testsuites>
"""

JUNIT_ALL_PASS = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" tests="1" failures="0" errors="0" skipped="0">
    <testcase classname="tests.unit.test_foo" name="test_pass" file="tests/unit/test_foo.py" line="3" time="0.01" />
  </testsuite>
</testsuites>
"""

JUNIT_NO_FILE_ATTR = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" tests="1" failures="1" errors="0" skipped="0">
    <testcase classname="tests.unit.test_bar" name="test_fail_no_file" time="0.01">
      <failure message="boom">AssertionError</failure>
    </testcase>
  </testsuite>
</testsuites>
"""

BIGQUERY_2068_DOTTED_JUNIT_FAILURE_IDS = [
    "tests.unit.platforms.credentials.test_bigquery_defaults.TestBigQueryCredentialDefaults::test_partial_existing_credentials"
]
LEDGER_2073_EXTERNAL_REF_EVIDENCE = {
    "failure_ids": ["tests/unit/test_ledger.py::test_seed"],
    "source_inputs": {"published-results": "source-new"},
    "predecessor_source_inputs": {"published-results": "source-old"},
    "ownership_match": False,
}


def test_build_signature_from_junit_extracts_failures_and_errors(tmp_path: Path) -> None:
    junit_path = tmp_path / "junit.xml"
    junit_path.write_text(JUNIT_WITH_FAILURES, encoding="utf-8")

    signature = build_signature_from_junit("fast-test", junit_path)

    assert signature["job"] == "fast-test"
    assert signature["kind"] == "junit"
    assert signature["failure_ids"] == sorted(
        [
            "tests/unit/test_foo.py::test_fail",
            "tests/unit/test_foo.py::test_error",
        ]
    )
    assert not any("test_pass" in fid or "test_skip" in fid for fid in signature["failure_ids"])


def test_build_signature_from_junit_no_failures(tmp_path: Path) -> None:
    junit_path = tmp_path / "junit.xml"
    junit_path.write_text(JUNIT_ALL_PASS, encoding="utf-8")

    signature = build_signature_from_junit("fast-test", junit_path)

    assert signature["kind"] == "junit"
    assert signature["failure_ids"] == []


def test_build_signature_from_junit_falls_back_to_classname_without_file(tmp_path: Path) -> None:
    junit_path = tmp_path / "junit.xml"
    junit_path.write_text(JUNIT_NO_FILE_ATTR, encoding="utf-8")

    signature = build_signature_from_junit("medium-test", junit_path)

    assert signature["failure_ids"] == ["tests.unit.test_bar::test_fail_no_file"]


def test_build_signature_from_junit_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(SignatureError, match="not found"):
        build_signature_from_junit("fast-test", tmp_path / "does-not-exist.xml")


def test_build_signature_from_junit_malformed_xml_raises(tmp_path: Path) -> None:
    junit_path = tmp_path / "junit.xml"
    junit_path.write_text("<testsuites><testsuite>not closed", encoding="utf-8")

    with pytest.raises(SignatureError, match="could not parse"):
        build_signature_from_junit("fast-test", junit_path)


def test_build_signature_from_job_failure_with_step() -> None:
    signature = build_signature_from_job_failure("lint", "Run CI lint mirror")

    assert signature == {
        "job": "lint",
        "kind": "job-failure",
        "failure_ids": ["lint:Run CI lint mirror"],
    }


def test_build_signature_from_job_failure_without_step_is_empty() -> None:
    signature = build_signature_from_job_failure("lint", None)

    assert signature == {"job": "lint", "kind": "none", "failure_ids": []}


def test_diff_signatures_returns_only_new_ids() -> None:
    previous = {"failure_ids": ["tests/a.py::test_a"]}
    current = {"failure_ids": ["tests/a.py::test_a", "tests/b.py::test_b"]}

    assert diff_signatures(previous, current) == ["tests/b.py::test_b"]


def test_diff_signatures_no_new_failures_returns_empty() -> None:
    previous = {"failure_ids": ["tests/a.py::test_a", "tests/b.py::test_b"]}
    current = {"failure_ids": ["tests/a.py::test_a"]}

    assert diff_signatures(previous, current) == []


def test_diff_signatures_empty_previous_all_current_are_new() -> None:
    previous = {"failure_ids": []}
    current = {"failure_ids": ["tests/a.py::test_a", "tests/b.py::test_b"]}

    assert diff_signatures(previous, current) == sorted(["tests/a.py::test_a", "tests/b.py::test_b"])


def test_diff_signatures_missing_previous_key_treated_as_empty() -> None:
    previous: dict[str, object] = {"job": "lint", "kind": "none"}
    current = {"failure_ids": ["lint:Run CI lint mirror"]}

    assert diff_signatures(previous, current) == ["lint:Run CI lint mirror"]


def test_diff_signatures_identical_signature_is_not_new() -> None:
    signature = {"failure_ids": ["tests/a.py::test_a"]}

    assert diff_signatures(signature, signature) == []


def test_load_signature_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(SignatureError, match="not found"):
        load_signature(tmp_path / "missing.json")


def test_load_signature_malformed_json_raises(tmp_path: Path) -> None:
    bad_path = tmp_path / "bad.json"
    bad_path.write_text("{not valid json", encoding="utf-8")

    with pytest.raises(SignatureError, match="not valid json"):
        load_signature(bad_path)


def test_load_signature_missing_failure_ids_key_raises(tmp_path: Path) -> None:
    bad_path = tmp_path / "bad.json"
    bad_path.write_text(json.dumps({"job": "lint"}), encoding="utf-8")

    with pytest.raises(SignatureError, match="failure_ids"):
        load_signature(bad_path)


def test_load_signature_non_object_json_raises(tmp_path: Path) -> None:
    bad_path = tmp_path / "bad.json"
    bad_path.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")

    with pytest.raises(SignatureError, match="failure_ids"):
        load_signature(bad_path)


def test_load_signature_round_trips_valid_file(tmp_path: Path) -> None:
    path = tmp_path / "sig.json"
    path.write_text(json.dumps({"job": "lint", "kind": "none", "failure_ids": []}), encoding="utf-8")

    signature = load_signature(path)

    assert signature["job"] == "lint"
    assert signature["failure_ids"] == []


def test_cli_build_from_junit_writes_signature_file(tmp_path: Path) -> None:
    junit_path = tmp_path / "junit.xml"
    junit_path.write_text(JUNIT_WITH_FAILURES, encoding="utf-8")
    out_path = tmp_path / "signature.json"

    exit_code = main(["build", "--job", "fast-test", "--junit", str(junit_path), "--out", str(out_path)])

    assert exit_code == 0
    written = json.loads(out_path.read_text(encoding="utf-8"))
    assert written["job"] == "fast-test"
    assert len(written["failure_ids"]) == 2


def test_cli_build_job_failed_with_clean_junit_falls_back_to_job_failure(tmp_path: Path) -> None:
    junit_path = tmp_path / "junit.xml"
    junit_path.write_text(JUNIT_ALL_PASS, encoding="utf-8")
    out_path = tmp_path / "signature.json"

    exit_code = main(
        [
            "build",
            "--job",
            "fast-test",
            "--junit",
            str(junit_path),
            "--failed-step",
            "coverage threshold not met",
            "--job-failed",
            "--out",
            str(out_path),
        ]
    )

    assert exit_code == 0
    written = json.loads(out_path.read_text(encoding="utf-8"))
    assert written["kind"] == "job-failure"
    assert written["failure_ids"] == ["fast-test:coverage threshold not met"]


def test_cli_build_job_failed_with_real_junit_failures_keeps_junit_signature(tmp_path: Path) -> None:
    junit_path = tmp_path / "junit.xml"
    junit_path.write_text(JUNIT_WITH_FAILURES, encoding="utf-8")
    out_path = tmp_path / "signature.json"

    exit_code = main(
        [
            "build",
            "--job",
            "fast-test",
            "--junit",
            str(junit_path),
            "--failed-step",
            "should not be used",
            "--job-failed",
            "--out",
            str(out_path),
        ]
    )

    assert exit_code == 0
    written = json.loads(out_path.read_text(encoding="utf-8"))
    assert written["kind"] == "junit"
    assert len(written["failure_ids"]) == 2


def test_cli_build_without_job_failed_keeps_clean_junit_signature(tmp_path: Path) -> None:
    junit_path = tmp_path / "junit.xml"
    junit_path.write_text(JUNIT_ALL_PASS, encoding="utf-8")
    out_path = tmp_path / "signature.json"

    exit_code = main(["build", "--job", "fast-test", "--junit", str(junit_path), "--out", str(out_path)])

    assert exit_code == 0
    written = json.loads(out_path.read_text(encoding="utf-8"))
    assert written["kind"] == "junit"
    assert written["failure_ids"] == []


def test_cli_build_from_job_failure_writes_signature_file(tmp_path: Path) -> None:
    out_path = tmp_path / "signature.json"

    exit_code = main(
        [
            "build",
            "--job",
            "lint",
            "--failed-step",
            "Run CI lint mirror",
            "--out",
            str(out_path),
        ]
    )

    assert exit_code == 0
    written = json.loads(out_path.read_text(encoding="utf-8"))
    assert written["failure_ids"] == ["lint:Run CI lint mirror"]


def test_cli_build_without_junit_or_failed_step_is_empty_signature(tmp_path: Path) -> None:
    out_path = tmp_path / "signature.json"

    exit_code = main(["build", "--job", "lint", "--out", str(out_path)])

    assert exit_code == 0
    written = json.loads(out_path.read_text(encoding="utf-8"))
    assert written["kind"] == "none"
    assert written["failure_ids"] == []


def test_cli_build_from_missing_junit_returns_error_exit_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out_path = tmp_path / "signature.json"

    exit_code = main(
        [
            "build",
            "--job",
            "fast-test",
            "--junit",
            str(tmp_path / "missing.xml"),
            "--out",
            str(out_path),
        ]
    )

    assert exit_code == 1
    assert not out_path.exists()
    captured = capsys.readouterr()
    assert "error:" in captured.err


def test_cli_build_from_malformed_junit_returns_error_exit_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    junit_path = tmp_path / "junit.xml"
    junit_path.write_text("<testsuites><testsuite>truncated mid-write", encoding="utf-8")
    out_path = tmp_path / "signature.json"

    exit_code = main(
        [
            "build",
            "--job",
            "fast-test",
            "--junit",
            str(junit_path),
            "--out",
            str(out_path),
        ]
    )

    assert exit_code == 1
    assert not out_path.exists()
    captured = capsys.readouterr()
    assert "error:" in captured.err


def test_cli_diff_writes_new_failure_ids(tmp_path: Path) -> None:
    previous_path = tmp_path / "previous.json"
    current_path = tmp_path / "current.json"
    out_path = tmp_path / "diff.json"
    previous_path.write_text(json.dumps({"failure_ids": ["tests/a.py::test_a"]}), encoding="utf-8")
    current_path.write_text(json.dumps({"failure_ids": ["tests/a.py::test_a", "tests/b.py::test_b"]}), encoding="utf-8")

    exit_code = main(
        [
            "diff",
            "--previous",
            str(previous_path),
            "--current",
            str(current_path),
            "--out",
            str(out_path),
        ]
    )

    assert exit_code == 0
    written = json.loads(out_path.read_text(encoding="utf-8"))
    assert written["new_failure_ids"] == ["tests/b.py::test_b"]


def test_cli_diff_missing_previous_file_errors_out(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    current_path = tmp_path / "current.json"
    current_path.write_text(json.dumps({"failure_ids": []}), encoding="utf-8")

    exit_code = main(
        [
            "diff",
            "--previous",
            str(tmp_path / "missing.json"),
            "--current",
            str(current_path),
        ]
    )

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "error:" in captured.err


def test_cli_diff_prints_new_ids_to_stdout(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    previous_path = tmp_path / "previous.json"
    current_path = tmp_path / "current.json"
    previous_path.write_text(json.dumps({"failure_ids": []}), encoding="utf-8")
    current_path.write_text(json.dumps({"failure_ids": ["tests/a.py::test_a"]}), encoding="utf-8")

    exit_code = main(["diff", "--previous", str(previous_path), "--current", str(current_path)])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == "tests/a.py::test_a"


def test_failure_id_test_paths_extracts_junit_paths_only() -> None:
    paths = failure_id_test_paths(
        [
            "tests/unit/test_query_generation_preflight.py::test_tpcds_throughput_preflight_detects_generation_failures",
            "tests.unit.platforms.credentials.test_bigquery_defaults.TestBigQueryCredentialDefaults::test_partial_existing_credentials",
            "lint:Run CI lint mirror",
            12,
        ]
    )
    assert paths == [
        "tests/unit/test_query_generation_preflight.py",
        "tests/unit/platforms/credentials/test_bigquery_defaults.py",
    ]


def test_attribution_advisory_for_unrelated_dotted_junit_classname() -> None:
    failure_ids = [
        "tests.unit.platforms.credentials.test_bigquery_defaults.TestBigQueryCredentialDefaults::test_partial_existing_credentials"
    ]
    changed = ["results-data/README.md", ".github/workflows/seed-corpus.yml"]
    assert attribution_action(failure_ids, changed) == "advisory"


def test_attribution_advisory_when_blamed_sha_misses_failing_test() -> None:
    failure_ids = [
        "tests/unit/test_query_generation_preflight.py::test_tpcds_throughput_preflight_detects_generation_failures"
    ]
    changed = ["benchbox/platforms/ducklake.py", "docs/operations/local-platform-provisioning.tsv"]
    assert attribution_action(failure_ids, changed) == "advisory"


def test_attribution_reverts_when_blamed_sha_touches_code_under_test() -> None:
    failure_ids = [
        "tests/unit/test_post_merge_signature.py::test_build_signature_from_junit_extracts_failures_and_errors"
    ]
    changed = ["scripts/post_merge_signature.py"]
    assert attribution_action(failure_ids, changed) == "revert"


def test_attribution_keeps_unmappable_job_failures_as_advisory() -> None:
    assert attribution_action(["lint:Run CI lint mirror"], ["README.md"]) == "advisory"


def _complete_attribution_evidence(**overrides: object) -> dict[str, object]:
    evidence: dict[str, object] = {
        "failing_sha": "failing-sha",
        "current_target_sha": "failing-sha",
        "failure_ids": ["tests/unit/test_ledger.py::test_seed"],
        "predecessor_evidence": {"run_url": "https://example.test/runs/previous", "sha": "parent-sha"},
        "source_inputs": {"published-results": "source-new"},
        "predecessor_source_inputs": {"published-results": "source-old"},
        "ownership_match": False,
        "owner": "maintainer",
    }
    evidence.update(overrides)
    return evidence


def test_external_ref_drift_does_not_propose_unrelated_revert() -> None:
    result = classify_attribution(_complete_attribution_evidence())

    assert result["classification"] == "external-ref-drift"
    assert result["action"] == "advisory"
    assert "reconcile" in str(result["next_action"]).lower()


def test_missing_predecessor_source_identity_is_unknown_not_external_drift() -> None:
    result = classify_attribution(_complete_attribution_evidence(predecessor_source_inputs={}))

    assert result["classification"] == "unknown"
    assert result["action"] == "advisory"


def test_owned_same_subsystem_regression_still_proposes_revert() -> None:
    result = classify_attribution(_complete_attribution_evidence(ownership_match=True))

    assert result["classification"] == "code-regression"
    assert result["action"] == "revert"


def test_missing_evidence_is_unknown_and_keeps_develop_red() -> None:
    result = classify_attribution({"failure_ids": ["lint:Run CI lint mirror"], "ownership_match": True})

    assert result["classification"] == "unknown"
    assert result["action"] == "advisory"
    assert "keep develop red" in result["next_action"]


def test_exact_commit_rerun_that_passes_is_transient() -> None:
    result = classify_attribution(
        _complete_attribution_evidence(rerun={"conclusion": "success", "run_url": "https://example.test/rerun"})
    )

    assert result["classification"] == "environment/transient-failure"
    assert result["action"] == "advisory"


def test_target_moved_since_failure_is_stale_not_a_revert() -> None:
    result = classify_attribution(_complete_attribution_evidence(current_target_sha="later-sha", ownership_match=True))

    assert result["classification"] == "stale-run"
    assert result["action"] == "advisory"


def test_revert_that_introduces_another_failure_is_not_approved() -> None:
    result = classify_attribution(
        _complete_attribution_evidence(ownership_match=True, proposed_revert={"introduced_failure": True})
    )

    assert result["classification"] == "unknown"
    assert result["action"] == "advisory"


def test_incident_artifact_captures_immutable_and_ownership_evidence() -> None:
    evidence = _complete_attribution_evidence(
        run_url="https://example.test/runs/current",
        failing_pr={"number": 2073, "url": "https://example.test/pr/2073"},
        jobs=[{"job": "fast-test", "failure_ids": ["tests/unit/test_ledger.py::test_seed"]}],
    )
    attribution = classify_attribution(evidence)
    artifact = build_incident_artifact(evidence, attribution)

    assert artifact["failing_commit"] == {"sha": "failing-sha", "run_url": "https://example.test/runs/current"}
    assert artifact["failing_pr"]["number"] == 2073
    assert artifact["jobs"][0]["job"] == "fast-test"
    assert artifact["source_input_identities"] == {
        "current": {"published-results": "source-new"},
        "predecessor": {"published-results": "source-old"},
    }
    assert artifact["predecessor_evidence"]["sha"] == "parent-sha"
    assert artifact["owner"] == "maintainer"


def test_missing_current_signature_reaches_owned_incident_without_revert() -> None:
    evidence = _complete_attribution_evidence(
        failure_ids=[],
        source_inputs={},
        predecessor_source_inputs={"published-results": "source-previous"},
        comparison_state="unknown",
        comparison_failure="Missing current-run signature artifact(s) for: fast-test",
    )

    artifact = build_incident_artifact(evidence, classify_attribution(evidence))

    assert artifact["classification"] == "unknown"
    assert artifact["action"] == "advisory"
    assert artifact["owner"] == "maintainer"
    assert artifact["comparison_state"] == "unknown"
    assert "Missing current-run signature" in artifact["comparison_failure"]
    assert artifact["incident_key"] == incident_key([])


def test_replay_2068_bigquery_dotted_junit_incident_never_reverts() -> None:
    evidence = _complete_attribution_evidence(
        failure_ids=BIGQUERY_2068_DOTTED_JUNIT_FAILURE_IDS,
        changed_paths=["results-data/README.md", ".github/workflows/seed-corpus.yml"],
        source_inputs={"published-results": "source"},
        predecessor_source_inputs={"published-results": "source"},
    )

    result = classify_attribution(evidence)

    assert failure_id_test_paths(BIGQUERY_2068_DOTTED_JUNIT_FAILURE_IDS) == [
        "tests/unit/platforms/credentials/test_bigquery_defaults.py"
    ]
    assert (
        attribution_action(
            BIGQUERY_2068_DOTTED_JUNIT_FAILURE_IDS,
            ["results-data/README.md", ".github/workflows/seed-corpus.yml"],
        )
        == "advisory"
    )
    assert result["classification"] == "unknown"
    assert result["action"] == "advisory"
    assert result["incident_key"] == incident_key(evidence["failure_ids"])


def test_replay_2073_ledger_external_ref_incident_never_reverts() -> None:
    evidence = _complete_attribution_evidence(
        **LEDGER_2073_EXTERNAL_REF_EVIDENCE,
    )

    result = classify_attribution(evidence)

    assert result["classification"] == "external-ref-drift"
    assert result["action"] == "advisory"


def test_owned_regression_is_not_suppressed() -> None:
    result = classify_attribution(
        _complete_attribution_evidence(
            source_inputs={"published-results": "source"},
            predecessor_source_inputs={"published-results": "source"},
            ownership_match=True,
        )
    )

    assert result["classification"] == "code-regression"
    assert result["action"] == "revert"


def test_incident_key_is_stable_for_reordered_duplicate_failure_ids() -> None:
    first = classify_attribution(_complete_attribution_evidence(failure_ids=["b", "a", "a"]))
    second = classify_attribution(_complete_attribution_evidence(failure_ids=["a", "b"]))

    assert first["incident_key"] == second["incident_key"]


def test_incident_key_reuses_repeated_signature_and_separates_distinct_signature() -> None:
    first = incident_key(["tests/unit/test_ledger.py::test_seed"])
    repeated = incident_key(["tests/unit/test_ledger.py::test_seed"])
    distinct = incident_key(["tests/unit/test_ledger.py::test_other"])

    assert first == repeated
    assert first != distinct


def test_imported_module_paths_finds_lazy_function_local_imports(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "dep.py").write_text("VALUE = 1\n", encoding="utf-8")
    test_dir = tmp_path / "tests"
    test_dir.mkdir()
    test_file = test_dir / "test_thing.py"
    test_file.write_text(
        "def test_it():\n    from pkg.dep import VALUE\n    assert VALUE == 1\n",
        encoding="utf-8",
    )

    paths = imported_module_paths("tests/test_thing.py", tmp_path)

    assert "pkg/dep.py" in paths


def test_imported_module_paths_skips_nonexistent_modules(tmp_path: Path) -> None:
    test_dir = tmp_path / "tests"
    test_dir.mkdir()
    (test_dir / "test_thing.py").write_text("import os\nimport totally.missing.module\n", encoding="utf-8")

    paths = imported_module_paths("tests/test_thing.py", tmp_path)

    assert paths == []


def test_imported_module_paths_missing_file_returns_empty(tmp_path: Path) -> None:
    assert imported_module_paths("tests/does_not_exist.py", tmp_path) == []


def test_imported_module_paths_finds_same_package_import_without_module(tmp_path: Path) -> None:
    test_dir = tmp_path / "tests" / "foo"
    test_dir.mkdir(parents=True)
    (test_dir / "helpers.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    (test_dir / "test_thing.py").write_text(
        "from . import helpers\n\ndef test_it():\n    assert helpers.run() == 1\n",
        encoding="utf-8",
    )

    paths = imported_module_paths("tests/foo/test_thing.py", tmp_path)

    assert "tests/foo/helpers.py" in paths


def test_attribution_reverts_via_real_import_when_basename_differs(tmp_path: Path) -> None:
    (tmp_path / "benchbox").mkdir()
    (tmp_path / "benchbox" / "throughput_test.py").write_text("def run():\n    pass\n", encoding="utf-8")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_query_generation_preflight.py").write_text(
        "def test_it():\n    from benchbox.throughput_test import run\n    run()\n",
        encoding="utf-8",
    )

    failure_ids = ["tests/test_query_generation_preflight.py::test_it"]
    changed = ["benchbox/throughput_test.py"]

    assert attribution_action(failure_ids, changed, repo_root=tmp_path) == "revert"


def test_attribution_stays_advisory_when_import_analysis_also_clears_sha(tmp_path: Path) -> None:
    (tmp_path / "benchbox").mkdir()
    (tmp_path / "benchbox" / "unrelated.py").write_text("def run():\n    pass\n", encoding="utf-8")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_query_generation_preflight.py").write_text(
        "def test_it():\n    from benchbox.unrelated import run\n    run()\n",
        encoding="utf-8",
    )

    failure_ids = ["tests/test_query_generation_preflight.py::test_it"]
    changed = ["docs/operations/local-platform-provisioning.tsv"]

    assert attribution_action(failure_ids, changed, repo_root=tmp_path) == "advisory"


def test_imported_module_paths_keeps_the_anchor_package(tmp_path: Path) -> None:
    pkg = tmp_path / "tests" / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    (pkg / "test_it.py").write_text(
        "def test_it():\n    from . import VALUE\n    assert VALUE == 1\n",
        encoding="utf-8",
    )

    found = imported_module_paths("tests/pkg/test_it.py", tmp_path)
    assert "tests/pkg/__init__.py" in found


def test_attribution_reverts_when_the_package_initializer_changed(tmp_path: Path) -> None:
    pkg = tmp_path / "tests" / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    (pkg / "test_it.py").write_text(
        "def test_it():\n    from . import VALUE\n    assert VALUE == 1\n",
        encoding="utf-8",
    )

    failure_ids = ["tests/pkg/test_it.py::test_it"]
    assert attribution_action(failure_ids, ["tests/pkg/__init__.py"], repo_root=tmp_path) == "revert"
