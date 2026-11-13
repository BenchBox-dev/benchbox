from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

import pytest
from skill_sync_ci_policy import (
    EVIDENCE_PATH,
    TOOL_REF,
    TOOL_SHA256,
    PolicyError,
    compare_manifest_texts,
    main,
    normalize_ref_only_manifest,
    validate_conf_text,
    validate_evidence_binding,
    validate_receipt_text,
    validate_tool_bytes,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]
ROOT = Path(__file__).resolve().parents[3]
CONF = (ROOT / "skill-sync.conf").read_text(encoding="utf-8")
RECEIPT = (ROOT / ".claude" / "skills" / "skill-sync.receipt").read_text(encoding="utf-8")


def _replace_rev(text: str, old: str, new: str) -> str:
    assert old in text
    return text.replace(old, new, 1)


def test_current_conf_satisfies_shape_and_selection_policy() -> None:
    validate_conf_text(CONF)
    validate_receipt_text(RECEIPT)
    validate_tool_bytes((ROOT / "tools" / "skill-sync").read_bytes())
    assert TOOL_REF == "25e1e47693d8b0b2aba91d3ad3dbc0c14b8d3c4c"
    assert hashlib.sha256((ROOT / "tools" / "skill-sync").read_bytes()).hexdigest() == TOOL_SHA256


def test_tampered_wrapper_bytes_are_rejected() -> None:
    with pytest.raises(PolicyError, match="does not match the pinned revision"):
        validate_tool_bytes(b"#!/bin/sh\necho tampered\n")


def test_hostile_receipt_source_is_rejected() -> None:
    hostile = RECEIPT.replace(
        "https://github.com/joeharris76/skill-sync-skills.git",
        "https://example.invalid/hostile.git",
        1,
    )
    with pytest.raises(PolicyError, match="not approved"):
        validate_receipt_text(hostile)


def test_approved_immutable_rev_only_change_is_narrow_eligible() -> None:
    revs = re.findall(r"rev *= *([0-9a-f]{40})", CONF)
    assert len(revs) == 4
    head = _replace_rev(CONF, revs[0], "a" * 40)

    decision = compare_manifest_texts(CONF, head, base_ref="b" * 40)

    assert decision.narrow_eligible is True
    assert decision.reason == "approved_ref_only_change"
    assert normalize_ref_only_manifest(CONF) == normalize_ref_only_manifest(head)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("skill  = tidy-perms\n", ""),
        ("target = .agents/skills\n", ""),
        ("target = .agents/skills", "target = .agents/skills\ntarget = .extra/skills"),
        ("dir    = skills", "dir    = other"),
        ("source = ~/Developer/skill-sync-skills", "source = ~/Developer/fork-skills"),
    ],
)
def test_structural_trust_boundary_change_forces_full_ci(old: str, new: str) -> None:
    assert old in CONF
    decision = compare_manifest_texts(CONF, CONF.replace(old, new, 1))

    assert decision.narrow_eligible is False
    assert decision.reason.startswith(("manifest_policy_error:", "manifest_structural_change"))


def test_floating_conf_rev_is_rejected() -> None:
    revs = re.findall(r"rev *= *([0-9a-f]{40})", CONF)
    head = _replace_rev(CONF, revs[0], "main")

    with pytest.raises(PolicyError, match="40-character commit SHA"):
        validate_conf_text(head)


def test_missing_or_malformed_conf_fails_closed() -> None:
    decision = compare_manifest_texts("target = .claude/skills\n", CONF)

    assert decision.narrow_eligible is False
    assert decision.reason.startswith("manifest_policy_error:")


def test_comments_or_non_rev_config_edits_are_structural() -> None:
    head = CONF.replace("target = .claude/skills", "# changed policy prose\ntarget = .claude/skills", 1)

    decision = compare_manifest_texts(CONF, head)

    assert decision.narrow_eligible is False
    assert decision.reason == "manifest_structural_change"


def _evidence_copy(tmp_path: Path) -> tuple[Path, Path, dict]:
    tracked = tmp_path / "todo"
    shutil.copytree(ROOT / ".claude" / "skills" / "todo", tracked)
    evidence_path = tmp_path / "evidence.json"
    shutil.copy(ROOT / EVIDENCE_PATH, evidence_path)
    return tracked, evidence_path, json.loads(evidence_path.read_text(encoding="utf-8"))


def test_current_evidence_matches_the_pin_and_tracked_mirror() -> None:
    validate_evidence_binding(CONF, (ROOT / EVIDENCE_PATH).read_text(encoding="utf-8"), ROOT / ".claude/skills/todo")


