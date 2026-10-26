from __future__ import annotations

import argparse
import ast
import codecs
import hashlib
import io
import json
import os
import re
import stat
import subprocess
import sys
import tokenize
from collections import Counter
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from check_comment_cleanup_scope import (
    COMMENT_POLICY_DIRECTIVES,
    COMMENT_POLICY_ENFORCEMENT_MODES,
    POLICY_PATH as SCOPE_POLICY_PATH,
    _extract_version_from_path,
    _parse_version,
    check_ratchet,
    comment_policy_matches as matches,
    directive_suppresses,
    immutable_external_ownership,
    is_wheel_bump as _is_wheel_bump,
    load_comment_policy as load_policy,
    load_policy_bytes as load_scope_policy_bytes,
    oracle_review_covers,
    validate_comment_policy_path as validate_path,
    verify_bound_wheel,
)
from comment_syntax import Finding, language, scan, source_language

DIRECTIVES = COMMENT_POLICY_DIRECTIVES
ENFORCEMENT_MODES = COMMENT_POLICY_ENFORCEMENT_MODES

POLICY_PATH = "quality/comment-policy.json"
LAUNCHER_PATH = "scripts/run_comment_policy.py"
EXPIRY_WARNING_DAYS = 30
BOOTSTRAP_BASE = "ed5c263c513ba65499f4918d3a7de607f280c65b"


def git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.PIPE)


def exception_identity(entry: dict) -> tuple[str, str, str, str, str, str]:
    return (
        entry["path"],
        entry["symbol"],
        entry["text"],
        entry["kind"],
        entry.get("payload", ""),
        entry.get("finding_kind", "comment"),
    )


def expired_exception_identities(policy: dict) -> set[tuple[str, str, str, str, str, str]]:
    return {
        exception_identity(entry)
        for entry in policy["exceptions"]
        if entry["kind"] == "directive" and "expires" in entry and date.fromisoformat(entry["expires"]) < date.today()
    }


def expiring_policy_warnings(policy: dict, today: date | None = None) -> list[str]:
    today = today or date.today()
    horizon = today + timedelta(days=EXPIRY_WARNING_DAYS)
    by_date: Counter = Counter(
        entry["expires"]
        for entry in policy["exceptions"]
        if entry["kind"] == "directive"
        and "expires" in entry
        and today <= date.fromisoformat(entry["expires"]) <= horizon
    )
    return [
        f"{count} directive exception(s) expire on {expires}; renew or remove them through an oracle-reviewed policy change"
        for expires, count in sorted(by_date.items())
    ]


def expired_policy_findings(policy: dict) -> list[Finding]:
    expired = expired_exception_identities(policy)
    return [
        Finding(
            POLICY_PATH,
            1,
            "policy-error",
            f"expired directive exception: {entry['path']} {entry['text']}",
        )
        for entry in policy["exceptions"]
        if (exception_identity(entry)) in expired
    ]


def revision_bytes(root: Path, path: str, staged: bool) -> bytes | None:
    try:
        return git(root, "show", f":{path}") if staged else (root / path).read_bytes()
    except (OSError, subprocess.CalledProcessError):
        return None


def verify_wheel_bumps(
    root: Path, policy: dict, baseline: dict, staged: bool = False, base: str | None = None
) -> set[str]:
    verified = set()
    for entry in policy["external"]:
        if entry in baseline["external"]:
            continue
        matched_base = [b for b in baseline["external"] if _is_wheel_bump(entry, b)]
        if matched_base:
            base_v = _extract_version_from_path(matched_base[0]["path"])
            cand_v = _extract_version_from_path(entry["path"])
            if _parse_version(cand_v) <= _parse_version(base_v):
                raise ValueError(f"wheel bump version {cand_v} must be strictly greater than base {base_v}")
            verify_bound_wheel(root, entry, cand_v, staged, base=base)
            verified.add(entry["path"])
    return verified


def ratchet_against_base(root: Path, policy: dict, base: str, staged: bool) -> tuple[dict, set[str]]:
    baseline = load_policy(git(root, "show", f"{base}:{POLICY_PATH}"))
    check_ratchet(policy, baseline)
    return baseline, verify_wheel_bumps(root, policy, baseline, staged, base=base)


