#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RUNBOOK = REPO_ROOT / "docs" / "internal" / "repo-admin-settings.md"

sys.path.insert(0, str(REPO_ROOT / "_project" / "scripts"))

from ruleset_review_enforcement import (
    ORACLE_GATE_INTEGRATION_ID,
    TAG_RULESET_ENFORCED,
    extract_rules,
    review_enforcement_findings,
    tag_bypass_advisory,
    tag_protection_findings,
)

CLI_DESCRIPTION = "Compare live GitHub rulesets with the repository admin runbook."

DEVELOP_RULESET_ID = 15611785
CAPTURE_IGNORED_FIELDS = frozenset({"updated_at", "_links", "current_user_can_bypass"})

WARNING_PREFIX = "WARNING (non-blocking): "

DEVELOP_REVIEW_RULE_ENFORCED = True

PYPI_ENVIRONMENT = "pypi"
PYPI_REQUIRED_REVIEWERS = (("User", 57046, "joeharris76"),)
PYPI_CAN_ADMINS_BYPASS = True
PYPI_PREVENT_SELF_REVIEW = False
TEST_PYPI_ENVIRONMENT = "test-pypi"
ENVIRONMENT_DEPLOYMENT_POLICIES = {
    PYPI_ENVIRONMENT: (("tag", "v*"),),
    TEST_PYPI_ENVIRONMENT: (("branch", "develop"), ("branch", "release"), ("tag", "v*")),
}


@dataclass(frozen=True)
class ExpectedRuleset:
    name: str
    ref: str
    required_checks: tuple[str, ...]
    strict_required_status_checks_policy: bool
    required_linear_history: bool
    non_fast_forward: bool
    deletion: bool
    bypass_actors_none: bool


def environment_protection_findings(live: dict[str, Any]) -> list[str]:
    findings: list[str] = []
    if live.get("name") != PYPI_ENVIRONMENT:
        findings.append(f"pypi environment: name is {live.get('name')!r}, expected {PYPI_ENVIRONMENT!r}")
    if live.get("can_admins_bypass") is not PYPI_CAN_ADMINS_BYPASS:
        findings.append(
            "pypi environment: can_admins_bypass is "
            f"{live.get('can_admins_bypass')!r}, expected {PYPI_CAN_ADMINS_BYPASS!r}"
        )

    protection_rules = live.get("protection_rules")
    if not isinstance(protection_rules, list):
        findings.append("pypi environment: protection_rules is missing or malformed")
        return findings
    reviewer_rules = [
        rule for rule in protection_rules if isinstance(rule, dict) and rule.get("type") == "required_reviewers"
    ]
    if len(reviewer_rules) != 1:
        findings.append(f"pypi environment: found {len(reviewer_rules)} required_reviewers rules, expected exactly one")
        return findings

    rule = reviewer_rules[0]
    if rule.get("prevent_self_review") is not PYPI_PREVENT_SELF_REVIEW:
        findings.append(
            "pypi environment: prevent_self_review is "
            f"{rule.get('prevent_self_review')!r}, expected {PYPI_PREVENT_SELF_REVIEW!r}"
        )
    raw_reviewers = rule.get("reviewers")
    if not isinstance(raw_reviewers, list):
        findings.append("pypi environment: reviewers is missing or malformed")
        return findings
    reviewers: list[tuple[Any, Any, Any]] = []
    for reviewer in raw_reviewers:
        if not isinstance(reviewer, dict):
            reviewers.append((None, None, None))
            continue
        principal = reviewer.get("reviewer")
        if not isinstance(principal, dict):
            principal = {}
        reviewers.append((reviewer.get("type"), principal.get("id"), principal.get("login")))
    if tuple(reviewers) != PYPI_REQUIRED_REVIEWERS:
        findings.append(f"pypi environment: required reviewers are {reviewers!r}, expected {PYPI_REQUIRED_REVIEWERS!r}")
    return findings


