from __future__ import annotations

import copy
import io
import json
import urllib.error
from email.message import Message
from pathlib import Path

import pytest

from scripts import ruleset_drift_check
from scripts.ruleset_drift_check import (
    ExpectedRuleset,
    blocking_findings,
    compare_ruleset,
    deployment_policy_findings,
    environment_protection_findings,
    merge_queue_findings,
    parse_expected_rulesets,
    release_branch_findings,
    retired_release_ruleset_findings,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).parent.parent.parent

FIXTURE_REF = "refs/heads/fixture"
FIXTURE_CHECKS = ("check-a", "check-b")


def _fixture_expected() -> ExpectedRuleset:
    return ExpectedRuleset(
        name="fixture-ruleset",
        ref=FIXTURE_REF,
        required_checks=FIXTURE_CHECKS,
        strict_required_status_checks_policy=False,
        required_linear_history=True,
        non_fast_forward=True,
        deletion=True,
        bypass_actors_none=True,
    )


def _live_ruleset(ref: str, checks: list[str], *, strict: bool = False, bypass: list[dict] | None = None) -> dict:
    return {
        "name": "fixture-ruleset",
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

    assert set(expected) == {"develop-squash-only", "release-branches"}
    assert expected["develop-squash-only"].ref == "refs/heads/develop"
    assert expected["develop-squash-only"].required_checks == (
        "core",
        "explorer",
        "results-data",
        "docs",
        "tooling",
        "hold",
        "review-gate",
        "release-v2 probe",
    )
    assert expected["develop-squash-only"].strict_required_status_checks_policy is False


def test_matching_ruleset_has_no_drift_findings() -> None:
    live = _live_ruleset(FIXTURE_REF, list(FIXTURE_CHECKS))

    assert compare_ruleset(_fixture_expected(), live) == []


def test_required_context_drift_is_reported() -> None:
    live = _live_ruleset(FIXTURE_REF, [FIXTURE_CHECKS[0]])

    findings = compare_ruleset(_fixture_expected(), live)

    assert findings
    assert "required checks" in findings[0]
    assert FIXTURE_CHECKS[1] in findings[0]


def test_ruleset_policy_drift_is_reported() -> None:
    live = _live_ruleset(
        FIXTURE_REF,
        list(FIXTURE_CHECKS),
        strict=True,
        bypass=[{"actor_type": "RepositoryRole"}],
    )
    live["rules"] = [rule for rule in live["rules"] if rule["type"] != "deletion"]

    findings = compare_ruleset(_fixture_expected(), live)

    assert any("strict_required_status_checks_policy" in finding for finding in findings)
    assert any("deletion" in finding for finding in findings)
    assert any("bypass actors" in finding for finding in findings)


def test_bypass_actor_visibility_can_be_required() -> None:
    live = _live_ruleset(FIXTURE_REF, list(FIXTURE_CHECKS))
    del live["bypass_actors"]

    findings = compare_ruleset(_fixture_expected(), live, require_bypass_actor_visibility=True)

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


@pytest.mark.parametrize("name", ["pypi", "test-pypi", "review"])
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
            lambda p: p["branch_policies"].append({"type": "branch", "name": "release"}),
            "deployment policies",
        ),
        (
            "review",
            lambda env: None,
            lambda p: p["branch_policies"].append({"type": "branch", "name": "feature/*"}),
            "review environment: deployment policies",
        ),
        (
            "review",
            lambda env: None,
            lambda p: p["branch_policies"].remove({"type": "branch", "name": "release/*"}),
            "review environment: deployment policies",
        ),
        (
            "review",
            lambda env: None,
            lambda p: p["branch_policies"].remove({"type": "branch", "name": "develop"}),
            "review environment: deployment policies",
        ),
        (
            "review",
            lambda env: None,
            lambda p: p["branch_policies"].append({"type": "tag", "name": "release/*"}),
            "review environment: deployment policies",
        ),
        (
            "review",
            lambda env: env["deployment_branch_policy"].update(custom_branch_policies=False),
            lambda p: None,
            "review environment: deployment_branch_policy",
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
        "review": _deployment_policies("review"),
    }
    policies["pypi"]["branch_policies"].append({"type": "branch", "name": "*"})
    monkeypatch.setattr(ruleset_drift_check, "_fetch_live_rulesets", lambda _repo, _token: {})
    monkeypatch.setattr(
        ruleset_drift_check,
        "_fetch_environment",
        lambda _repo, _token, name: {**_pypi_environment(), **_deployment_environment(), "name": name},
    )
    monkeypatch.setattr(ruleset_drift_check, "_fetch_environment_policies", lambda _repo, _token, name: policies[name])
    monkeypatch.setattr(ruleset_drift_check, "_fetch_branch_exists", lambda _repo, _token, _branch: False)
    monkeypatch.delenv("RELEASE_READINESS_OVERRIDE_SHA", raising=False)
    monkeypatch.delenv("RELEASE_READINESS_OVERRIDE_REASON", raising=False)

    assert ruleset_drift_check.main(["--token", "token", "--output", str(output)]) == 1

    blocking = json.loads(output.read_text(encoding="utf-8"))["blocking_findings"]
    assert any("pypi environment: deployment policies" in finding for finding in blocking)
    assert not any("test-pypi environment" in finding for finding in blocking)
    assert not any("retired" in finding for finding in blocking)


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

    monkeypatch.setattr(
        ruleset_drift_check, "_fetch_branch_exists", lambda _repo, token, _branch: seen.append(token) or False
    )

    assert ruleset_drift_check.main(["--token-stdin"]) == 1
    assert seen == ["stdin-token"] * 8


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://api.github.com/x", code, "status", Message(), None)