def with_bumped_wheels(exclusions: set[str] | None, bumped: set[str]) -> set[str] | None:
    return None if exclusions is None else exclusions | bumped


def bootstrap_base_allowed(root: Path, base: str) -> bool:
    contains_rollout = (
        subprocess.run(
            ["git", "-C", str(root), "merge-base", "--is-ancestor", BOOTSTRAP_BASE, base], capture_output=True
        ).returncode
        == 0
    )
    installed = any(
        subprocess.run(["git", "-C", str(root), "cat-file", "-e", f"{base}:{path}"], capture_output=True).returncode
        == 0
        for path in ("scripts/run_comment_policy.py", "quality/comment-policy.json")
    )
    return contains_rollout and not installed


def same_commit_fixture(entry: dict, changed: set[str]) -> bool:
    return entry["kind"] == "fixture" and entry.get("count", 1) == 1 and entry["path"] in changed


def require_local_staged_check(args: argparse.Namespace) -> None:
    if not args.accept_same_commit_fixtures:
        return
    if not (args.staged and args.base):
        raise ValueError("same-commit fixture acceptance applies only to a staged comparison with a base")
    if os.environ.get("GITHUB_ACTIONS") == "true":
        raise ValueError("same-commit fixture acceptance is not available in CI")


def expiry_only_change(entry: dict, baseline: dict) -> bool:
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return False
    identity = exception_identity(entry)
    for base in baseline["exceptions"]:
        if exception_identity(base) != identity:
            continue
        if {k: v for k, v in base.items() if k != "expires"} != {k: v for k, v in entry.items() if k != "expires"}:
            return False
        if "expires" not in entry:
            return entry["kind"] == "directive" and not directive_suppresses(entry["text"])
        if "expires" not in base:
            return False
        try:
            return date.fromisoformat(entry["expires"]) > date.fromisoformat(base["expires"])
        except ValueError:
            return False
    return False


def counted_exceptions(
    policy: dict, baseline: dict, reviewed: bool, same_commit: bool, changed: set[str]
) -> list[dict]:
    return [
        e
        for e in policy["exceptions"]
        if e in baseline["exceptions"]
        or reviewed
        or (same_commit and (same_commit_fixture(e, changed) or expiry_only_change(e, baseline)))
    ]


def allowed(
    finding: Finding,
    policy: dict,
    source: str,
    budget: Counter | None = None,
    expired: set[tuple[str, str, str, str, str, str]] | None = None,
) -> bool:
    text = finding.text
    if finding.kind == "comment" and not finding.symbol:
        if (
            source_language(finding.path, source) in {"python", "bash", "javascript", "unsupported"}
            and finding.line == 1
            and re.fullmatch(
                r"#!(?:/usr/bin/env (?:bash|sh|zsh|ksh|python3|node)|/(?:bin|usr/bin)/(?:bash|sh|zsh|ksh|python3|node))",
                text,
            )
        ):
            return True
        cookie = re.fullmatch(r"# (?:-\*- )?coding[:=] ?([\w.-]+)(?: -\*-)?", text)
        if (
            source_language(finding.path, source) == "python"
            and cookie
            and finding.line <= 2
            and (finding.line == 1 or not source.splitlines()[0].strip() or source.splitlines()[0].startswith("#"))
        ):
            return codecs.lookup(cookie.group(1)).name != "utf-8"
    for index, entry in enumerate(policy["exceptions"]):
        identity = exception_identity(entry)
        if (
            identity not in (expired if expired is not None else expired_exception_identities(policy))
            and finding.path == entry["path"]
            and finding.symbol == entry["symbol"]
            and text == entry["text"]
            and finding.kind == entry.get("finding_kind", "comment")
            and (entry["kind"] != "fixture" or finding.payload == entry["payload"])
            and (budget is None or budget[index] > 0)
        ):
            if budget is not None:
                budget[index] -= 1
            return True
    return False


def decode(path: str, raw: bytes) -> str:
    if language(path) == "python" or re.match(rb"#![^\r\n]*\bpython[0-9.]*\b", raw):
        encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
        return raw.decode(encoding)
    return raw.decode("utf-8-sig")


def develop_merge_base(root: Path) -> str | None:
    try:
        return git(root, "merge-base", "HEAD", "origin/develop").decode().strip()
    except subprocess.CalledProcessError:
        return None