def deployment_policy_findings(name: str, live_environment: dict[str, Any], live_policies: Any) -> list[str]:
    findings: list[str] = []
    branch_policy = live_environment.get("deployment_branch_policy")
    if branch_policy != {"protected_branches": False, "custom_branch_policies": True}:
        findings.append(
            f"{name} environment: deployment_branch_policy is {branch_policy!r}, expected custom policies only"
        )
    entries = live_policies.get("branch_policies") if isinstance(live_policies, dict) else None
    if not isinstance(entries, list):
        findings.append(f"{name} environment: deployment branch policies are missing or malformed")
        return findings
    live = {
        (entry.get("type"), entry.get("name")) if isinstance(entry, dict) else ("invalid", repr(entry))
        for entry in entries
    }
    expected = set(ENVIRONMENT_DEPLOYMENT_POLICIES[name])
    if live != expected:
        findings.append(
            f"{name} environment: deployment policies are {sorted(live, key=repr)!r}, expected {sorted(expected)!r}"
        )
    return findings


def _api_json(url: str, token: str) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _bool_from_text(value: str) -> bool:
    lowered = value.strip().lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    raise ValueError(f"expected boolean text, got {value!r}")


def _section_for_ruleset(text: str, name: str) -> str:
    marker = f"Ruleset name: `{name}`"
    start = text.find(marker)
    if start < 0:
        raise ValueError(f"could not find ruleset section for {name!r}")
    next_heading = text.find("\n## ", start + len(marker))
    return text[start:] if next_heading < 0 else text[start:next_heading]


def _required_checks(section: str, name: str) -> tuple[str, ...]:
    match = re.search(r"Required status checks:\n\n```text\n(.*?)\n```", section, re.DOTALL)
    if not match:
        raise ValueError(f"could not find required checks block for {name!r}")
    checks = tuple(line.removeprefix("- ").strip() for line in match.group(1).splitlines() if line.strip())
    if not checks:
        raise ValueError(f"required checks block for {name!r} is empty")
    return checks


def _other_properties(section: str, name: str) -> dict[str, str]:
    match = re.search(r"Other ruleset properties to preserve:\n\n```text\n(.*?)\n```", section, re.DOTALL)
    if not match:
        raise ValueError(f"could not find other-properties block for {name!r}")
    props: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        props[key.strip()] = value.strip()
    return props


def parse_expected_rulesets(runbook_text: str) -> dict[str, ExpectedRuleset]:
    expected: dict[str, ExpectedRuleset] = {}
    for name in ("develop-squash-only", "release-only"):
        section = _section_for_ruleset(runbook_text, name)
        ref_match = re.search(r"targets?\s+`([^`]+)`", section)
        if not ref_match:
            raise ValueError(f"could not find target ref for {name!r}")
        props = _other_properties(section, name)
        expected[name] = ExpectedRuleset(
            name=name,
            ref=ref_match.group(1),
            required_checks=_required_checks(section, name),
            strict_required_status_checks_policy=_bool_from_text(props["strict_required_status_checks_policy"]),
            required_linear_history=_bool_from_text(props["required_linear_history"]),
            non_fast_forward=_bool_from_text(props["non_fast_forward"]),
            deletion=props["deletion"].lower() == "blocked",
            bypass_actors_none=props["bypass_actors"].lower() == "(none)",
        )
    return expected


def _rule_by_type(ruleset: dict[str, Any], rule_type: str) -> dict[str, Any] | None:
    rules = ruleset.get("rules", [])
    if not isinstance(rules, list):
        return None
    for rule in rules:
        if isinstance(rule, dict) and rule.get("type") == rule_type:
            return rule
    return None


def _required_status_contexts(ruleset: dict[str, Any]) -> tuple[str, ...]:
    rule = _rule_by_type(ruleset, "required_status_checks")
    if not rule:
        return ()
    checks = rule.get("parameters", {}).get("required_status_checks", [])
    return tuple(check.get("context", "") for check in checks if check.get("context"))


def _strict_required_status_checks_policy(ruleset: dict[str, Any]) -> bool | None:
    rule = _rule_by_type(ruleset, "required_status_checks")
    if not rule:
        return None
    return rule.get("parameters", {}).get("strict_required_status_checks_policy")


def _ref_includes(ruleset: dict[str, Any]) -> tuple[str, ...]:
    ref_name = ruleset.get("conditions", {}).get("ref_name", {})
    return tuple(ref_name.get("include", []) or ())