def _stub_branch_api(monkeypatch: pytest.MonkeyPatch, outcome: object) -> list[str]:
    seen: list[str] = []

    def fake_api_json(url: str, token: str) -> dict:
        seen.append(url)
        if isinstance(outcome, Exception):
            raise outcome
        return {"name": "release"}

    monkeypatch.setattr(ruleset_drift_check, "_api_json", fake_api_json)
    return seen


def test_release_branch_present_is_a_finding(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _stub_branch_api(monkeypatch, {"name": "release"})

    findings = release_branch_findings("owner/repo", "token")

    assert seen == ["https://api.github.com/repos/owner/repo/branches/release"]
    assert len(findings) == 1
    assert "`release` branch exists" in findings[0]


def test_release_branch_absent_is_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_branch_api(monkeypatch, _http_error(404))

    assert release_branch_findings("owner/repo", "token") == []


def test_release_branch_lookup_error_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_branch_api(monkeypatch, _http_error(403))

    findings = release_branch_findings("owner/repo", "token")

    assert len(findings) == 1
    assert "could not verify" in findings[0]
    assert blocking_findings(findings) == findings


def test_ruleset_targeting_release_branch_is_a_finding() -> None:
    stale = _live_ruleset("refs/heads/release", ["validate-base"])
    stale["name"] = "stale-release-ruleset"

    findings = retired_release_ruleset_findings([stale])

    assert len(findings) == 1
    assert "stale-release-ruleset" in findings[0]
    assert "refs/heads/release" in findings[0]


def test_develop_and_tag_rulesets_are_not_release_findings() -> None:
    develop = _live_ruleset("refs/heads/develop", ["core"])
    tag = {"name": "v-tag-restricted", "conditions": {"ref_name": {"include": ["refs/tags/v*"], "exclude": []}}}
    no_conditions = {"name": "bare"}

    assert retired_release_ruleset_findings([develop, tag, no_conditions]) == []


def test_main_fails_when_the_release_branch_reappears(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output = tmp_path / "ruleset-drift.json"
    stale = _live_ruleset("refs/heads/release", ["validate-base"])
    stale["name"] = "stale-release-ruleset"
    monkeypatch.setattr(ruleset_drift_check, "_fetch_live_rulesets", lambda _repo, _token: {stale["name"]: stale})
    monkeypatch.setattr(
        ruleset_drift_check,
        "_fetch_environment",
        lambda _repo, _token, name: {**_pypi_environment(), **_deployment_environment(), "name": name},
    )
    monkeypatch.setattr(
        ruleset_drift_check, "_fetch_environment_policies", lambda _repo, _token, name: _deployment_policies(name)
    )
    monkeypatch.setattr(ruleset_drift_check, "_fetch_branch_exists", lambda _repo, _token, _branch: True)
    monkeypatch.delenv("RELEASE_READINESS_OVERRIDE_SHA", raising=False)
    monkeypatch.delenv("RELEASE_READINESS_OVERRIDE_REASON", raising=False)

    assert ruleset_drift_check.main(["--token", "token", "--output", str(output)]) == 1

    blocking = json.loads(output.read_text(encoding="utf-8"))["blocking_findings"]
    assert any("`release` branch exists" in finding for finding in blocking)
    assert any("stale-release-ruleset" in finding for finding in blocking)


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
        [
            "core",
            "explorer",
            "results-data",
            "docs",
            "tooling",
            "hold",
            "review-gate",
            "release-v2 probe",
        ],
        strict=False,
    )
    live["name"] = "develop-squash-only"
    bound = {"review-gate": 5201041, "release-v2 probe": 15368}
    for check in live["rules"][0]["parameters"]["required_status_checks"]:
        if check["context"] in bound:
            check["integration_id"] = bound[check["context"]]
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
        (
            lambda live: _rule(live, "required_status_checks")["parameters"].update(do_not_enforce_on_create=True),
            "do_not_enforce_on_create",
        ),
    ],
)
def test_develop_ruleset_drift_is_blocking(mutate, expected_text: str) -> None:
    live = _verified_develop_ruleset()
    mutate(live)

    findings = _develop_findings(live)

    assert any(expected_text in finding for finding in findings)
    assert ruleset_drift_check.blocking_findings(findings)


