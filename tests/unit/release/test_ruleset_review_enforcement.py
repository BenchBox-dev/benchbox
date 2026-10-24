from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = ROOT / "_project" / "scripts"


def _load(name: str):
    sys.path.insert(0, str(SCRIPTS_DIR))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rre = _load("ruleset_review_enforcement")

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _check(context: str, integration_id: int | None = None) -> dict:
    if context == "oracle-gate" and integration_id is None:
        integration_id = 5201041
    return {"context": context} if integration_id is None else {"context": context, "integration_id": integration_id}


def _pr_rule(
    count: int, thread_resolution: bool | None = True, contexts: tuple[str, ...] = ("core", "oracle-gate")
) -> list[dict]:
    parameters: dict[str, object] = {
        "required_approving_review_count": count,
    }
    if thread_resolution is not None:
        parameters["required_review_thread_resolution"] = thread_resolution
    return [
        {
            "type": "required_status_checks",
            "parameters": {"required_status_checks": [_check(context) for context in contexts]},
        },
        {
            "type": "pull_request",
            "parameters": parameters,
        },
    ]


def test_enforced_ruleset_passes() -> None:
    rules = _pr_rule(count=0, thread_resolution=True)
    assert rre.is_review_enforced(rules) is True
    assert rre.review_enforcement_findings(rules) == []


def test_unenforced_thread_resolution_fails() -> None:
    findings = rre.review_enforcement_findings(_pr_rule(count=0, thread_resolution=False))
    assert findings
    assert "required_review_thread_resolution=False" in " ".join(findings)


def test_missing_thread_resolution_fails() -> None:
    findings = rre.review_enforcement_findings(_pr_rule(count=0, thread_resolution=None))
    assert findings
    assert "required_review_thread_resolution=False" in " ".join(findings)


def test_required_approving_review_count_is_not_checked() -> None:
    for count in (0, 1, 5):
        assert rre.review_enforcement_findings(_pr_rule(count=count, thread_resolution=True)) == []


def test_missing_pull_request_rule_fails() -> None:
    rules = [{"type": "required_status_checks", "parameters": {"required_status_checks": []}}]
    findings = rre.review_enforcement_findings(rules)
    assert findings and "no pull_request rule" in findings[0]


def test_missing_required_status_rule_fails() -> None:
    rules = [rule for rule in _pr_rule(count=0) if rule["type"] != "required_status_checks"]
    assert rre.is_review_enforced(rules) is False
    assert "no required_status_checks rule" in " ".join(rre.review_enforcement_findings(rules))


@pytest.mark.parametrize("contexts", [("core", "oracle-review", "oracle-gate"), ("core", "oracle-gate")])
def test_review_enforced_when_the_app_bound_oracle_gate_is_required(contexts: tuple[str, ...]) -> None:
    rules = _pr_rule(count=0, contexts=contexts)
    assert rre.is_review_enforced(rules) is True


def test_oracle_review_alone_no_longer_enforces_review() -> None:
    rules = _pr_rule(count=0, contexts=("core", "oracle-review"))
    assert rre.is_review_enforced(rules) is False
    assert "must include oracle-gate scoped to the oracle App" in " ".join(rre.review_enforcement_findings(rules))


@pytest.mark.parametrize("integration_id", [None, 15368, 0])
def test_oracle_gate_without_the_oracle_app_binding_fails(integration_id: int | None) -> None:
    rules = _pr_rule(count=0)
    rules[0]["parameters"]["required_status_checks"] = [
        {"context": "core"},
        {"context": "oracle-gate", **({} if integration_id is None else {"integration_id": integration_id})},
    ]
    assert rre.is_review_enforced(rules) is False
    assert "scoped to the oracle App" in " ".join(rre.review_enforcement_findings(rules))


@pytest.mark.parametrize(
    "checks", [[], [{"context": "core"}], [{"context": "oracle-review"}], [{"context": "oracle-gate-other"}]]
)
def test_missing_oracle_gate_fails(checks: list[dict]) -> None:
    rules = _pr_rule(count=0)
    rules[0]["parameters"]["required_status_checks"] = checks
    assert rre.is_review_enforced(rules) is False
    assert "must include oracle-gate scoped" in " ".join(rre.review_enforcement_findings(rules))


@pytest.mark.parametrize(
    "parameters",
    [
        None,
        [],
        {},
        {"required_status_checks": None},
        {"required_status_checks": "oracle-review"},
        {"required_status_checks": {"context": "oracle-review"}},
        {"required_status_checks": ["oracle-review"]},
        {"required_status_checks": [{}]},
        {"required_status_checks": [{"context": ""}]},
        {"required_status_checks": [{"context": None}]},
        {"required_status_checks": [{"context": "oracle-review"}, None]},
    ],
)
def test_malformed_required_status_checks_fail_closed(parameters: object) -> None:
    rules = _pr_rule(count=0)
    rules[0]["parameters"] = parameters
    assert rre.is_review_enforced(rules) is False
    assert "malformed" in " ".join(rre.review_enforcement_findings(rules))


@pytest.mark.parametrize("thread_resolution", ["true", "false", 1])
def test_malformed_thread_resolution_fails_closed(thread_resolution: object) -> None:
    rules = _pr_rule(count=0)
    rules[1]["parameters"]["required_review_thread_resolution"] = thread_resolution
    assert rre.is_review_enforced(rules) is False


def test_extract_rules_accepts_both_payload_shapes() -> None:
    flat = _pr_rule(count=0)
    assert rre.extract_rules(flat) == flat
    assert rre.extract_rules({"rules": flat}) == flat
    assert rre.extract_rules({"name": "develop-squash-only", "rules": flat}) == flat


def test_cli_exit_codes(tmp_path: Path) -> None:
    import json

    enforced = tmp_path / "enforced.json"
    enforced.write_text(json.dumps(_pr_rule(count=0)), encoding="utf-8")
    unenforced = tmp_path / "unenforced.json"
    unenforced.write_text(json.dumps(_pr_rule(count=0, thread_resolution=False)), encoding="utf-8")

    assert rre.main(["--rules-file", str(enforced)]) == 0
    assert rre.main(["--rules-file", str(unenforced)]) == 1


def test_cli_rejects_missing_oracle_review(tmp_path: Path) -> None:
    import json

    rules = _pr_rule(count=0)
    rules[0]["parameters"]["required_status_checks"] = [{"context": "core"}]
    missing_oracle = tmp_path / "missing-oracle.json"
    missing_oracle.write_text(json.dumps(rules), encoding="utf-8")

    assert rre.main(["--rules-file", str(missing_oracle)]) == 1