def compare_ruleset(
    expected: ExpectedRuleset,
    live: dict[str, Any],
    *,
    require_bypass_actor_visibility: bool = False,
    enforce_review_rule: bool = DEVELOP_REVIEW_RULE_ENFORCED,
) -> list[str]:
    findings: list[str] = []
    if live.get("enforcement") != "active":
        findings.append(f"{expected.name}: enforcement is {live.get('enforcement')!r}, expected 'active'")
    if expected.ref not in _ref_includes(live):
        findings.append(f"{expected.name}: target refs {_ref_includes(live)!r}, expected {expected.ref!r}")

    live_checks = _required_status_contexts(live)
    if set(live_checks) != set(expected.required_checks):
        findings.append(
            f"{expected.name}: required checks {sorted(live_checks)!r}, expected {sorted(expected.required_checks)!r}"
        )
    live_strict = _strict_required_status_checks_policy(live)
    if live_strict != expected.strict_required_status_checks_policy:
        findings.append(
            f"{expected.name}: strict_required_status_checks_policy is {live_strict!r}, "
            f"expected {expected.strict_required_status_checks_policy!r}"
        )

    for rule_type, expected_present in [
        ("required_linear_history", expected.required_linear_history),
        ("non_fast_forward", expected.non_fast_forward),
        ("deletion", expected.deletion),
    ]:
        live_present = _rule_by_type(live, rule_type) is not None
        if live_present != expected_present:
            findings.append(f"{expected.name}: {rule_type} present={live_present!r}, expected {expected_present!r}")

    if expected.bypass_actors_none:
        if require_bypass_actor_visibility and "bypass_actors" not in live:
            findings.append(
                f"{expected.name}: bypass actors are not visible to this token; "
                "use RULESET_DRIFT_TOKEN with ruleset write/admin visibility"
            )
        else:
            bypass_actors = live.get("bypass_actors", []) or []
            if bypass_actors:
                actor_types = [actor.get("actor_type", "(unknown)") for actor in bypass_actors]
                findings.append(f"{expected.name}: bypass actors {actor_types!r}, expected none")

    if expected.name == "develop-squash-only":
        review_findings = review_enforcement_findings(extract_rules(live))
        if review_findings:
            if enforce_review_rule:
                findings.extend(review_findings)
            else:
                findings.extend(f"{WARNING_PREFIX}{finding}" for finding in review_findings)

    return findings


def _normalized_for_capture(ruleset: dict[str, Any]) -> dict[str, Any]:
    normalized = {key: value for key, value in ruleset.items() if key not in CAPTURE_IGNORED_FIELDS}
    rules = []
    for rule in normalized.get("rules") or []:
        parameters = rule.get("parameters")
        if rule.get("type") == "required_status_checks" and isinstance(parameters, dict):
            checks = {
                str(check.get("context")): {"integration_id": check.get("integration_id")}
                for check in parameters.get("required_status_checks") or []
            }
            rule = {**rule, "parameters": {**parameters, "required_status_checks": checks}}
        rules.append(rule)
    if "rules" in normalized:
        normalized["rules"] = rules
    return normalized


def _differing_fields(path: str, capture: Any, live: Any) -> list[str]:
    if isinstance(capture, dict) and isinstance(live, dict):
        findings: list[str] = []
        for key in sorted(capture.keys() | live.keys(), key=str):
            child = f"{path}.{key}" if path else str(key)
            if key not in live:
                findings.append(f"{child}: capture has {capture[key]!r}, live is missing")
            elif key not in capture:
                findings.append(f"{child}: capture is missing, live has {live[key]!r}")
            else:
                findings.extend(_differing_fields(child, capture[key], live[key]))
        return findings
    if isinstance(capture, list) and isinstance(live, list) and len(capture) == len(live):
        findings = []
        for index, (capture_item, live_item) in enumerate(zip(capture, live, strict=True)):
            findings.extend(_differing_fields(f"{path}[{index}]", capture_item, live_item))
        return findings
    if capture != live:
        return [f"{path}: capture has {capture!r}, live has {live!r}"]
    return []