@pytest.mark.parametrize("context", ["review-gate", "release-v2 probe"])
def test_missing_develop_required_check_is_blocking(context: str) -> None:
    live = _verified_develop_ruleset()
    checks = _required_checks(live)
    checks[:] = [check for check in checks if check["context"] != context]

    findings = _develop_findings(live)

    assert any("required checks" in finding and context in finding for finding in findings)
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


def _drop_review_gate(live: dict) -> None:
    checks = _required_checks(live)
    checks[:] = [check for check in checks if check["context"] != "review-gate"]


def _set_integration(context: str, value: int | None):
    def mutate(live: dict) -> None:
        _check(live, context)["integration_id"] = value

    return mutate


@pytest.mark.parametrize(
    "mutate, expected_text",
    [
        (_drop_tooling, "tooling"),
        (_drop_review_gate, "review-gate"),
        (_set_integration("core", 15368), "core"),
        (_set_integration("core", 1), "integration_id"),
        (_set_integration("review-gate", None), "review-gate"),
        (_set_integration("review-gate", 15368), "review-gate"),
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


def test_capture_comparison_review_gate_removed_fails_and_rebound_passes() -> None:
    live = _capture()
    _drop_review_gate(live)
    findings = ruleset_drift_check.capture_comparison_findings(_capture(), live)
    assert any("review-gate" in finding for finding in findings)

    live_unbound = _capture()
    _drop_review_gate(live_unbound)
    _required_checks(live_unbound).append({"context": "review-gate"})
    assert ruleset_drift_check.capture_comparison_findings(_capture(), live_unbound)

    live_rebound = _capture()
    _drop_review_gate(live_rebound)
    _required_checks(live_rebound).append({"context": "review-gate", "integration_id": 5201041})
    assert ruleset_drift_check.capture_comparison_findings(_capture(), live_rebound) == []


def test_compare_capture_command_exit_codes(monkeypatch, capsys, tmp_path: Path) -> None:
    live = _capture()
    monkeypatch.setattr(ruleset_drift_check, "_api_json", lambda url, token: live)
    arguments = ["--compare-capture", str(CAPTURE_FIXTURE), "--token", "token"]

    assert ruleset_drift_check.main(arguments) == 0

    _drop_tooling(live)
    assert ruleset_drift_check.main(arguments) == 1
    assert "tooling" in capsys.readouterr().out

    live_without_gate = _capture()
    _drop_review_gate(live_without_gate)
    monkeypatch.setattr(ruleset_drift_check, "_api_json", lambda url, token: live_without_gate)
    assert ruleset_drift_check.main(arguments) == 1
    assert "review-gate" in capsys.readouterr().out


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


RELEASE_BRANCHES_CHECKS = (
    "core",
    "explorer",
    "results-data",
    "docs",
    "tooling",
    "hold",
    "review-gate",
    "release-v2 probe",
)
OWNER_BYPASS = {"actor_id": 57046, "actor_type": "User", "bypass_mode": "always"}


def _runbook_rulesets() -> dict:
    runbook = REPO_ROOT / "docs" / "internal" / "repo-admin-settings.md"
    return parse_expected_rulesets(runbook.read_text(encoding="utf-8"))


def _verified_release_branches_ruleset() -> dict:
    live = _verified_develop_ruleset()
    live["name"] = "release-branches"
    live["target"] = "branch"
    live["conditions"]["ref_name"]["include"] = ["refs/heads/release/**"]
    live["rules"] = [rule for rule in live["rules"] if rule["type"] not in {"deletion", "required_linear_history"}]
    _rule(live, "required_status_checks")["parameters"]["do_not_enforce_on_create"] = True
    return live


def _rule(ruleset: dict, rule_type: str) -> dict:
    return next(rule for rule in ruleset["rules"] if rule["type"] == rule_type)


def _release_lifecycle_ruleset() -> dict:
    return {
        "name": "release-branch-lifecycle",
        "target": "branch",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": ["refs/heads/release/**"], "exclude": []}},
        "bypass_actors": [dict(OWNER_BYPASS)],
        "rules": [{"type": "creation"}, {"type": "deletion"}],
    }


def _release_branches_findings(live: dict) -> list[str]:
    return compare_ruleset(_runbook_rulesets()["release-branches"], live, require_bypass_actor_visibility=True)


def test_runbook_expects_the_release_branches_ruleset_with_the_develop_gate() -> None:
    rulesets = _runbook_rulesets()
    release = rulesets["release-branches"]

    assert release.ref == "refs/heads/release/**"
    assert release.required_checks == rulesets["develop-squash-only"].required_checks == RELEASE_BRANCHES_CHECKS
    assert release.strict_required_status_checks_policy is False
    assert release.required_linear_history is False
    assert release.do_not_enforce_on_create is True
    assert rulesets["develop-squash-only"].do_not_enforce_on_create is False
    assert release.non_fast_forward is True
    assert release.deletion is False
    assert release.bypass_actors_none is True


def test_matching_release_branches_ruleset_has_no_findings() -> None:
    assert _release_branches_findings(_verified_release_branches_ruleset()) == []


@pytest.mark.parametrize(
    ("mutate", "expected_text"),
    [
        (lambda live: live.update(enforcement="disabled"), "enforcement"),
        (lambda live: live["conditions"]["ref_name"].update(include=["refs/heads/release"]), "target refs"),
        (lambda live: live["rules"].insert(0, {"type": "required_status_checks", "parameters": {}}), "required checks"),
        (lambda live: live["rules"].append({"type": "deletion"}), "deletion"),
        (lambda live: live["rules"].remove({"type": "non_fast_forward"}), "non_fast_forward"),
        (lambda live: live["rules"].append({"type": "required_linear_history"}), "required_linear_history"),
        (
            lambda live: _rule(live, "required_status_checks")["parameters"].update(do_not_enforce_on_create=False),
            "do_not_enforce_on_create",
        ),
        (lambda live: live.update(bypass_actors=[dict(OWNER_BYPASS)]), "bypass actors"),
        (lambda live: live.pop("bypass_actors"), "bypass actors are not visible"),
        (lambda live: _check(live, "review-gate").pop("integration_id"), "review-gate"),
        (
            lambda live: _rule(live, "pull_request")["parameters"].update(required_review_thread_resolution=False),
            "thread",
        ),
    ],
)
def test_weakened_release_branches_ruleset_is_blocking(mutate, expected_text: str) -> None:
    live = _verified_release_branches_ruleset()
    mutate(live)

    findings = _release_branches_findings(live)

    assert any(expected_text in finding for finding in findings), findings
    assert ruleset_drift_check.blocking_findings(findings)


@pytest.mark.parametrize("dropped", RELEASE_BRANCHES_CHECKS)
def test_every_release_branches_required_context_is_pinned(dropped: str) -> None:
    live = _verified_release_branches_ruleset()
    checks = _required_checks(live)
    checks[:] = [check for check in checks if check["context"] != dropped]

    findings = _release_branches_findings(live)

    assert any("required checks" in finding and dropped in finding for finding in findings)


def test_matching_release_lifecycle_ruleset_has_no_findings() -> None:
    assert ruleset_drift_check.release_lifecycle_findings(_release_lifecycle_ruleset()) == []


def test_a_missing_release_lifecycle_ruleset_is_a_finding() -> None:
    assert ruleset_drift_check.release_lifecycle_findings(None) == ["release-branch-lifecycle: live ruleset is missing"]


@pytest.mark.parametrize(
    ("mutate", "expected_text"),
    [
        (lambda live: live.update(enforcement="evaluate"), "enforcement"),
        (lambda live: live["conditions"]["ref_name"].update(include=["refs/heads/release"]), "target refs"),
        (lambda live: live["rules"].remove({"type": "creation"}), "creation rule is missing"),
        (lambda live: live["rules"].remove({"type": "deletion"}), "deletion rule is missing"),
        (lambda live: live.update(bypass_actors=[]), "bypass actors are ()"),
        (
            lambda live: live["bypass_actors"].append(
                {"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}
            ),
            "bypass actors are",
        ),
        (lambda live: live["bypass_actors"][0].update(bypass_mode="pull_request"), "bypass actors are"),
        (lambda live: live.pop("bypass_actors"), "bypass actors are not visible"),
    ],
)
def test_weakened_release_lifecycle_ruleset_is_blocking(mutate, expected_text: str) -> None:
    live = _release_lifecycle_ruleset()
    mutate(live)

    findings = ruleset_drift_check.release_lifecycle_findings(live, require_bypass_actor_visibility=True)

    assert any(expected_text in finding for finding in findings), findings
    assert ruleset_drift_check.blocking_findings(findings)


def _all_live_rulesets() -> dict[str, dict]:
    develop = _verified_develop_ruleset()
    develop["target"] = "branch"
    tag = {
        "name": "v-tag-restricted",
        "target": "tag",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": ["refs/tags/v*"], "exclude": []}},
        "bypass_actors": [dict(OWNER_BYPASS)],
        "rules": [{"type": "creation"}, {"type": "update"}, {"type": "deletion"}],
    }
    return {
        "develop-squash-only": develop,
        "release-branches": _verified_release_branches_ruleset(),
        "release-branch-lifecycle": _release_lifecycle_ruleset(),
        "v-tag-restricted": tag,
    }


def _run_main(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rulesets: dict[str, dict]) -> tuple[int, dict]:
    output = tmp_path / "ruleset-drift.json"
    monkeypatch.setattr(ruleset_drift_check, "_fetch_live_rulesets", lambda _repo, _token: copy.deepcopy(rulesets))
    monkeypatch.setattr(ruleset_drift_check, "_fetch_branch_exists", lambda _repo, _token, _branch: False)
    monkeypatch.setattr(
        ruleset_drift_check,
        "_fetch_environment",
        lambda _repo, _token, name: {**_pypi_environment(), **_deployment_environment(), "name": name},
    )
    monkeypatch.setattr(
        ruleset_drift_check, "_fetch_environment_policies", lambda _repo, _token, name: _deployment_policies(name)
    )
    monkeypatch.delenv("RELEASE_READINESS_OVERRIDE_SHA", raising=False)
    monkeypatch.delenv("RELEASE_READINESS_OVERRIDE_REASON", raising=False)
    code = ruleset_drift_check.main(["--token", "token", "--require-bypass-actor-visibility", "--output", str(output)])
    return code, json.loads(output.read_text(encoding="utf-8"))


def test_main_passes_when_every_ruleset_matches_including_the_maintenance_rulesets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    code, report = _run_main(monkeypatch, tmp_path, _all_live_rulesets())

    assert report["blocking_findings"] == []
    assert code == 0


@pytest.mark.parametrize("missing", ["release-branches", "release-branch-lifecycle"])
def test_main_fails_when_a_maintenance_ruleset_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, missing: str
) -> None:
    rulesets = _all_live_rulesets()
    del rulesets[missing]

    code, report = _run_main(monkeypatch, tmp_path, rulesets)

    assert code == 1
    assert report["blocking_findings"] == [f"{missing}: live ruleset is missing"]
