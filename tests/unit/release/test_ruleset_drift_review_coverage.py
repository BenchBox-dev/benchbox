from __future__ import annotations

import importlib.util
from dataclasses import replace
from pathlib import Path

import pytest

from scripts.ruleset_drift_check import (
    WARNING_PREFIX,
    blocking_findings,
    compare_ruleset,
    parse_expected_rulesets,
    tag_creation_findings,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_review_enforcement():
    scripts_dir = REPO_ROOT / "_project" / "scripts"
    spec = importlib.util.spec_from_file_location(
        "ruleset_review_enforcement", scripts_dir / "ruleset_review_enforcement.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_rre = _load_review_enforcement()


def _tag_ruleset(
    *,
    name="v-tag-restricted",
    enforcement="active",
    include=("refs/tags/v*",),
    rule_types=("creation", "update", "deletion"),
):
    return {
        "name": name,
        "target": "tag",
        "enforcement": enforcement,
        "conditions": {"ref_name": {"include": list(include), "exclude": []}},
        "rules": [{"type": t} for t in rule_types],
    }


def _develop_expected():
    expected = parse_expected_rulesets((REPO_ROOT / "docs" / "internal" / "repo-admin-settings.md").read_text())[
        "develop-squash-only"
    ]
    base = tuple(check for check in expected.required_checks if not check.startswith("oracle-"))
    return replace(expected, required_checks=(*base, "oracle-review", "oracle-gate"))


def _status_check(context: str) -> dict:
    check: dict = {"context": context}
    if context == "oracle-gate":
        check["integration_id"] = 5201041
    return check


def _live_develop_ruleset(
    *,
    review_count: int = 0,
    thread_resolution: bool = True,
    oracle_contexts: tuple[str, ...] = ("oracle-review", "oracle-gate"),
) -> dict:
    return {
        "name": "develop-squash-only",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": ["refs/heads/develop"], "exclude": []}},
        "bypass_actors": [],
        "rules": [
            {
                "type": "pull_request",
                "parameters": {
                    "required_approving_review_count": review_count,
                    "required_review_thread_resolution": thread_resolution,
                },
            },
            {
                "type": "required_status_checks",
                "parameters": {
                    "strict_required_status_checks_policy": False,
                    "required_status_checks": [
                        {"context": "core"},
                        {"context": "explorer"},
                        {"context": "results-data"},
                        {"context": "docs"},
                        {"context": "tooling"},
                        *(_status_check(context) for context in oracle_contexts),
                        {"context": "hold"},
                        {"context": "oracle-gate"},
                    ],
                },
            },
            {"type": "required_linear_history"},
            {"type": "non_fast_forward"},
            {"type": "deletion"},
        ],
    }


def test_missing_develop_review_rule_is_blocking_by_default():
    live = _live_develop_ruleset(thread_resolution=False)

    findings = compare_ruleset(_develop_expected(), live)

    assert findings and blocking_findings(findings) == findings
    assert not any(f.startswith(WARNING_PREFIX) for f in findings)
    assert any("required_review_thread_resolution" in finding for finding in findings)


def test_develop_review_rule_passes_when_live_rule_is_present():
    assert compare_ruleset(_develop_expected(), _live_develop_ruleset()) == []


def _expected_with_oracle_contexts(*contexts: str):
    expected = _develop_expected()
    base = tuple(check for check in expected.required_checks if not check.startswith("oracle-"))
    return replace(expected, required_checks=(*base, *contexts))


@pytest.mark.parametrize(
    "contexts",
    [("oracle-review", "oracle-gate"), ("oracle-gate",), ("oracle-review",)],
)
def test_develop_drift_passes_with_any_supported_oracle_context_set(contexts):
    expected = _expected_with_oracle_contexts(*contexts)
    live = _live_develop_ruleset(oracle_contexts=contexts)

    assert compare_ruleset(expected, live) == []


def test_missing_oracle_contexts_are_blocking_despite_stale_expected_contexts():
    expected = _expected_with_oracle_contexts()
    live = _live_develop_ruleset(oracle_contexts=())

    findings = compare_ruleset(expected, live)

    message = "required_status_checks must include oracle-review or oracle-gate scoped to the oracle App"
    assert message in findings
    assert message in blocking_findings(findings)


def test_develop_review_rule_can_be_warn_only_for_explicit_migration_override():
    findings = compare_ruleset(
        _develop_expected(), _live_develop_ruleset(thread_resolution=False), enforce_review_rule=False
    )

    assert findings and all(f.startswith(WARNING_PREFIX) for f in findings)
    assert blocking_findings(findings) == []


def test_release_only_matches_runbook_expectations():
    expected = parse_expected_rulesets((REPO_ROOT / "docs" / "internal" / "repo-admin-settings.md").read_text())[
        "release-only"
    ]
    live = {
        "name": "release-only",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": ["refs/heads/release"], "exclude": []}},
        "bypass_actors": [],
        "rules": [
            {
                "type": "required_status_checks",
                "parameters": {
                    "strict_required_status_checks_policy": False,
                    "required_status_checks": [
                        {"context": "validate-base"},
                        {"context": "release-required-result"},
                    ],
                },
            },
            {"type": "required_linear_history"},
            {"type": "non_fast_forward"},
            {"type": "deletion"},
        ],
    }

    assert compare_ruleset(expected, live) == []


def test_tag_protection_missing_when_no_tag_ruleset_exists():
    branch_ruleset = {
        "name": "v-release-branches-minimal",
        "target": "branch",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": ["refs/heads/v*"], "exclude": []}},
        "rules": [{"type": "creation"}],
    }
    findings = _rre.tag_protection_findings([branch_ruleset])
    assert findings, "a branch ruleset must not satisfy the tag-creation requirement"
    assert "target='tag'" in findings[0]
    assert not _rre.is_tag_creation_protected([branch_ruleset])


def test_tag_protection_satisfied_by_active_v_tag_creation_ruleset():
    findings = _rre.tag_protection_findings([_tag_ruleset()])
    assert findings == []
    assert _rre.is_tag_creation_protected([_tag_ruleset()])


def test_tag_protection_refuses_creation_only_ruleset():
    findings = _rre.tag_protection_findings([_tag_ruleset(rule_types=("creation",))])
    assert findings and "no 'update' rule" in findings[0] and "no 'deletion' rule" in findings[0]
    assert not _rre.is_tag_creation_protected([_tag_ruleset(rule_types=("creation",))])


def test_tag_protection_satisfied_when_ref_is_all_tags():
    findings = _rre.tag_protection_findings([_tag_ruleset(include=("~ALL",))])
    assert findings == []


@pytest.mark.parametrize("pattern", ("refs/tags/v*", "refs/tags/*", "refs/tags/?*", "*"))
def test_tag_glob_coverage_accepts_only_provable_full_language_containment(pattern: str):
    assert _rre._tag_glob_covers(pattern)


@pytest.mark.parametrize(
    "pattern",
    ("refs/tags/*[v13tw]", "refs/tags/v?", "refs/tags/v*.*", "refs/tags/rc*"),
)
def test_tag_glob_coverage_rejects_sample_covering_or_narrow_patterns(pattern: str):
    assert not _rre._tag_glob_covers(pattern)


def test_tag_protection_flags_inactive_or_incomplete_tag_ruleset():
    inactive = _rre.tag_protection_findings([_tag_ruleset(enforcement="evaluate")])
    assert inactive and "enforcement='evaluate'" in inactive[0]
    no_creation = _rre.tag_protection_findings([_tag_ruleset(rule_types=("deletion",))])
    assert no_creation and "no 'creation' rule" in no_creation[0]
    wrong_ref = _rre.tag_protection_findings([_tag_ruleset(include=("refs/tags/rc*",))])
    assert wrong_ref and "does not cover refs/tags/v*" in wrong_ref[0]


def test_tag_protection_summary_only_payload_does_not_pass():
    summary = {"name": "v-tag-restricted", "target": "tag", "enforcement": "active"}
    findings = _rre.tag_protection_findings([summary])
    assert findings, "summary-only ruleset lacks a creation rule and must be flagged"


def test_tag_check_is_enforced_now_that_ruleset_is_applied():
    assert _rre.TAG_RULESET_ENFORCED is True


def test_tag_check_main_fails_when_missing_now_that_enforced(tmp_path, capsys):
    import json as _json

    payload = tmp_path / "rulesets.json"
    payload.write_text(_json.dumps([]), encoding="utf-8")
    rc = _rre.main(["--rulesets-file", str(payload)])
    out = capsys.readouterr().out
    assert rc == 1, "missing tag ruleset must block now that TAG_RULESET_ENFORCED is True"
    assert "Tag-creation ruleset - FAILED" in out
    assert "target='tag'" in out


def test_tag_check_main_warns_non_blocking_when_flag_off(tmp_path, capsys, monkeypatch):
    import json as _json

    monkeypatch.setattr(_rre, "TAG_RULESET_ENFORCED", False)
    payload = tmp_path / "rulesets.json"
    payload.write_text(_json.dumps([]), encoding="utf-8")
    rc = _rre.main(["--rulesets-file", str(payload)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "WARNING (non-blocking):" in out
    assert "target='tag'" in out


def test_tag_check_main_passes_when_ruleset_present(tmp_path, capsys):
    import json as _json

    payload = tmp_path / "rulesets.json"
    payload.write_text(_json.dumps([_tag_ruleset()]), encoding="utf-8")
    rc = _rre.main(["--rulesets-file", str(payload)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Tag-creation ruleset - OK" in out


def test_tag_protection_flags_exclude_that_negates_v_coverage():
    negated = _rre.tag_protection_findings(
        [
            _tag_ruleset(include=("~ALL",))
            | {"conditions": {"ref_name": {"include": ["~ALL"], "exclude": ["refs/tags/v*"]}}}
        ]
    )
    assert negated and "negates coverage" in negated[0]


def test_tag_bypass_advisory_surfaces_actors_without_failing_structure():
    ruleset = _tag_ruleset() | {
        "bypass_actors": [{"actor_type": "Integration", "actor_id": 42, "bypass_mode": "always"}]
    }
    assert _rre.is_tag_creation_protected([ruleset])
    advisory = _rre.tag_bypass_advisory([ruleset])
    assert advisory and "bypass_actors" in advisory[0]
    assert "Integration:42" in advisory[0]


def test_tag_bypass_advisory_empty_when_no_bypass_or_no_ruleset():
    assert _rre.tag_bypass_advisory([_tag_ruleset()]) == []
    assert _rre.tag_bypass_advisory([]) == []


def test_tag_check_main_prints_bypass_confirmation_on_ok(tmp_path, capsys):
    import json as _json

    ruleset = _tag_ruleset() | {"bypass_actors": [{"actor_type": "Team", "actor_id": 7, "bypass_mode": "pull_request"}]}
    payload = tmp_path / "rulesets.json"
    payload.write_text(_json.dumps([ruleset]), encoding="utf-8")
    rc = _rre.main(["--rulesets-file", str(payload)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Tag-creation ruleset - OK" in out
    assert "CONFIRM before enforcing:" in out


def test_tag_protection_flags_explicitly_empty_bypass_actors():
    ruleset = _tag_ruleset() | {"bypass_actors": []}
    findings = _rre.tag_protection_findings([ruleset])
    assert findings and "bypass_actors is empty" in findings[0]
    assert not _rre.is_tag_creation_protected([ruleset])
    assert _rre.tag_bypass_advisory([ruleset]) == []


def test_tag_protection_does_not_flag_missing_bypass_actors_key():
    ruleset = _tag_ruleset()
    assert "bypass_actors" not in ruleset
    assert _rre.tag_protection_findings([ruleset]) == []


def test_tag_protection_include_covers_via_broader_fnmatch_glob():
    findings = _rre.tag_protection_findings([_tag_ruleset(include=("refs/tags/*",))])
    assert findings == []


@pytest.mark.parametrize("pattern", ["refs/tags/v?", "refs/tags/v[0-9]*", "refs/tags/v?*"])
def test_tag_protection_rejects_globs_that_do_not_cover_the_full_v_domain(pattern: str):
    findings = _rre.tag_protection_findings([_tag_ruleset(include=(pattern,))])

    assert findings and "does not cover refs/tags/v*" in findings[0]


def test_tag_protection_flags_exclude_that_negates_v_coverage_via_broader_glob():
    negated = _rre.tag_protection_findings(
        [
            _tag_ruleset(include=("~ALL",))
            | {"conditions": {"ref_name": {"include": ["~ALL"], "exclude": ["refs/tags/*"]}}}
        ]
    )
    assert negated and "negates coverage" in negated[0]


def test_tag_protection_narrower_exclude_does_not_negate_coverage():
    findings = _rre.tag_protection_findings(
        [_tag_ruleset() | {"conditions": {"ref_name": {"include": ["refs/tags/v*"], "exclude": ["refs/tags/rc*"]}}}]
    )
    assert findings == []


def test_tag_creation_findings_blocks_by_default_when_no_tag_ruleset_exists():
    all_live = [
        _live_develop_ruleset(),
    ]
    findings = tag_creation_findings(all_live)
    assert findings and blocking_findings(findings) == findings
    assert not any(f.startswith(WARNING_PREFIX) for f in findings)
    assert any("target='tag'" in f for f in findings)


def test_tag_creation_findings_warns_when_enforcement_forced_off():
    all_live = [
        _live_develop_ruleset(),
    ]
    findings = tag_creation_findings(all_live, enforce_tag_rule=False)
    assert findings and all(f.startswith(WARNING_PREFIX) for f in findings)
    assert blocking_findings(findings) == []
    assert any("target='tag'" in f for f in findings)


def test_tag_creation_findings_empty_when_ruleset_present_and_no_bypass_gap():
    all_live = [
        _tag_ruleset() | {"bypass_actors": [{"actor_type": "Integration", "actor_id": 1, "bypass_mode": "always"}]}
    ]
    findings = tag_creation_findings(all_live)
    assert blocking_findings(findings) == []


def test_tag_creation_findings_require_visible_bypass_actors_when_requested():
    findings = tag_creation_findings(
        [_tag_ruleset()],
        require_bypass_actor_visibility=True,
    )

    assert findings and blocking_findings(findings) == findings
    assert "not visible" in findings[0]


def test_tag_creation_findings_can_be_switched_to_blocking_explicitly():
    findings = tag_creation_findings([], enforce_tag_rule=True)
    assert findings, "expected a blocking finding once tag-rule enforcement is on"
    assert not any(f.startswith(WARNING_PREFIX) for f in findings)
    assert blocking_findings(findings) == findings


def test_oracle_gate_without_the_oracle_app_binding_is_blocking():
    expected = _expected_with_oracle_contexts("oracle-gate")
    live = _live_develop_ruleset(oracle_contexts=("oracle-gate",))
    for check in live["rules"][1]["parameters"]["required_status_checks"]:
        check.pop("integration_id", None)

    findings = compare_ruleset(expected, live)

    assert "required_status_checks must include oracle-review or oracle-gate scoped to the oracle App" in (
        blocking_findings(findings)
    )