def capture_comparison_findings(capture: dict[str, Any], live: dict[str, Any]) -> list[str]:
    findings = [
        f"{source} ruleset has no bypass_actors field; the token cannot see bypass actors"
        for source, document in (("capture", capture), ("live", live))
        if "bypass_actors" not in document
    ]
    if live.get("enforcement") != "active":
        findings.append(f"live enforcement is {live.get('enforcement')!r}, expected 'active'")
    findings.extend(_differing_fields("", _normalized_for_capture(capture), _normalized_for_capture(live)))
    bindings = {
        context: check["integration_id"]
        for rule in _normalized_for_capture(live).get("rules", [])
        if rule.get("type") == "required_status_checks"
        for context, check in rule["parameters"]["required_status_checks"].items()
    }
    if bindings.get("oracle-gate") != ORACLE_GATE_INTEGRATION_ID:
        findings.append(
            f"oracle-gate integration_id: live is {bindings.get('oracle-gate')!r}, expected {ORACLE_GATE_INTEGRATION_ID}"
        )
    return findings


def compare_capture_main(args: argparse.Namespace, token: str) -> int:
    try:
        capture = json.loads(args.compare_capture.read_text(encoding="utf-8"))
        live = _api_json(f"https://api.github.com/repos/{args.repo}/rulesets/{args.ruleset_id}", token)
    except Exception as exc:
        print(f"ERROR: capture comparison failed: {exc}", file=sys.stderr)
        return 1
    findings = capture_comparison_findings(capture, live)
    if findings:
        print(f"Ruleset {args.ruleset_id} differs from {args.compare_capture}:")
        for finding in findings:
            print(f"- {finding}")
        return 1
    print(f"Ruleset {args.ruleset_id} matches {args.compare_capture}.")
    return 0


def tag_creation_findings(
    all_live_rulesets: list[dict[str, Any]],
    *,
    enforce_tag_rule: bool = TAG_RULESET_ENFORCED,
    require_bypass_actor_visibility: bool = False,
) -> list[str]:
    findings: list[str] = []
    protection_findings = tag_protection_findings(
        all_live_rulesets,
        require_bypass_actor_visibility=require_bypass_actor_visibility,
    )
    if protection_findings:
        if enforce_tag_rule:
            findings.extend(protection_findings)
        else:
            findings.extend(f"{WARNING_PREFIX}{finding}" for finding in protection_findings)
    else:
        findings.extend(
            f"{WARNING_PREFIX}CONFIRM before enforcing: {advisory}"
            for advisory in tag_bypass_advisory(all_live_rulesets)
        )
    return findings


def _override_active(env: dict[str, str]) -> tuple[bool, str]:
    sha = env.get("RELEASE_READINESS_OVERRIDE_SHA", "").strip()
    reason = env.get("RELEASE_READINESS_OVERRIDE_REASON", "").strip()
    current_sha = env.get("GITHUB_SHA", "").strip()
    if sha and reason and sha == current_sha:
        return True, reason
    return False, ""


def _fetch_live_rulesets(repo: str, token: str) -> dict[str, dict[str, Any]]:
    base_url = f"https://api.github.com/repos/{repo}/rulesets"
    listing = _api_json(base_url, token)
    by_name: dict[str, dict[str, Any]] = {}
    for entry in listing:
        name = entry.get("name")
        ruleset_id = entry.get("id")
        if name and ruleset_id:
            by_name[name] = _api_json(f"{base_url}/{ruleset_id}", token)
    return by_name


def _fetch_environment(repo: str, token: str, name: str = PYPI_ENVIRONMENT) -> dict[str, Any]:
    return _api_json(f"https://api.github.com/repos/{repo}/environments/{name}", token)


def _fetch_environment_policies(repo: str, token: str, name: str) -> dict[str, Any]:
    return _api_json(f"https://api.github.com/repos/{repo}/environments/{name}/deployment-branch-policies", token)


def merge_queue_findings(live: dict[str, Any], name: str) -> list[str]:
    if _rule_by_type(live, "merge_queue") is None:
        return []
    return [f"{name}: a merge_queue rule is present; the approved develop policy has no merge queue"]


def blocking_findings(findings: list[str]) -> list[str]:
    return [finding for finding in findings if not finding.startswith(WARNING_PREFIX)]


