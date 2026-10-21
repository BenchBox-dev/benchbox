from __future__ import annotations

import pytest

from _project.scripts.oracle_reviewers.classifier import ChangedFile, SoundnessScope, matching, soundness_scope
from _project.scripts.oracle_reviewers.policy import ClassifierRules, Policy

pytestmark = [pytest.mark.unit, pytest.mark.fast]

RULES = ClassifierRules(
    gate_paths=("gate/*", ".github/oracle-reviewers.yml", "_project/scripts/oracle_reviewers/*"),
    logic_paths=("logic/*", "*validation*"),
    data_paths=("data/*",),
    exempt_paths=("docs/*", "*.md"),
)


def _file(path: str, previous: str | None = None) -> ChangedFile:
    return ChangedFile(path, previous, 5, 0)


def _scope(*files: ChangedFile, pred: bool = True) -> SoundnessScope:
    return soundness_scope(list(files), RULES, lambda path: pred)


def test_docs_only_and_markdown_only_changes_are_out_of_scope() -> None:
    result = _scope(_file("docs/index.md"), _file("README.md"), pred=False)
    assert result.soundness is False
    assert result.reasons == ("only exempt paths changed",)
    assert result.paths == ()
    assert result.gate_paths == ()
    assert result.logic_paths == ()


def test_empty_file_list_is_out_of_scope() -> None:
    result = soundness_scope([], RULES, lambda path: True)
    assert result.soundness is False
    assert result.reasons == ("only exempt paths changed",)
    assert result.paths == ()


def test_code_outside_the_soundness_list_requires_review() -> None:
    result = _scope(_file("src/app.py"), pred=False)
    assert result.soundness is True
    assert result.gate_paths == ()
    assert result.logic_paths == ()
    assert result.reasons == ("changes 1 files outside the exempt paths",)
    assert result.paths == ("src/app.py",)


def test_soundness_list_hits_are_noted_in_the_fallback_reason() -> None:
    result = _scope(_file("src/app.py"))
    assert result.soundness is True
    assert result.reasons == (
        "changes 1 files outside the exempt paths",
        "touches soundness paths: src/app.py",
    )


def test_gate_template_matching_the_markdown_exemption_still_needs_review() -> None:
    result = _scope(_file("_project/scripts/oracle_reviewers/templates/gate.md"), pred=False)
    assert result.soundness is True
    assert result.gate_paths == ("_project/scripts/oracle_reviewers/templates/gate.md",)
    assert result.reasons == ("changes the gate: _project/scripts/oracle_reviewers/templates/gate.md",)


def test_soundness_manifest_beats_the_markdown_exemption() -> None:
    path = ".github/PULL_REQUEST_TEMPLATE.md"
    result = soundness_scope([_file(path)], RULES, lambda candidate: candidate == path)
    assert result.soundness is True
    assert result.gate_paths == ()
    assert result.reasons == (
        "changes 1 files outside the exempt paths",
        f"touches soundness paths: {path}",
    )


def test_exempt_docs_stay_out_of_scope() -> None:
    result = soundness_scope([_file("docs/foo.md")], RULES, lambda candidate: candidate == "other")
    assert result.soundness is False
    assert result.reasons == ("only exempt paths changed",)


def test_gate_matches_are_recorded_with_reasons() -> None:
    result = _scope(_file("gate/policy.py"))
    assert result.soundness is True
    assert result.gate_paths == ("gate/policy.py",)
    assert result.reasons == ("changes the gate: gate/policy.py",)
    assert result.paths == ("gate/policy.py",)


def test_logic_matches_are_recorded_with_reasons() -> None:
    result = _scope(_file("logic/equivalence.py"), _file("scripts/validation.py"))
    assert result.soundness is True
    assert result.logic_paths == ("logic/equivalence.py", "scripts/validation.py")
    assert result.reasons == (
        "changes comparison, validation or result logic: logic/equivalence.py, scripts/validation.py",
    )


def test_gate_and_logic_reasons_combine() -> None:
    result = _scope(_file("gate/policy.py"), _file("logic/equivalence.py"))
    assert result.reasons == (
        "changes the gate: gate/policy.py",
        "changes comparison, validation or result logic: logic/equivalence.py",
    )


def test_data_only_files_in_scope_use_the_fallback_reason() -> None:
    result = _scope(_file("data/q1.out"), _file("data/q2.out"))
    assert result.soundness is True
    assert result.gate_paths == ()
    assert result.logic_paths == ()
    assert result.reasons == (
        "changes 2 files outside the exempt paths",
        "touches soundness paths: data/q1.out, data/q2.out",
    )
    assert result.paths == ("data/q1.out", "data/q2.out")


def test_previous_path_matches_gate() -> None:
    renamed_gate = _scope(_file("elsewhere.py", previous="gate/policy.py"))
    assert renamed_gate.soundness is True
    assert renamed_gate.gate_paths == ("gate/policy.py",)
    assert renamed_gate.paths == ("elsewhere.py", "gate/policy.py")


def test_rename_from_docs_into_code_requires_review() -> None:
    result = _scope(_file("benchbox/new.py", previous="docs/old.md"), pred=False)
    assert result.soundness is True
    assert result.paths == ("benchbox/new.py", "docs/old.md")
    assert result.reasons == ("changes 1 files outside the exempt paths",)


def test_rename_within_exempt_paths_stays_out_of_scope() -> None:
    result = _scope(_file("docs/new.md", previous="docs/old.md"), pred=False)
    assert result.soundness is False
    assert result.reasons == ("only exempt paths changed",)
    assert result.paths == ()


def test_live_policy_data_answer_uses_the_fallback_reason(policy: Policy) -> None:
    from _project.scripts.soundness_paths import is_soundness_path

    path = "_sources/tpc-h/dbgen/answers/q1.out"
    assert is_soundness_path(path) is True
    result = soundness_scope([_file(path)], policy.classifier, is_soundness_path)
    assert result.soundness is True
    assert result.gate_paths == ()
    assert result.logic_paths == ()
    assert result.reasons == (
        "changes 1 files outside the exempt paths",
        f"touches soundness paths: {path}",
    )


def test_to_json_from_json_round_trip() -> None:
    result = _scope(_file("gate/policy.py"), _file("logic/equivalence.py"))
    assert SoundnessScope.from_json(result.to_json()) == result


def test_from_json_defaults_missing_collections() -> None:
    result = SoundnessScope.from_json({"soundness": False})
    assert result == SoundnessScope(False, (), (), (), ())


def test_matching_returns_sorted_hits() -> None:
    assert matching(["logic/b.py", "docs/a.md", "logic/a.py"], RULES.logic_paths) == [
        "logic/a.py",
        "logic/b.py",
    ]
