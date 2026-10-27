from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from scripts import ruleset_drift_check
from scripts.ruleset_drift_check import (
    compare_ruleset,
    deployment_policy_findings,
    environment_protection_findings,
    merge_queue_findings,
    parse_expected_rulesets,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).parent.parent.parent


def _live_ruleset(ref: str, checks: list[str], *, strict: bool = False, bypass: list[dict] | None = None) -> dict:
    return {
        "name": "release-only",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": [ref], "exclude": []}},
        "bypass_actors": bypass or [],
        "rules": [
            {
                "type": "required_status_checks",
                "parameters": {
                    "strict_required_status_checks_policy": strict,
                    "required_status_checks": [{"context": check} for check in checks],
                },
            },
            {"type": "required_linear_history"},
            {"type": "non_fast_forward"},
            {"type": "deletion"},
        ],
    }


def test_parse_expected_rulesets_from_admin_runbook() -> None:
    expected = parse_expected_rulesets((REPO_ROOT / "docs" / "internal" / "repo-admin-settings.md").read_text())

    assert expected["develop-squash-only"].ref == "refs/heads/develop"
    assert expected["develop-squash-only"].required_checks == (
        "core",
        "explorer",
        "results-data",
        "docs",
        "tooling",
        "hold",
        "oracle-gate",
    )
    assert expected["develop-squash-only"].strict_required_status_checks_policy is False
    assert expected["release-only"].ref == "refs/heads/release"
    assert expected["release-only"].required_checks == ("validate-base", "release-required-result")
    assert expected["release-only"].strict_required_status_checks_policy is False
    assert expected["release-only"].bypass_actors_none is True


def test_matching_ruleset_has_no_drift_findings() -> None:
    expected = parse_expected_rulesets((REPO_ROOT / "docs" / "internal" / "repo-admin-settings.md").read_text())[
        "release-only"
    ]
    live = _live_ruleset("refs/heads/release", ["validate-base", "release-required-result"])

    assert compare_ruleset(expected, live) == []


def test_required_context_drift_is_reported() -> None:
    expected = parse_expected_rulesets((REPO_ROOT / "docs" / "internal" / "repo-admin-settings.md").read_text())[
        "release-only"
    ]
    live = _live_ruleset("refs/heads/release", ["validate-base"])

    findings = compare_ruleset(expected, live)

    assert findings
    assert "required checks" in findings[0]
    assert "release-required-result" in findings[0]


def test_ruleset_policy_drift_is_reported() -> None:
    expected = parse_expected_rulesets((REPO_ROOT / "docs" / "internal" / "repo-admin-settings.md").read_text())[
        "release-only"
    ]
    live = _live_ruleset(
        "refs/heads/release",
        ["validate-base", "release-required-result"],
        strict=True,
        bypass=[{"actor_type": "RepositoryRole"}],
    )
    live["rules"] = [rule for rule in live["rules"] if rule["type"] != "deletion"]

    findings = compare_ruleset(expected, live)

    assert any("strict_required_status_checks_policy" in finding for finding in findings)
    assert any("deletion" in finding for finding in findings)
    assert any("bypass actors" in finding for finding in findings)


def test_bypass_actor_visibility_can_be_required() -> None:
    expected = parse_expected_rulesets((REPO_ROOT / "docs" / "internal" / "repo-admin-settings.md").read_text())[
        "release-only"
    ]
    live = _live_ruleset("refs/heads/release", ["validate-base", "release-required-result"])
    del live["bypass_actors"]

    findings = compare_ruleset(expected, live, require_bypass_actor_visibility=True)

    assert any("bypass actors are not visible" in finding for finding in findings)


def _pypi_environment() -> dict:
    return {
        "name": "pypi",
        "can_admins_bypass": True,
        "protection_rules": [
            {
                "type": "required_reviewers",
                "prevent_self_review": False,
                "reviewers": [{"type": "User", "reviewer": {"id": 57046, "login": "joeharris76"}}],
            }
        ],
    }


def test_matching_pypi_environment_has_no_drift_findings() -> None:
    assert environment_protection_findings(_pypi_environment()) == []


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda payload: payload.update(can_admins_bypass=False), "can_admins_bypass"),
        (lambda payload: payload.update(protection_rules=[]), "required_reviewers"),
        (lambda payload: payload["protection_rules"][0].update(prevent_self_review=True), "prevent_self_review"),
        (
            lambda payload: payload["protection_rules"][0]["reviewers"][0]["reviewer"].update(id=999),
            "required reviewers",
        ),
        (
            lambda payload: payload["protection_rules"][0]["reviewers"].append(
                {"type": "User", "reviewer": {"id": None, "login": None}}
            ),
            "required reviewers",
        ),
        (lambda payload: payload.update(protection_rules=None), "protection_rules"),
        (lambda payload: payload["protection_rules"][0].update(reviewers=None), "reviewers"),
    ],
)
def test_pypi_environment_policy_drift_is_reported(mutate, expected: str) -> None:
    payload = _pypi_environment()
    mutate(payload)

    findings = environment_protection_findings(payload)

    assert any(expected in finding for finding in findings)


