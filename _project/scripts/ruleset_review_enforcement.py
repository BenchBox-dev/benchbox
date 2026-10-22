#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import json
import sys
from pathlib import Path
from typing import Any

ORACLE_REVIEW_CONTEXT = "oracle-review"
ORACLE_GATE_CONTEXT = "oracle-gate"
ORACLE_GATE_INTEGRATION_ID = 5201041


def extract_rules(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [rule for rule in payload if isinstance(rule, dict)]
    if isinstance(payload, dict):
        return [rule for rule in payload.get("rules", []) if isinstance(rule, dict)]
    return []


def _pull_request_parameters(rules: list[dict[str, Any]]) -> dict[str, Any] | None:
    for rule in rules:
        if rule.get("type") == "pull_request":
            params = rule.get("parameters")
            return params if isinstance(params, dict) else {}
    return None


def _is_oracle_check(check: dict[str, Any]) -> bool:
    if check["context"] == ORACLE_REVIEW_CONTEXT:
        return True
    return check["context"] == ORACLE_GATE_CONTEXT and check.get("integration_id") == ORACLE_GATE_INTEGRATION_ID


def review_enforcement_findings(rules: list[dict[str, Any]]) -> list[str]:
    params = _pull_request_parameters(rules)
    if params is None:
        return ["develop ruleset has no pull_request rule: a PR can squash-auto-merge without review thread resolution"]
    findings: list[str] = []
    if params.get("required_review_thread_resolution") is not True:
        findings.append(
            f"required_review_thread_resolution={params.get('required_review_thread_resolution', False)} (need true)"
        )
    status_rule = next((rule for rule in rules if rule.get("type") == "required_status_checks"), None)
    if status_rule is None:
        findings.append(
            "develop ruleset has no required_status_checks rule: oracle-review or oracle-gate must be required"
        )
        return findings
    status_params = status_rule.get("parameters")
    if not isinstance(status_params, dict):
        findings.append("required_status_checks parameters are malformed: oracle enforcement is unverified")
        return findings
    checks = status_params.get("required_status_checks")
    if not isinstance(checks, list) or any(
        not isinstance(check, dict) or not isinstance(check.get("context"), str) or not check["context"].strip()
        for check in checks
    ):
        findings.append("required_status_checks are malformed: oracle enforcement is unverified")
    elif not any(_is_oracle_check(check) for check in checks):
        findings.append("required_status_checks must include oracle-review or oracle-gate scoped to the oracle App")
    return findings


def is_review_enforced(rules: list[dict[str, Any]]) -> bool:
    return not review_enforcement_findings(rules)


TAG_RULESET_ENFORCED = True

TAG_REF_PATTERN = "refs/tags/v*"


def _tag_glob_covers(pattern: str) -> bool:
    tokens: list[str] = []
    index = 0
    while index < len(pattern):
        if pattern[index] == "[":
            closing = index + 1
            if closing < len(pattern) and pattern[closing] == "!":
                closing += 1
            if closing < len(pattern) and pattern[closing] == "]":
                closing += 1
            while closing < len(pattern) and pattern[closing] != "]":
                closing += 1
            if closing < len(pattern):
                tokens.append(pattern[index : closing + 1])
                index = closing + 1
                continue
        token = pattern[index]
        if token != "*" or not tokens or tokens[-1] != "*":
            tokens.append(token)
        index += 1

    def closure(states: set[int]) -> set[int]:
        expanded = set(states)
        pending = list(states)
        while pending:
            state = pending.pop()
            if state < len(tokens) and tokens[state] == "*" and state + 1 not in expanded:
                expanded.add(state + 1)
                pending.append(state + 1)
        return expanded

    states = closure({0})
    for char in TAG_REF_PATTERN.removesuffix("*"):
        following: set[int] = set()
        for state in states:
            if state >= len(tokens):
                continue
            token = tokens[state]
            if token == "*":
                following.add(state)
            elif token == "?" or fnmatch.fnmatchcase(char, token):
                following.add(state + 1)
        states = closure(following)

    return any(state < len(tokens) and all(token == "*" for token in tokens[state:]) for state in states)


def tag_protection_findings(
    rulesets: list[dict[str, Any]], *, require_bypass_actor_visibility: bool = False
) -> list[str]:
    tag_rulesets = [rs for rs in rulesets if isinstance(rs, dict) and rs.get("target") == "tag"]
    if not tag_rulesets:
        return [
            "no ruleset with target='tag' exists: any collaborator with push access can "
            f"create a {TAG_REF_PATTERN} tag on a main-ancestor commit and reach release.yml's "
            "publish path with no human gate"
        ]
    problems: list[str] = []
    for ruleset in tag_rulesets:
        name = ruleset.get("name", "(unnamed)")
        issues: list[str] = []
        if ruleset.get("enforcement") != "active":
            issues.append(f"enforcement={ruleset.get('enforcement')!r} (need 'active')")
        ref_name = (ruleset.get("conditions") or {}).get("ref_name") or {}
        include = tuple(ref_name.get("include") or ())
        exclude = tuple(ref_name.get("exclude") or ())
        if not any(pattern == "~ALL" or _tag_glob_covers(pattern) for pattern in include):
            issues.append(f"ref include={include!r} does not cover {TAG_REF_PATTERN}")
        elif any(pattern == "~ALL" or _tag_glob_covers(pattern) for pattern in exclude):
            issues.append(f"ref exclude={exclude!r} negates coverage of {TAG_REF_PATTERN}")
        rule_types = {rule.get("type") for rule in ruleset.get("rules") or [] if isinstance(rule, dict)}
        for required_rule in ("creation", "update", "deletion"):
            if required_rule not in rule_types:
                issues.append(f"no '{required_rule}' rule")
        if require_bypass_actor_visibility and "bypass_actors" not in ruleset:
            issues.append("bypass actors are not visible to this token")
        elif ruleset.get("bypass_actors") == []:
            issues.append(
                "bypass_actors is empty -- `make release-finalize`'s `git push origin "
                "v$(VERSION)` would be blocked with no exception for the release-finalize "
                "identity; add a bypass actor for that identity before enforcing"
            )
        if not issues:
            return []
        problems.append(f"{name}: " + "; ".join(issues))
    return [
        f"no active tag ruleset covers {TAG_REF_PATTERN} with creation, update and deletion rules -- "
        + " | ".join(problems)
    ]


def tag_bypass_advisory(rulesets: list[dict[str, Any]]) -> list[str]:
    if tag_protection_findings(rulesets):
        return []
    for ruleset in rulesets:
        if not isinstance(ruleset, dict) or ruleset.get("target") != "tag":
            continue
        if tag_protection_findings([ruleset]):
            continue
        actors = ruleset.get("bypass_actors") or []
        if actors:
            rendered = ", ".join(
                f"{a.get('actor_type', '?')}:{a.get('actor_id', '?')}({a.get('bypass_mode', '?')})"
                for a in actors
                if isinstance(a, dict)
            )
            return [
                f"{ruleset.get('name', '(unnamed)')} has bypass_actors [{rendered}] -- confirm these "
                "are the release-finalize identity ONLY (not a broad Write/Admin role) before "
                "enforcing; a wide bypass leaves v* tag creation open"
            ]
        return []
    return []


def is_tag_creation_protected(rulesets: list[dict[str, Any]]) -> bool:
    return not tag_protection_findings(rulesets)


def _load_rulesets(raw_source: str) -> list[dict[str, Any]]:
    raw = sys.stdin.read() if raw_source == "-" else Path(raw_source).read_text(encoding="utf-8")
    payload = json.loads(raw)
    if isinstance(payload, list):
        return [rs for rs in payload if isinstance(rs, dict)]
    if isinstance(payload, dict):
        return [payload]
    return []


def _fetch_branch_rules(repo: str, branch: str, token: str) -> list[dict[str, Any]]:
    import urllib.request

    url = f"https://api.github.com/repos/{repo}/rules/branches/{branch}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return extract_rules(json.loads(response.read().decode("utf-8")))


def _load_rules(args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.rules_file:
        raw = sys.stdin.read() if args.rules_file == "-" else Path(args.rules_file).read_text(encoding="utf-8")
        return extract_rules(json.loads(raw))
    if args.token:
        return _fetch_branch_rules(args.repo, args.branch, args.token)
    raise SystemExit(
        "Provide --rules-file (e.g. `gh api repos/<owner>/<repo>/rules/branches/develop | "
        "ruleset_review_enforcement.py --rules-file -`) or --token to fetch live."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rules-file", help="Path to a JSON rules/ruleset payload, or '-' to read stdin.")
    parser.add_argument("--repo", default="BenchBox-dev/BenchBox", help="owner/repo for live fetch.")
    parser.add_argument("--branch", default="develop", help="Branch whose ruleset to check.")
    parser.add_argument("--token", default="", help="Ruleset-read token for live fetch.")
    parser.add_argument(
        "--rulesets-file",
        help=(
            "Path to a JSON array of FULL ruleset objects (or '-' for stdin) to check "
            "v* tag-creation protection; e.g. "
            "`gh api repos/<owner>/<repo>/rulesets --jq '[.[] | .id] | map(...)'` -- see "
            "docs/internal/repo-admin-settings.md for the exact fetch."
        ),
    )
    args = parser.parse_args(argv)

    if args.rulesets_file:
        rulesets = _load_rulesets(args.rulesets_file)
        tag_findings = tag_protection_findings(rulesets)
        if not tag_findings:
            print("# Tag-creation ruleset - OK")
            print(f"- {TAG_REF_PATTERN} creation restricted by an active tag ruleset")
            for advisory in tag_bypass_advisory(rulesets):
                print(f"- CONFIRM before enforcing: {advisory}")
            return 0
        if TAG_RULESET_ENFORCED:
            print("# Tag-creation ruleset - FAILED")
            for finding in tag_findings:
                print(f"- {finding}")
            return 1
        print("# Tag-creation ruleset - WARNING (non-blocking, enforcement override)")
        for finding in tag_findings:
            print(f"- WARNING (non-blocking): {finding}")
        return 0

    rules = _load_rules(args)
    findings = review_enforcement_findings(rules)
    if findings:
        print(f"# Ruleset review enforcement ({args.branch}) - FAILED")
        for finding in findings:
            print(f"- {finding}")
        return 1
    print(f"# Ruleset review enforcement ({args.branch}) - OK")
    print("- oracle-review or App-scoped oracle-gate required")
    print("- review thread resolution required")
    return 0


if __name__ == "__main__":
    sys.exit(main())