def ownership_base(root: Path, mode: str, comparison: str | None, requested: str | None) -> str | None:
    if comparison:
        if requested and requested != comparison:
            raise ValueError("the ownership base must match the comparison base")
        return comparison
    anchor = develop_merge_base(root)
    if requested:
        if anchor is None:
            raise ValueError("an explicit ownership base needs origin/develop to be anchored to its history")
        if (
            subprocess.run(
                ["git", "-C", str(root), "merge-base", "--is-ancestor", requested, anchor], capture_output=True
            ).returncode
            != 0
        ):
            raise ValueError("ownership base must be an ancestor of the merge-base with origin/develop")
        return requested
    if mode == "strict":
        if anchor is None:
            raise ValueError(
                "strict mode derives its ownership base from the merge-base with origin/develop, "
                "which is unavailable; fetch origin/develop or pass --ownership-base"
            )
        return anchor
    return None


def candidate_scope_notices(root: Path, staged: bool) -> list[dict[str, Any]] | None:
    try:
        raw = git(root, "show", f":{SCOPE_POLICY_PATH}") if staged else (root / SCOPE_POLICY_PATH).read_bytes()
        notices = load_scope_policy_bytes(raw)["notices"]
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        subprocess.CalledProcessError,
    ):
        return None
    return notices if isinstance(notices, list) else None


def ownership_exclusions(
    root: Path, base: str | None, staged: bool, candidate_notices: list[dict[str, Any]] | None = None
) -> set[str] | None:
    if base is None:
        return None
    if not re.fullmatch(r"[a-f0-9]{40}", base):
        raise ValueError("ownership base must be a full commit SHA")
    git(root, "cat-file", "-e", f"{base}^{{commit}}")
    git(root, "merge-base", "--is-ancestor", base, "HEAD")
    trusted_external = load_policy(git(root, "show", f"{base}:{POLICY_PATH}"))["external"]
    snapshot = immutable_external_ownership(root, base, trusted_external, staged, candidate_notices)
    if snapshot is None:
        return None
    excluded, notices = snapshot
    for notice in notices:
        try:
            raw = git(root, "show", f":{notice['path']}") if staged else (root / notice["path"]).read_bytes()
            start, end = notice["byte_start"], notice["byte_end"]
            retained = notice["retained_sha256"]
            whole_file = notice["whole_file"]
            blob = notice["blob_sha256"]
        except (KeyError, TypeError) as exc:
            raise ValueError(f"protected external notice entry is malformed: {exc}") from exc
        if len(raw) < end or hashlib.sha256(raw[start:end]).hexdigest() != retained:
            raise ValueError(f"protected external notice changed: {notice['path']}")
        if whole_file:
            if hashlib.sha256(raw).hexdigest() != blob:
                raise ValueError(f"protected external notice blob changed: {notice['path']}")
            excluded.add(notice["path"])
    return excluded


def scan_sources(root: Path, sources: dict[str, bytes], policy: dict) -> list[Finding]:
    decoded = {}
    errors = []
    for path, raw in sources.items():
        ownership_members = policy.get("ownership_excluded_members")
        if ownership_members is not None:
            if path in ownership_members:
                continue
        else:
            excluded = any(matches(path, e["path"]) for e in policy["external"])
            if excluded and ("external_members" not in policy or path in policy["external_members"]):
                continue
        if language(path) or raw.startswith(b"#!"):
            try:
                decoded[path] = decode(path, raw)
            except (UnicodeError, SyntaxError) as exc:
                errors.append(Finding(path, 1, "coverage-error", str(exc)))
    budget = Counter({index: entry.get("count", 1) for index, entry in enumerate(policy["exceptions"])})
    expired = expired_exception_identities(policy)
    return errors + [
        finding
        for path, text in decoded.items()
        for finding in scan(path, text, source_language(path, text))
        if not allowed(finding, policy, text, budget, expired)
    ]