def test_fetch_environment_uses_pypi_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, str]] = []

    def fake_api_json(url: str, token: str) -> dict:
        seen.append((url, token))
        return _pypi_environment()

    monkeypatch.setattr(ruleset_drift_check, "_api_json", fake_api_json)

    assert ruleset_drift_check._fetch_environment("owner/repo", "secret") == _pypi_environment()
    assert seen == [("https://api.github.com/repos/owner/repo/environments/pypi", "secret")]


def _deployment_environment() -> dict:
    return {"deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True}}


def _deployment_policies(name: str) -> dict:
    return {
        "branch_policies": [
            {"type": kind, "name": pattern}
            for kind, pattern in ruleset_drift_check.ENVIRONMENT_DEPLOYMENT_POLICIES[name]
        ]
    }


@pytest.mark.parametrize("name", ["pypi", "test-pypi", "oracle"])
def test_matching_deployment_policies_have_no_findings(name: str) -> None:
    assert deployment_policy_findings(name, _deployment_environment(), _deployment_policies(name)) == []


@pytest.mark.parametrize(
    ("name", "mutate_environment", "mutate_policies", "expected"),
    [
        ("pypi", lambda env: env.update(deployment_branch_policy=None), lambda p: None, "deployment_branch_policy"),
        (
            "pypi",
            lambda env: env["deployment_branch_policy"].update(protected_branches=True),
            lambda p: None,
            "deployment_branch_policy",
        ),
        ("pypi", lambda env: None, lambda p: p["branch_policies"].append({"type": "branch", "name": "*"}), "pypi"),
        ("pypi", lambda env: None, lambda p: p["branch_policies"].clear(), "deployment policies"),
        (
            "pypi",
            lambda env: None,
            lambda p: p["branch_policies"][0].update(type="branch"),
            "deployment policies",
        ),
        (
            "test-pypi",
            lambda env: None,
            lambda p: p["branch_policies"].remove({"type": "branch", "name": "release"}),
            "deployment policies",
        ),
        (
            "oracle",
            lambda env: None,
            lambda p: p["branch_policies"].append({"type": "branch", "name": "feature/*"}),
            "oracle environment: deployment policies",
        ),
        (
            "oracle",
            lambda env: env["deployment_branch_policy"].update(custom_branch_policies=False),
            lambda p: None,
            "oracle environment: deployment_branch_policy",
        ),
        ("pypi", lambda env: None, lambda p: p.update(branch_policies=None), "missing or malformed"),
        ("pypi", lambda env: None, lambda p: p["branch_policies"].append("v*"), "deployment policies"),
    ],
)
def test_deployment_policy_drift_is_reported(name: str, mutate_environment, mutate_policies, expected: str) -> None:
    environment = _deployment_environment()
    policies = _deployment_policies(name)
    mutate_environment(environment)
    mutate_policies(policies)

    findings = deployment_policy_findings(name, environment, policies)

    assert findings
    assert any(expected in finding for finding in findings)


def test_deployment_policies_absent_from_the_api_response_are_drift() -> None:
    findings = deployment_policy_findings("pypi", {"deployment_branch_policy": None}, {"message": "Not Found"})

    assert len(findings) == 2


def test_main_fails_when_a_live_environment_gains_a_policy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output = tmp_path / "ruleset-drift.json"
    policies = {
        "pypi": _deployment_policies("pypi"),
        "test-pypi": _deployment_policies("test-pypi"),
        "oracle": _deployment_policies("oracle"),
    }
    policies["pypi"]["branch_policies"].append({"type": "branch", "name": "*"})
    monkeypatch.setattr(ruleset_drift_check, "_fetch_live_rulesets", lambda _repo, _token: {})
    monkeypatch.setattr(
        ruleset_drift_check,
        "_fetch_environment",
        lambda _repo, _token, name: {**_pypi_environment(), **_deployment_environment(), "name": name},
    )
    monkeypatch.setattr(ruleset_drift_check, "_fetch_environment_policies", lambda _repo, _token, name: policies[name])
    monkeypatch.delenv("RELEASE_READINESS_OVERRIDE_SHA", raising=False)
    monkeypatch.delenv("RELEASE_READINESS_OVERRIDE_REASON", raising=False)

    assert ruleset_drift_check.main(["--token", "token", "--output", str(output)]) == 1

    blocking = json.loads(output.read_text(encoding="utf-8"))["blocking_findings"]
    assert any("pypi environment: deployment policies" in finding for finding in blocking)
    assert not any("test-pypi environment" in finding for finding in blocking)


def test_token_stdin_is_forwarded_to_all_github_queries(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr(ruleset_drift_check.sys, "stdin", io.StringIO("stdin-token\n"))
    monkeypatch.setattr(ruleset_drift_check, "_fetch_live_rulesets", lambda _repo, token: seen.append(token) or {})
    monkeypatch.setattr(
        ruleset_drift_check,
        "_fetch_environment",
        lambda _repo, token, _name: seen.append(token) or {**_pypi_environment(), **_deployment_environment()},
    )
    monkeypatch.setattr(
        ruleset_drift_check,
        "_fetch_environment_policies",
        lambda _repo, token, name: seen.append(token) or _deployment_policies(name),
    )

    assert ruleset_drift_check.main(["--token-stdin"]) == 1
    assert seen == ["stdin-token"] * 7


def test_github_api_failure_is_reported_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def _raise_api_error(_repo: str, _token: str) -> dict:
        raise RuntimeError("api unavailable")

    output = tmp_path / "ruleset-drift.json"
    monkeypatch.setattr(ruleset_drift_check, "_fetch_live_rulesets", _raise_api_error)
    monkeypatch.delenv("RELEASE_READINESS_OVERRIDE_SHA", raising=False)
    monkeypatch.delenv("RELEASE_READINESS_OVERRIDE_REASON", raising=False)

    rc = ruleset_drift_check.main(["--token", "token", "--output", str(output)])

    assert rc == 1
    assert json.loads(output.read_text()) == {"status": "error", "error": "api unavailable"}


def _live_with_queue(params: dict | None) -> dict:
    live = _live_ruleset("refs/heads/develop", ["core", "tooling"])
    live["name"] = "develop-squash-only"
    if params is not None:
        live["rules"].append({"type": "merge_queue", "parameters": dict(params)})
    return live


def test_merge_queue_absent_passes() -> None:
    assert ruleset_drift_check.merge_queue_findings(_live_with_queue(None), "develop-squash-only") == []


def test_merge_queue_present_is_blocking() -> None:
    live = _live_with_queue({"merge_method": "SQUASH", "check_response_timeout_minutes": 90})
    findings = ruleset_drift_check.merge_queue_findings(live, "develop-squash-only")
    assert len(findings) == 1
    assert "merge_queue" in findings[0]
    assert ruleset_drift_check.blocking_findings(findings) == findings


def _verified_develop_ruleset() -> dict:
    live = _live_ruleset(
        "refs/heads/develop",
        ["core", "explorer", "results-data", "docs", "tooling", "hold", "oracle-gate"],
        strict=False,
    )
    live["name"] = "develop-squash-only"
    for check in live["rules"][0]["parameters"]["required_status_checks"]:
        if check["context"] == "oracle-gate":
            check["integration_id"] = 5201041
    live["rules"].append(
        {
            "type": "pull_request",
            "parameters": {
                "require_code_owner_review": True,
                "required_review_thread_resolution": True,
            },
        }
    )
    return live


def _develop_findings(live: dict) -> list[str]:
    expected = parse_expected_rulesets((REPO_ROOT / "docs" / "internal" / "repo-admin-settings.md").read_text())[
        "develop-squash-only"
    ]
    findings = compare_ruleset(expected, live, require_bypass_actor_visibility=True)
    return findings + merge_queue_findings(live, expected.name)


def test_develop_ruleset_matching_runbook_has_no_findings() -> None:
    assert _develop_findings(_verified_develop_ruleset()) == []


@pytest.mark.parametrize(
    "mutate, expected_text",
    [
        (lambda live: live["rules"].append({"type": "merge_queue", "parameters": {}}), "merge_queue"),
        (lambda live: live["rules"].insert(0, {"type": "required_status_checks", "parameters": {}}), "required checks"),
        (lambda live: live.pop("bypass_actors"), "bypass actors are not visible"),
    ],
)
def test_develop_ruleset_drift_is_blocking(mutate, expected_text: str) -> None:
    live = _verified_develop_ruleset()
    mutate(live)

    findings = _develop_findings(live)

    assert any(expected_text in finding for finding in findings)
    assert ruleset_drift_check.blocking_findings(findings)


CAPTURE_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "ruleset-develop-squash-only.json"


def _capture() -> dict:
    return json.loads(CAPTURE_FIXTURE.read_text(encoding="utf-8"))


def _required_checks(ruleset: dict) -> list[dict]:
    return next(rule for rule in ruleset["rules"] if rule["type"] == "required_status_checks")["parameters"][
        "required_status_checks"
    ]


def _check(ruleset: dict, context: str) -> dict:
    return next(check for check in _required_checks(ruleset) if check["context"] == context)


def test_capture_comparison_ignores_only_volatile_fields() -> None:
    live = _capture()
    live["updated_at"] = "2026-10-25T10:00:00.000-04:00"
    live["_links"] = {"self": {"href": "https://example.invalid/other"}}
    live["current_user_can_bypass"] = "always"

    assert ruleset_drift_check.capture_comparison_findings(_capture(), live) == []


def _drop_tooling(live: dict) -> None:
    checks = _required_checks(live)
    checks[:] = [check for check in checks if check["context"] != "tooling"]


def _set_integration(context: str, value: int | None):
    def mutate(live: dict) -> None:
        _check(live, context)["integration_id"] = value

    return mutate


@pytest.mark.parametrize(
    "mutate, expected_text",
    [
        (_drop_tooling, "tooling"),
        (_set_integration("core", 15368), "core"),
        (_set_integration("core", 1), "integration_id"),
        (_set_integration("oracle-gate", None), "oracle-gate"),
        (_set_integration("oracle-gate", 15368), "oracle-gate"),
        (
            lambda live: live["bypass_actors"].append({"actor_id": 5, "actor_type": "RepositoryRole"}),
            "bypass_actors",
        ),
        (lambda live: live.__setitem__("enforcement", "evaluate"), "enforcement"),
        (
            lambda live: live["rules"][0]["parameters"].__setitem__("required_review_thread_resolution", False),
            "required_review_thread_resolution",
        ),
        (
            lambda live: next(r for r in live["rules"] if r["type"] == "required_status_checks")[
                "parameters"
            ].__setitem__("strict_required_status_checks_policy", True),
            "strict_required_status_checks_policy",
        ),
    ],
)
def test_capture_comparison_reports_each_substantive_difference(mutate, expected_text: str) -> None:
    live = _capture()
    mutate(live)

    findings = ruleset_drift_check.capture_comparison_findings(_capture(), live)

    assert any(expected_text in finding for finding in findings)


@pytest.mark.parametrize("hidden_in", [("capture", "live"), ("capture",), ("live",)])
def test_capture_comparison_fails_when_bypass_actors_are_hidden(hidden_in: tuple[str, ...]) -> None:
    documents = {"capture": _capture(), "live": _capture()}
    for name in hidden_in:
        documents[name].pop("bypass_actors")

    findings = ruleset_drift_check.capture_comparison_findings(documents["capture"], documents["live"])

    assert any("bypass_actors" in finding for finding in findings)


@pytest.mark.parametrize("enforcement", ["evaluate", "disabled"])
def test_capture_comparison_fails_unless_live_enforcement_is_active(enforcement: str) -> None:
    capture = _capture()
    capture["enforcement"] = enforcement
    live = _capture()
    live["enforcement"] = enforcement

    findings = ruleset_drift_check.capture_comparison_findings(capture, live)

    assert any("enforcement" in finding and "active" in finding for finding in findings)


def test_capture_comparison_distinguishes_null_from_numeric_integration_id() -> None:
    capture = _capture()
    live = _capture()
    _check(live, "core")["integration_id"] = None
    _check(capture, "core")["integration_id"] = 15368

    findings = ruleset_drift_check.capture_comparison_findings(capture, live)

    assert any("core" in finding and "15368" in finding and "None" in finding for finding in findings)


def test_capture_comparison_treats_absent_integration_id_as_null() -> None:
    live = _capture()
    _check(live, "core")["integration_id"] = None

    assert ruleset_drift_check.capture_comparison_findings(_capture(), live) == []


def test_compare_capture_command_exit_codes(monkeypatch, capsys, tmp_path: Path) -> None:
    live = _capture()
    monkeypatch.setattr(ruleset_drift_check, "_api_json", lambda url, token: live)
    arguments = ["--compare-capture", str(CAPTURE_FIXTURE), "--token", "token"]

    assert ruleset_drift_check.main(arguments) == 0

    _drop_tooling(live)
    assert ruleset_drift_check.main(arguments) == 1
    assert "tooling" in capsys.readouterr().out


@pytest.mark.parametrize("duplicate_in", ["capture", "live"])
def test_capture_comparison_detects_context_multiplicity(duplicate_in: str) -> None:
    documents = {"capture": _capture(), "live": _capture()}
    document = documents[duplicate_in]
    _required_checks(document).append(dict(_check(document, "core")))

    findings = ruleset_drift_check.capture_comparison_findings(documents["capture"], documents["live"])

    assert any("core" in finding for finding in findings)


def test_capture_comparison_allows_reordered_duplicate_contexts() -> None:
    capture = _capture()
    _required_checks(capture).append(dict(_check(capture, "core")))
    live = json.loads(json.dumps(capture))
    _required_checks(live).reverse()

    assert ruleset_drift_check.capture_comparison_findings(capture, live) == []