def test_stale_pin_in_evidence_names_the_file_and_field(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tracked, evidence_path, evidence = _evidence_copy(tmp_path)
    evidence["benchbox_pin"]["after"] = "0" * 40
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    with pytest.raises(PolicyError, match=r"batch-rollout-evidence\.json: benchbox_pin\.after"):
        validate_evidence_binding(CONF, evidence_path.read_text(encoding="utf-8"), tracked)
    code = main(
        [
            "validate",
            "--manifest",
            str(ROOT / "skill-sync.conf"),
            "--receipt",
            str(ROOT / ".claude/skills/skill-sync.receipt"),
            "--tool",
            str(ROOT / "tools/skill-sync"),
            "--evidence",
            str(evidence_path),
            "--tracked-todo",
            str(tracked),
        ]
    )
    assert code == 1
    assert "batch-rollout-evidence.json" in capsys.readouterr().out


def test_wrong_mirror_hash_names_the_file(tmp_path: Path) -> None:
    tracked, evidence_path, evidence = _evidence_copy(tmp_path)
    evidence["mirrors"]["tracked_target"]["files"]["references/batch.md"] = "f" * 64
    with pytest.raises(PolicyError, match=r"batch-rollout-evidence\.json: .*references/batch\.md"):
        validate_evidence_binding(CONF, json.dumps(evidence), tracked)


@pytest.mark.parametrize(
    ("section", "field", "label"),
    [
        ("catalog", "merge_revision", r"catalog\.merge_revision"),
        ("tracked_target", "receipt_revision", r"mirrors\.tracked_target\.receipt_revision"),
    ],
)
def test_each_stale_revision_field_is_reported(tmp_path: Path, section: str, field: str, label: str) -> None:
    tracked, _, evidence = _evidence_copy(tmp_path)
    target = evidence["mirrors"][section] if section == "tracked_target" else evidence[section]
    target[field] = "0" * 40
    with pytest.raises(PolicyError, match=rf"batch-rollout-evidence\.json: {label}"):
        validate_evidence_binding(CONF, json.dumps(evidence), tracked)


@pytest.mark.parametrize("files", [{}, [], None])
def test_empty_or_malformed_file_list_is_refused(tmp_path: Path, files: object) -> None:
    tracked, _, evidence = _evidence_copy(tmp_path)
    evidence["mirrors"]["tracked_target"]["files"] = files
    with pytest.raises(PolicyError, match=r"batch-rollout-evidence\.json"):
        validate_evidence_binding(CONF, json.dumps(evidence), tracked)


def test_trimmed_file_list_is_refused(tmp_path: Path) -> None:
    tracked, _, evidence = _evidence_copy(tmp_path)
    del evidence["mirrors"]["tracked_target"]["files"]["SKILL.md"]
    with pytest.raises(PolicyError, match=r"not listed: \['SKILL\.md'\]"):
        validate_evidence_binding(CONF, json.dumps(evidence), tracked)


def test_mirror_file_missing_from_evidence_is_refused(tmp_path: Path) -> None:
    tracked, _, evidence = _evidence_copy(tmp_path)
    (tracked / "references" / "extra.md").write_text("extra\n", encoding="utf-8")
    with pytest.raises(PolicyError, match=r"not listed: \['references/extra\.md'\]"):
        validate_evidence_binding(CONF, json.dumps(evidence), tracked)


@pytest.mark.parametrize("key", ["../outside.md", "/etc/hosts", "references/gone.md"])
def test_listed_path_outside_the_mirror_is_refused(tmp_path: Path, key: str) -> None:
    tracked, _, evidence = _evidence_copy(tmp_path)
    (tmp_path / "outside.md").write_text("outside\n", encoding="utf-8")
    evidence["mirrors"]["tracked_target"]["files"][key] = "0" * 64
    with pytest.raises(PolicyError, match=r"listed but not in the mirror"):
        validate_evidence_binding(CONF, json.dumps(evidence), tracked)


def test_missing_pin_field_is_refused(tmp_path: Path) -> None:
    tracked, _, evidence = _evidence_copy(tmp_path)
    del evidence["benchbox_pin"]
    with pytest.raises(PolicyError, match=r"cannot read the catalog pin fields"):
        validate_evidence_binding(CONF, json.dumps(evidence), tracked)


@pytest.mark.parametrize(
    ("old", "new"), [("skill  = todo\n", ""), ("skill  = todo\n", "skill  = todo\nskill  = todo-extra\n")]
)
def test_conf_must_pin_todo_in_exactly_one_group(tmp_path: Path, old: str, new: str) -> None:
    tracked, _, evidence = _evidence_copy(tmp_path)
    assert old in CONF
    conf = CONF.replace(old, new)
    if new:
        conf = conf.replace("skill  = benchbox\n", "skill  = benchbox\nskill  = todo\n")
    with pytest.raises(PolicyError, match=r"expected exactly one conf group"):
        validate_evidence_binding(conf, json.dumps(evidence), tracked)
