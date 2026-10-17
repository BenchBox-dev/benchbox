#!/usr/bin/env python3

from __future__ import annotations

import argparse
import fnmatch
import json
import subprocess
import sys
from pathlib import Path
from typing import Iterable, cast

from skill_sync_ci_policy import compare_repository_manifest

CLI_DESCRIPTION = "Classify changed paths for the develop PR CI umbrella."

DEFAULT_RULES = Path(".github/path-filters.yml")
CORE_RULE_KEYS = {"safe-content", "content-guard", "code-ci"}
LIST_NAMES = {
    "changed": "changed_paths",
    "content": "content_guard_paths",
    "code": "code_paths",
    "unknown": "unknown_paths",
    "yaml": "yaml_paths",
    "markdown": "markdown_paths",
    "todo": "todo_paths",
    "docs": "docs_paths",
}


def normalize_path(path: str) -> str:
    normalized = path.strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def unquote_yaml_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def load_rules(path: Path) -> dict[str, list[str]]:
    rules: dict[str, list[str]] = {}
    current_key: str | None = None

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if not line.startswith(" ") and line.endswith(":"):
            current_key = line[:-1].strip()
            rules[current_key] = []
            continue
        if current_key and line.lstrip().startswith("- "):
            rules[current_key].append(unquote_yaml_scalar(line.lstrip()[2:]))

    missing = {"safe-content", "content-guard", "code-ci"} - set(rules)
    if missing:
        raise ValueError(f"{path} is missing required filter keys: {', '.join(sorted(missing))}")
    return rules


def pattern_matches(path: str, pattern: str) -> bool:
    path = normalize_path(path)
    pattern = normalize_path(pattern)
    if pattern.endswith("/**"):
        prefix = pattern[:-3]
        return path == prefix or path.startswith(f"{prefix}/")
    if "/" not in pattern:
        return "/" not in path and fnmatch.fnmatchcase(path, pattern)
    return fnmatch.fnmatchcase(path, pattern)


def matches_any(path: str, patterns: Iterable[str]) -> bool:
    return any(pattern_matches(path, pattern) for pattern in patterns)


def extra_group_keys(rules: dict[str, list[str]]) -> list[str]:
    groups = [key for key in rules if key not in CORE_RULE_KEYS]
    return groups


def git_changed_paths(base_ref: str) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=ACDMRT", f"{base_ref}...HEAD"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    return [normalize_path(line) for line in result.stdout.splitlines() if normalize_path(line)]


def stdin_changed_paths() -> list[str]:
    if sys.stdin.isatty():
        return []
    return [normalize_path(line) for line in sys.stdin.read().splitlines() if normalize_path(line)]


