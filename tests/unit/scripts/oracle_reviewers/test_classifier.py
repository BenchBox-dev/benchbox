from __future__ import annotations

import pytest

from _project.scripts.oracle_reviewers.classifier import ChangedFile, classify
from _project.scripts.oracle_reviewers.policy import Policy
from _project.scripts.soundness_paths import is_soundness_path

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _file(path: str, additions: int = 5, deletions: int = 0, previous: str | None = None) -> ChangedFile:
    return ChangedFile(path, previous, additions, deletions)


def _classify(policy: Policy, *files: ChangedFile, labels: tuple[str, ...] = ()):
    return classify(list(files), labels, policy, is_soundness_path)


def test_non_soundness_change_is_out_of_scope(policy: Policy) -> None:
    result = _classify(policy, _file("README.md"), _file("docs/index.md"))
    assert result.soundness is False
    assert result.tier is None


def test_answer_files_are_low_medium_whatever_their_size(policy: Policy) -> None:
    result = _classify(
        policy,
        _file("_sources/tpc-h/dbgen/answers/q1.out", 5000, 5000),
        _file("_sources/tpc-ds/answer_sets/q2.ans", 3000),
    )
    assert result.soundness is True
    assert result.tier == "low-medium"


def test_small_single_area_code_change_is_low_medium(policy: Policy) -> None:
    result = _classify(policy, _file("scripts/publication/validator_parity.py", 20, 3))
    assert result.tier == "low-medium"


@pytest.mark.parametrize(
    "path",
    [
        "benchbox/core/equivalence/checker.py",
        "benchbox/core/validation/engines.py",
        "benchbox/platforms/base/result_capture.py",
        "benchbox/core/results/result_digest.py",
    ],
)
def test_comparison_validation_and_capture_logic_is_medium_high(policy: Policy, path: str) -> None:
    assert _classify(policy, _file(path, 2)).tier == "medium-high"


def test_larger_or_multi_area_code_diff_is_medium_high(policy: Policy) -> None:
    assert _classify(policy, _file("scripts/publication/validator_parity.py", 200)).tier == "medium-high"
    assert (
        _classify(
            policy, _file("scripts/publication/validator_parity.py"), _file("benchbox/core/equivalence/x.py")
        ).tier
        == "medium-high"
    )


@pytest.mark.parametrize(
    "path",
    [
        "_project/scripts/oracle_review_check.py",
        ".github/workflows/oracle-review.yml",
        ".github/workflows/oracle-verdict.yml",
        ".github/soundness-paths.txt",
        "_project/scripts/soundness_paths.py",
        "_project/scripts/ruleset_review_enforcement.py",
    ],
)
def test_gate_changes_are_very_high(policy: Policy, path: str) -> None:
    assert _classify(policy, _file(path, 1)).tier == "very-high"


def test_every_gate_path_is_a_soundness_path_in_the_manifest(policy: Policy) -> None:
    samples = [pattern.replace("*", "selection.py") for pattern in policy.classifier.gate_paths]
    assert ".github/oracle-reviewers.yml" in samples and "_project/scripts/oracle_reviewers/selection.py" in samples
    assert [path for path in samples if not is_soundness_path(path)] == []


def test_gate_files_missing_from_the_manifest_are_still_in_scope(policy: Policy) -> None:
    for path in (".github/oracle-reviewers.yml", "_project/scripts/oracle_reviewers/selection.py"):
        result = classify([_file(path, 1)], (), policy, lambda _path: False)
        assert result.soundness is True
        assert result.tier == "very-high"


def test_renaming_a_gate_file_away_is_very_high(policy: Policy) -> None:
    result = _classify(policy, _file("scripts/elsewhere.py", 0, 0, previous="_project/scripts/soundness_paths.py"))
    assert result.tier == "very-high"


def test_large_multi_area_diff_is_very_high(policy: Policy) -> None:
    result = _classify(
        policy,
        _file("scripts/publication/validator_parity.py", 400),
        _file("benchbox/core/equivalence/b.py", 300),
        _file("publication/ledger-seed.json", 200),
    )
    assert result.tier == "very-high"


def test_label_raises_the_tier(policy: Policy) -> None:
    result = _classify(policy, _file("_sources/tpc-h/dbgen/answers/q1.out"), labels=("oracle-tier:very-high",))
    assert result.computed_tier == "low-medium"
    assert result.tier == "very-high"


def test_label_never_lowers_the_tier(policy: Policy) -> None:
    result = _classify(policy, _file(".github/soundness-paths.txt"), labels=("oracle-tier:low-medium",))
    assert result.tier == "very-high"
    result = _classify(
        policy, _file("benchbox/core/equivalence/checker.py"), labels=("oracle-tier:low-medium", "other")
    )
    assert result.tier == "medium-high"


def test_unrelated_labels_are_ignored(policy: Policy) -> None:
    result = _classify(policy, _file("_sources/tpc-h/dbgen/answers/q1.out"), labels=("tier:very-high",))
    assert result.tier == "low-medium"