def source_paths(root: Path, staged: bool) -> list[str]:
    paths = (
        git(root, "ls-files", "-z", "--cached", *([] if staged else ["--others", "--exclude-standard"]))
        .decode()
        .split("\0")
    )
    executables = {
        entry.split("\t", 1)[1]
        for entry in git(root, "ls-files", "--stage", "-z").decode().split("\0")
        if entry.startswith("100755 ")
    }
    selected = []
    for path in sorted(set(paths) - {""}):
        if language(path):
            selected.append(path)
        elif path in executables or (
            not staged and (root / path).is_file() and (root / path).stat().st_mode & stat.S_IXUSR
        ):
            if staged:
                header = git(root, "show", f":{path}")[:128]
            else:
                with (root / path).open("rb") as stream:
                    header = stream.read(128)
            if header.startswith(b"#!"):
                selected.append(path)
    paths = selected
    return paths


def launcher_trusted_files(root: Path, base: str) -> set[str] | None:
    try:
        tree = ast.parse(git(root, "show", f"{base}:{LAUNCHER_PATH}"))
    except (subprocess.CalledProcessError, SyntaxError, ValueError):
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "TRUSTED_FILES" for t in node.targets
        ):
            try:
                names = {str(name) for name in ast.literal_eval(node.value)}
            except ValueError:
                return None
            return names or None
    return None


def indexed_paths(root: Path) -> set[str]:
    return set(git(root, "ls-files", "-z", "--cached").decode().split("\0")) - {""}


def path_present(root: Path, path: str, staged: bool, indexed: set[str] | None = None) -> bool:
    if staged:
        return path in (indexed if indexed is not None else indexed_paths(root))
    return (root / path).is_file()


def validate_consumers(root: Path, policy: dict, staged: bool = False) -> None:
    indexed = indexed_paths(root) if staged else None
    for entry in policy["exceptions"]:
        if not path_present(root, entry["consumer"], staged, indexed):
            raise ValueError(f"exception consumer missing: {entry['consumer']}")
    for entry in policy["external"]:
        validate_path(entry["provenance"])
        if not path_present(root, entry["provenance"], staged, indexed):
            raise ValueError(f"external provenance missing: {entry['provenance']}")
        if entry["path"].endswith(".whl") and not path_present(root, entry["path"], staged, indexed):
            raise ValueError(f"external wheel missing: {entry['path']}")


def plain_text(value: str) -> str:
    text = "".join(f"\\x{ord(char):02x}" if ord(char) < 32 or ord(char) == 127 else char for char in value)
    text = text.replace("##[", "\\x23#[")
    return f"\\{text}" if text.lstrip().startswith("::") else text


def annotation_text(value: str, *, property_value: bool = False) -> str:
    value = value.replace("##[", "%23#[").replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return value.replace(":", "%3A").replace(",", "%2C") if property_value else value