def ordered_unique(paths: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            result.append(path)
    return result


def classify_paths(
    changed_paths: list[str],
    rules: dict[str, list[str]],
    *,
    forced_code_paths: Iterable[str] = (),
    manifest_decision_reason: str | None = None,
) -> dict[str, object]:
    safe_patterns = rules["safe-content"]
    content_patterns = rules["content-guard"]
    code_patterns = rules["code-ci"]
    specialized_patterns = rules.get("skill-integrity", [])
    forced = set(forced_code_paths)

    safe_paths = [path for path in changed_paths if matches_any(path, safe_patterns)]
    content_paths = [path for path in changed_paths if matches_any(path, content_patterns)]
    specialized_paths = [path for path in changed_paths if matches_any(path, specialized_patterns)]
    explicit_code_paths = [path for path in changed_paths if matches_any(path, code_patterns)]
    code_paths = [
        path
        for path in changed_paths
        if path in forced or path in explicit_code_paths or (path not in safe_paths and path not in specialized_paths)
    ]
    unknown_paths = [path for path in code_paths if path not in forced and not matches_any(path, code_patterns)]

    skill_integrity_needed = bool(specialized_paths)
    safe_content_only = bool(changed_paths) and not code_paths and not skill_integrity_needed
    needs_code_ci = not bool(changed_paths) or bool(code_paths)
    skill_integrity_only = bool(changed_paths) and skill_integrity_needed and not needs_code_ci
    markdown_paths = [path for path in content_paths if path.endswith(".md")]
    yaml_paths = [path for path in content_paths if path.endswith((".yaml", ".yml"))]
    todo_paths = [path for path in yaml_paths if path.startswith("_project/TODO/") or path.startswith("_project/DONE/")]
    docs_paths = [path for path in content_paths if path.startswith("docs/")]

    decision: dict[str, object] = {
        "changed_paths": changed_paths,
        "safe_content_paths": safe_paths,
        "content_guard_paths": content_paths,
        "code_paths": code_paths,
        "unknown_paths": unknown_paths,
        "yaml_paths": yaml_paths,
        "markdown_paths": markdown_paths,
        "todo_paths": todo_paths,
        "docs_paths": docs_paths,
        "safe_content_only": safe_content_only,
        "needs_code_ci": needs_code_ci,
        "content_guard_needed": bool(content_paths),
        "skill_integrity_only": skill_integrity_only,
        "specialized_paths": specialized_paths,
        "forced_code_paths": [path for path in changed_paths if path in forced],
        "manifest_decision_reason": manifest_decision_reason,
        "estimated_runner_minutes_saved": 5 if not needs_code_ci else 0,
    }

    group_keys = extra_group_keys(rules)
    for group in group_keys:
        group_id = group.replace("-", "_")
        matched = [path for path in changed_paths if matches_any(path, rules[group])]
        decision[f"{group_id}_paths"] = matched
        decision[f"{group_id}_needed"] = bool(matched)
    decision["extra_group_ids"] = [group.replace("-", "_") for group in group_keys]

    return decision


def bool_text(value: object) -> str:
    return "true" if bool(value) else "false"


def path_list(decision: dict[str, object], key: str) -> list[str]:
    return cast(list[str], decision[key])


def write_github_output(path: Path, decision: dict[str, object]) -> None:
    lines = [
        f"safe-content-only={bool_text(decision['safe_content_only'])}",
        f"needs-code-ci={bool_text(decision['needs_code_ci'])}",
        f"content-guard-needed={bool_text(decision['content_guard_needed'])}",
        f"skill-integrity-only={bool_text(decision.get('skill_integrity_only'))}",
        f"estimated-runner-minutes-saved={decision['estimated_runner_minutes_saved']}",
    ]
    for group_id in cast(list[str], decision.get("extra_group_ids", [])):
        output_name = group_id.replace("_", "-")
        lines.append(f"{output_name}-needed={bool_text(decision[f'{group_id}_needed'])}")
    for key, decision_key in [
        ("changed-paths", "changed_paths"),
        ("content-paths", "content_guard_paths"),
        ("code-paths", "code_paths"),
        ("unknown-paths", "unknown_paths"),
    ]:
        value = "\n".join(path_list(decision, decision_key))
        lines.append(f"{key}<<EOF_{key}")
        lines.append(value)
        lines.append(f"EOF_{key}")
    with path.open("a", encoding="utf-8") as output:
        output.write("\n".join(lines))
        output.write("\n")


def write_summary(path: Path, decision: dict[str, object]) -> None:
    changed_paths = path_list(decision, "changed_paths")
    code_paths = path_list(decision, "code_paths")
    content_paths = path_list(decision, "content_guard_paths")
    unknown_paths = path_list(decision, "unknown_paths")
    with path.open("a", encoding="utf-8") as summary:
        summary.write("### Develop PR Path Decision\n\n")
        summary.write(f"- Changed paths: {len(changed_paths)}\n")
        summary.write(f"- Content paths: {len(content_paths)}\n")
        summary.write(f"- Code paths: {len(code_paths)}\n")
        summary.write(f"- Unknown paths: {len(unknown_paths)}\n")
        summary.write(f"- Safe content only: `{bool_text(decision['safe_content_only'])}`\n")
        summary.write(f"- Skill integrity only: `{bool_text(decision.get('skill_integrity_only'))}`\n")
        summary.write(f"- Needs code CI: `{bool_text(decision['needs_code_ci'])}`\n")
        selected = []
        if decision["content_guard_needed"]:
            selected.append("content")
        if decision.get("skill_integrity_needed"):
            selected.append("skill-integrity")
        if decision["needs_code_ci"]:
            selected.append("product-code")
        summary.write(f"- Selected lanes: `{', '.join(selected) or 'none'}`\n")
        summary.write(f"- Estimated runner minutes saved: `{decision['estimated_runner_minutes_saved']}`\n")


def write_lists(directory: Path, decision: dict[str, object]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    list_names = dict(LIST_NAMES)
    for group_id in cast(list[str], decision.get("extra_group_ids", [])):
        list_names[group_id] = group_id if group_id.endswith("_paths") else f"{group_id}_paths"
    for filename, decision_key in list_names.items():
        with (directory / f"{filename}.txt").open("w", encoding="utf-8") as list_file:
            for path in path_list(decision, decision_key):
                list_file.write(f"{path}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument("--rules", type=Path, default=DEFAULT_RULES)
    parser.add_argument("--base-ref", help="Git ref to diff against, for example origin/develop")
    parser.add_argument("--changed-file", type=Path, help="Read changed paths from a newline-delimited file")
    parser.add_argument("--json-in", type=Path, help="Read an existing decision JSON instead of classifying paths")
    parser.add_argument("--json-out", type=Path, help="Write the full decision as JSON")
    parser.add_argument("--github-output", type=Path, help="Append GitHub Actions outputs")
    parser.add_argument("--summary", type=Path, help="Append a GitHub step summary")
    parser.add_argument("--lists-dir", type=Path, help="Write changed/content/code helper lists")
    parser.add_argument(
        "--check",
        choices=["needs-code-ci", "safe-content-only", "content-guard-needed", "skill-integrity-only"],
        help="Exit 0 when the named decision is true, otherwise exit 1",
    )
    args = parser.parse_args()

    if args.json_in:
        decision = json.loads(args.json_in.read_text(encoding="utf-8"))
    else:
        rules = load_rules(args.rules)
        if args.changed_file:
            changed_paths = [
                normalize_path(line)
                for line in args.changed_file.read_text(encoding="utf-8").splitlines()
                if normalize_path(line)
            ]
        elif args.base_ref:
            changed_paths = git_changed_paths(args.base_ref)
        else:
            changed_paths = stdin_changed_paths()
        changed_paths = ordered_unique(changed_paths)
        forced_code_paths: list[str] = []
        manifest_reason: str | None = None
        manifest_base_sha: str | None = None
        if "skill-sync.conf" in changed_paths:
            if not args.base_ref:
                forced_code_paths.append("skill-sync.conf")
                manifest_reason = "manifest_base_ref_missing"
            else:
                resolved = subprocess.run(
                    ["git", "rev-parse", "--verify", f"{args.base_ref}^{{commit}}"],
                    check=False,
                    text=True,
                    capture_output=True,
                )
                if resolved.returncode != 0:
                    forced_code_paths.append("skill-sync.conf")
                    manifest_reason = "manifest_base_ref_unresolvable"
                else:
                    base_sha = resolved.stdout.strip()
                    manifest_base_sha = base_sha
                    manifest_decision = compare_repository_manifest(base_sha)
                    manifest_reason = manifest_decision.reason
                    if not manifest_decision.narrow_eligible:
                        forced_code_paths.append("skill-sync.conf")
        decision = classify_paths(
            changed_paths,
            rules,
            forced_code_paths=forced_code_paths,
            manifest_decision_reason=manifest_reason,
        )
        decision["manifest_base_sha"] = manifest_base_sha

    if args.json_out:
        args.json_out.write_text(json.dumps(decision, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.github_output:
        write_github_output(args.github_output, decision)
    if args.summary:
        write_summary(args.summary, decision)
    if args.lists_dir:
        write_lists(args.lists_dir, decision)

    print(json.dumps(decision, indent=2, sort_keys=True))
    if args.check:
        decision_key = args.check.replace("-", "_")
        return 0 if decision[decision_key] else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