def render_summary(findings: list[str], expected: dict[str, ExpectedRuleset]) -> str:
    blocking = blocking_findings(findings)
    warnings = [finding for finding in findings if finding.startswith(WARNING_PREFIX)]
    if blocking:
        lines = ["# Ruleset Drift - FAILED", "", *[f"- {finding}" for finding in blocking]]
    else:
        lines = ["# Ruleset Drift - OK", ""]
        for ruleset in expected.values():
            lines.append(f"- {ruleset.name}: required checks {', '.join(ruleset.required_checks)}")
        lines.append("- pypi environment: required reviewer joeharris76 (User id 57046)")
        lines.append("- pypi environment: deployments only from tag v*")
        lines.append("- test-pypi environment: deployments only from tag v* and branches develop and release")
    if warnings:
        lines += ["", "## Non-blocking warnings", "", *[f"- {finding}" for finding in warnings]]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument("--runbook", type=Path, default=DEFAULT_RUNBOOK)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", "BenchBox-dev/BenchBox"))
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""))
    parser.add_argument(
        "--token-stdin",
        action="store_true",
        help="read the GitHub token from stdin so it is never exposed in process arguments",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--require-bypass-actor-visibility",
        action="store_true",
        help="Fail if the GitHub API response does not expose bypass_actors.",
    )
    parser.add_argument(
        "--compare-capture",
        type=Path,
        help="compare the live develop ruleset with a saved ruleset JSON capture instead of the runbook",
    )
    parser.add_argument("--ruleset-id", type=int, default=DEVELOP_RULESET_ID)
    args = parser.parse_args(argv)

    if args.compare_capture:
        token = sys.stdin.read().strip() if args.token_stdin else args.token
        if not token:
            print("ERROR: capture comparison requires --token or --token-stdin.", file=sys.stderr)
            return 1
        return compare_capture_main(args, token)

    override, reason = _override_active(os.environ)
    try:
        expected = parse_expected_rulesets(args.runbook.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"ERROR: failed to parse ruleset runbook: {exc}", file=sys.stderr)
        return 1
    if override:
        payload = {
            "status": "override",
            "reason": reason,
            "rulesets": sorted(expected),
        }
        if args.output:
            args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"Ruleset drift override active: {reason}")
        return 0
    token = args.token
    if args.token_stdin:
        token = sys.stdin.read().strip()
    if not token:
        print("ERROR: ruleset drift check requires RULESET_DRIFT_TOKEN, --token, or --token-stdin.", file=sys.stderr)
        return 1

    try:
        live_by_name = _fetch_live_rulesets(args.repo, token)
        live_environments = {
            name: _fetch_environment(args.repo, token, name) for name in ENVIRONMENT_DEPLOYMENT_POLICIES
        }
        live_policies = {
            name: _fetch_environment_policies(args.repo, token, name) for name in ENVIRONMENT_DEPLOYMENT_POLICIES
        }
        live_pypi_environment = live_environments[PYPI_ENVIRONMENT]
    except Exception as exc:
        if args.output:
            args.output.write_text(
                json.dumps({"status": "error", "error": str(exc)}, indent=2) + "\n",
                encoding="utf-8",
            )
        print(f"ERROR: governance drift check failed while querying GitHub: {exc}", file=sys.stderr)
        return 1

    findings: list[str] = []
    for name, expected_ruleset in expected.items():
        live = live_by_name.get(name)
        if live is None:
            findings.append(f"{name}: live ruleset is missing")
            continue
        findings.extend(
            compare_ruleset(
                expected_ruleset,
                live,
                require_bypass_actor_visibility=args.require_bypass_actor_visibility,
            )
        )
    develop_live = live_by_name.get("develop-squash-only")
    if develop_live is not None:
        findings.extend(merge_queue_findings(develop_live, "develop-squash-only"))
    findings.extend(
        tag_creation_findings(
            list(live_by_name.values()),
            require_bypass_actor_visibility=args.require_bypass_actor_visibility,
        )
    )
    findings.extend(environment_protection_findings(live_pypi_environment))
    for name, live_environment in live_environments.items():
        findings.extend(deployment_policy_findings(name, live_environment, live_policies[name]))

    blocking = blocking_findings(findings)
    summary = render_summary(findings, expected)
    if args.output:
        args.output.write_text(
            json.dumps(
                {
                    "status": "failed" if blocking else "ok",
                    "findings": findings,
                    "blocking_findings": blocking,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    print(summary)
    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main())