def exit_status(
    mode: str,
    baseline_policy: dict,
    failed: list[Finding],
    *,
    compared: bool = False,
    policy_owner_failure: bool = False,
) -> int:
    if policy_owner_failure:
        return int(bool(failed))
    rejects = bool(failed) and mode != "report"
    if not (rejects and compared and baseline_policy.get("enforcement", "blocking") == "advisory"):
        return int(rejects)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        for finding in failed[:100]:
            excerpt = annotation_text(finding.text.splitlines()[0][:160] if finding.text else "")
            path = annotation_text(finding.path, property_value=True)
            print(f"::warning file={path},line={finding.line}::comment-policy {finding.kind}: {excerpt}")
    gaps = sum(finding.kind in {"coverage-error", "payload-error"} for finding in failed)
    print(
        f"comment-policy: enforcement is advisory, so these {len(failed)} findings do not fail the check "
        f"({gaps} are inputs the checker could not analyze); they will once the policy sets enforcement to blocking"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reject discretionary comments and docstrings in maintained source.")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--mode", choices=("strict", "report"), default="strict")
    parser.add_argument("--base")
    parser.add_argument("--ownership-base")
    parser.add_argument("--staged", action="store_true")
    parser.add_argument("--policy", default=POLICY_PATH)
    parser.add_argument("--bootstrap", action="store_true")
    parser.add_argument("--accept-same-commit-fixtures", action="store_true")
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--path", action="append", default=[])
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve()
        if args.policy != POLICY_PATH:
            raise ValueError("policy location is fixed")
        policy = load_policy(git(root, "show", f":{POLICY_PATH}") if args.staged else (root / POLICY_PATH).read_bytes())
        if args.base and not re.fullmatch(r"[a-f0-9]{40}", args.base):
            raise ValueError("base must be a full commit SHA")
        compared = bool(args.base)
        require_local_staged_check(args)
        if args.bootstrap and not (compared and bootstrap_base_allowed(root, args.base)):
            raise ValueError("bootstrap requires a base that contains the initial rollout commit")
        owner_base = ownership_base(root, args.mode, args.base, args.ownership_base)
        reviewed = compared and oracle_review_covers(root)
        candidate_notices = candidate_scope_notices(root, args.staged) if reviewed else None
        owned_exclusions = ownership_exclusions(root, owner_base, args.staged, candidate_notices)
        baseline_policy, bumped_wheels = policy, set()
        if args.base and not args.bootstrap:
            baseline_policy, bumped_wheels = ratchet_against_base(root, policy, args.base, args.staged)
        owned_exclusions = with_bumped_wheels(owned_exclusions, bumped_wheels)
        paths = source_paths(root, args.staged)
        if not paths:
            raise ValueError("empty source inventory")
        for scope in args.path:
            validate_path(scope)
            if not any(matches(path, scope) for path in paths):
                raise ValueError(f"scope matches no registered source: {scope}")
        changed: set[str] = set()
        committed: set[str] = set()
        if compared:
            revoked = [e["path"] for e in baseline_policy["exceptions"] if e not in policy["exceptions"]]
            revoked.extend(e["path"] for e in baseline_policy["external"] if e not in policy["external"])
            changed = set(
                git(
                    root, "diff", "--name-only", "--no-renames", "-z", *(["--cached"] if args.staged else []), args.base
                )
                .decode()
                .split("\0")
            )
            if not args.staged:
                changed.update(git(root, "ls-files", "--others", "--exclude-standard", "-z").decode().split("\0"))
            committed = set(changed)
            trusted = launcher_trusted_files(root, args.base)
            global_change = (
                args.full
                or bool(revoked)
                or trusted is None
                or bool(changed & {POLICY_PATH, SCOPE_POLICY_PATH, LAUNCHER_PATH, *trusted})
            )
            if not global_change:
                changed.update(entry["path"] for entry in policy["exceptions"] if entry["consumer"] in changed)
                paths = [path for path in paths if path in changed]
        policy_owner_failure = (
            args.mode == "strict" and compared and POLICY_PATH in changed and bool(expired_policy_findings(policy))
        )
        if args.path:
            paths = [path for path in paths if any(matches(path, scope) for scope in args.path)]
        sources = {
            path: git(root, "show", f":{path}") if args.staged else (root / path).read_bytes()
            for path in paths
            if args.staged or (root / path).is_file()
        }
        validate_consumers(root, policy, args.staged)
        effective_policy = {
            **policy,
            "exceptions": counted_exceptions(
                policy, baseline_policy, reviewed, args.accept_same_commit_fixtures, committed
            ),
            "ownership_excluded_members": owned_exclusions,
        }
        if compared:
            base_paths = git(root, "ls-tree", "-r", "--name-only", "-z", args.base).decode().split("\0")
            effective_policy["external_members"] = set(base_paths) | bumped_wheels
        failed = expired_policy_findings(policy) + scan_sources(root, sources, effective_policy)
        warnings = expiring_policy_warnings(policy)
        if args.json_out:
            args.json_out.write_text(
                json.dumps(
                    {
                        "mode": args.mode,
                        "base": args.base,
                        "findings": [asdict(f) for f in failed],
                        "failed": [asdict(f) for f in failed],
                        "warnings": warnings,
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        for finding in failed[:100]:
            excerpt = finding.text.splitlines()[0][:160] if finding.text else ""
            print(plain_text(f"{finding.path}:{finding.line}: CP {finding.kind}: {excerpt}"))
        for warning in warnings:
            print(
                f"::warning::{warning}"
                if os.environ.get("GITHUB_ACTIONS") == "true"
                else f"comment-policy: warning: {warning}"
            )
        print(
            f"comment-policy: {len(sources)} source files, {len(failed)} violations, {len(failed)} enforced failures ({args.mode})"
        )
        return exit_status(
            args.mode, baseline_policy, failed, compared=compared, policy_owner_failure=policy_owner_failure
        )
    except (OSError, UnicodeError, ValueError, subprocess.CalledProcessError) as exc:
        print(plain_text(f"comment-policy: configuration or parser failure: {exc}"), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
