from __future__ import annotations

import argparse
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
import zipfile
from collections import Counter
from dataclasses import asdict
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any

from check_comment_cleanup_scope import (
    COMMENT_POLICY_DIRECTIVES,
    COMMENT_POLICY_ENFORCEMENT_MODES,
    POLICY_PATH as SCOPE_POLICY_PATH,
    comment_policy_matches as matches,
    immutable_external_ownership,
    load_comment_policy as load_policy,
    load_policy_bytes as load_scope_policy_bytes,
    oracle_review_covers,
    validate_comment_policy_path as validate_path,
)
from comment_syntax import Finding, javascript_requests, language, scan, source_language

DIRECTIVES = COMMENT_POLICY_DIRECTIVES
ENFORCEMENT_MODES = COMMENT_POLICY_ENFORCEMENT_MODES

POLICY_PATH = "quality/comment-policy.json"
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


def _is_wheel_bump(candidate_entry: dict, baseline_entry: dict) -> bool:
    cand_path = PurePosixPath(candidate_entry["path"])
    base_path = PurePosixPath(baseline_entry["path"])
    if cand_path.suffix != ".whl" or base_path.suffix != ".whl":
        return False
    if cand_path.parent != base_path.parent:
        return False
    if candidate_entry.get("owner") != baseline_entry.get("owner"):
        return False
    if candidate_entry.get("provenance") != baseline_entry.get("provenance"):
        return False
    cand_dist = cand_path.name.split("-", 1)[0]
    base_dist = base_path.name.split("-", 1)[0]
    return cand_dist == base_dist


def check_ratchet(policy: dict, baseline: dict) -> None:
    if baseline.get("enforcement", "blocking") == "blocking" and policy.get("enforcement", "blocking") == "advisory":
        raise ValueError("enforcement cannot be relaxed from blocking to advisory")
    if not set(baseline["completed"]).issubset(policy["completed"]):
        raise ValueError("completed scopes cannot be removed")
    for entry in policy["external"]:
        if entry in baseline["external"]:
            continue
        matched_base = [b for b in baseline["external"] if _is_wheel_bump(entry, b)]
        if matched_base and not any(b in policy["external"] for b in matched_base):
            continue
        raise ValueError("candidate policy cannot add or expand external exclusions")


def revision_bytes(root: Path, path: str, staged: bool) -> bytes | None:
    try:
        return git(root, "show", f":{path}") if staged else (root / path).read_bytes()
    except (OSError, subprocess.CalledProcessError):
        return None


def _wheel_metadata(wheel: bytes) -> dict[str, str]:
    with zipfile.ZipFile(io.BytesIO(wheel)) as archive:
        names = [name for name in archive.namelist() if re.fullmatch(r"[^/]+\.dist-info/METADATA", name)]
        if len(names) != 1:
            raise ValueError("wheel must carry exactly one dist-info METADATA file")
        lines = archive.read(names[0]).decode("utf-8", errors="replace").splitlines()
    fields: dict[str, str] = {}
    for line in lines:
        if not line.strip():
            break
        key, _, value = line.partition(":")
        fields.setdefault(key.strip(), value.strip())
    return fields


def _normalized_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _declares(requirement: str, owner: str) -> bool:
    match = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", requirement)
    return match is not None and _normalized_name(match.group()) == _normalized_name(owner)


def verify_bound_wheel(root: Path, entry: dict, staged: bool) -> None:
    import tomllib

    path, project_path, owner = entry["path"], entry["provenance"], entry["owner"]
    label = f"wheel bump is not verified: {path}"
    if not project_path.endswith("/pyproject.toml"):
        raise ValueError(f"{label}: provenance must be the owning pyproject.toml")
    lock_path = str(PurePosixPath(project_path).with_name("uv.lock"))
    wheel = revision_bytes(root, path, staged)
    project_raw = revision_bytes(root, project_path, staged)
    lock_raw = revision_bytes(root, lock_path, staged)
    if wheel is None or project_raw is None or lock_raw is None:
        raise ValueError(f"{label}: wheel, pyproject.toml or uv.lock is missing")
    try:
        project = tomllib.loads(project_raw.decode("utf-8"))
        locked = tomllib.loads(lock_raw.decode("utf-8"))
        metadata = _wheel_metadata(wheel)
    except (UnicodeError, tomllib.TOMLDecodeError, zipfile.BadZipFile, ValueError) as exc:
        raise ValueError(f"{label}: {exc}") from exc
    source = str(PurePosixPath(path).relative_to(PurePosixPath(project_path).parent))
    binding = project.get("tool", {}).get("uv", {}).get("sources", {}).get(owner)
    if binding != {"path": source}:
        raise ValueError(f"{label}: pyproject.toml does not bind {owner} to it")
    dependencies = project.get("project", {}).get("dependencies", [])
    if not any(_declares(dependency, owner) for dependency in dependencies):
        raise ValueError(f"{label}: {owner} is not a declared dependency")
    packages = [package for package in locked.get("package", []) if package.get("name") == owner]
    filename = PurePosixPath(path).name
    version = filename.split("-")[1] if filename.count("-") >= 2 else ""
    digest = "sha256:" + hashlib.sha256(wheel).hexdigest()
    if (
        len(packages) != 1
        or packages[0].get("source") != binding
        or packages[0].get("wheels") != [{"filename": filename, "hash": digest}]
    ):
        raise ValueError(f"{label}: uv.lock does not record this file and hash")
    if _normalized_name(metadata.get("Name", "")) != _normalized_name(owner) or metadata.get("Version") != version:
        raise ValueError(f"{label}: wheel METADATA does not match {owner} {version}")
    if packages[0].get("version") != version:
        raise ValueError(f"{label}: uv.lock version does not match the wheel")


def verify_wheel_bumps(root: Path, policy: dict, baseline: dict, staged: bool) -> None:
    for entry in policy["external"]:
        if entry in baseline["external"]:
            continue
        if any(_is_wheel_bump(entry, b) for b in baseline["external"]):
            verify_bound_wheel(root, entry, staged)


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


def native_results(root: Path, requests: dict[str, str]) -> dict[str, list[dict]]:
    results = {}
    pending = requests
    while pending:
        response = subprocess.run(
            ["node", str(Path(__file__).with_name("comment_syntax_js.cjs"))],
            input=json.dumps(pending),
            text=True,
            capture_output=True,
            check=True,
            env={**os.environ, "COMMENT_POLICY_ROOT": str(root)},
        )
        batch = json.loads(response.stdout)
        if set(batch) != set(pending):
            raise ValueError("native parser returned a different request set")
        results.update(batch)
        pending = {
            key: text
            for rows in batch.values()
            for row in rows
            if row["kind"] == "payload"
            for key, text in javascript_requests("payload." + row["language"], row["text"], row["language"]).items()
            if key not in results
        }
    return results


def develop_merge_base(root: Path) -> str | None:
    try:
        return git(root, "merge-base", "HEAD", "origin/develop").decode().strip()
    except subprocess.CalledProcessError:
        return None


def ownership_base(root: Path, mode: str, comparison: str | None, requested: str | None) -> str | None:
    if mode == "transition":
        if requested and requested != comparison:
            raise ValueError("transition ownership base must match the comparison base")
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
    requests = {
        key: text
        for path, source in decoded.items()
        for key, text in javascript_requests(path, source, language(path)).items()
    }
    js_results = native_results(root, requests)
    budget = Counter({index: entry.get("count", 1) for index, entry in enumerate(policy["exceptions"])})
    expired = expired_exception_identities(policy)
    return errors + [
        finding
        for path, text in decoded.items()
        for finding in scan(path, text, source_language(path, text), js_results)
        if not allowed(finding, policy, text, budget, expired)
    ]


def introduced(current: list[Finding], baseline: list[Finding], completed: list[str]) -> list[Finding]:
    remaining = Counter(f.identity for f in baseline)
    result = []
    for finding in current:
        if (
            finding.kind in {"coverage-error", "payload-error"}
            or any(matches(finding.path, scope) for scope in completed)
            or not remaining[finding.identity]
        ):
            result.append(finding)
        else:
            remaining[finding.identity] -= 1
    return result


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


def path_present(root: Path, path: str, staged: bool) -> bool:
    if staged:
        return (
            subprocess.run(["git", "-C", str(root), "cat-file", "-e", f":{path}"], capture_output=True).returncode == 0
        )
    return (root / path).is_file()


def validate_consumers(root: Path, policy: dict, staged: bool = False) -> None:
    for entry in policy["exceptions"]:
        if not path_present(root, entry["consumer"], staged):
            raise ValueError(f"exception consumer missing: {entry['consumer']}")
    for entry in policy["external"]:
        validate_path(entry["provenance"])
        if not path_present(root, entry["provenance"], staged):
            raise ValueError(f"external provenance missing: {entry['provenance']}")
        if entry["path"].endswith(".whl") and not path_present(root, entry["path"], staged):
            raise ValueError(f"external wheel missing: {entry['path']}")


def plain_text(value: str) -> str:
    text = "".join(f"\\x{ord(char):02x}" if ord(char) < 32 or ord(char) == 127 else char for char in value)
    text = text.replace("##[", "\\x23#[")
    return f"\\{text}" if text.lstrip().startswith("::") else text


def annotation_text(value: str, *, property_value: bool = False) -> str:
    value = value.replace("##[", "%23#[").replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return value.replace(":", "%3A").replace(",", "%2C") if property_value else value


def exit_status(mode: str, baseline_policy: dict, failed: list[Finding], *, policy_owner_failure: bool = False) -> int:
    if policy_owner_failure and mode == "transition":
        return int(bool(failed))
    rejects = bool(failed) and mode != "report"
    if not (rejects and mode == "transition" and baseline_policy.get("enforcement", "blocking") == "advisory"):
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
    parser.add_argument("--mode", choices=("strict", "transition", "report"), default="strict")
    parser.add_argument("--base")
    parser.add_argument("--ownership-base")
    parser.add_argument("--staged", action="store_true")
    parser.add_argument("--policy", default=POLICY_PATH)
    parser.add_argument("--bootstrap", action="store_true")
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--path", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve()
        if args.policy != POLICY_PATH:
            raise ValueError("policy location is fixed")
        policy = load_policy(git(root, "show", f":{POLICY_PATH}") if args.staged else (root / POLICY_PATH).read_bytes())
        if args.mode == "transition" and not args.base:
            raise ValueError("transition requires an explicit immutable base SHA")
        if args.base and not re.fullmatch(r"[a-f0-9]{40}", args.base):
            raise ValueError("base must be a full commit SHA")
        if args.bootstrap and (args.mode != "transition" or not bootstrap_base_allowed(root, args.base or "")):
            raise ValueError("bootstrap requires a base that contains the initial rollout commit")
        owner_base = ownership_base(root, args.mode, args.base, args.ownership_base)
        reviewed = args.mode == "transition" and oracle_review_covers(root)
        candidate_notices = candidate_scope_notices(root, args.staged) if reviewed else None
        owned_exclusions = ownership_exclusions(root, owner_base, args.staged, candidate_notices)
        baseline_policy = policy
        if args.base and not args.bootstrap:
            baseline_policy = load_policy(git(root, "show", f"{args.base}:{POLICY_PATH}"))
            check_ratchet(policy, baseline_policy)
            verify_wheel_bumps(root, policy, baseline_policy, args.staged)
        paths = source_paths(root, args.staged)
        if not paths:
            raise ValueError("empty source inventory")
        for scope in args.path:
            validate_path(scope)
            if not any(matches(path, scope) for path in paths):
                raise ValueError(f"scope matches no registered source: {scope}")
        changed: set[str] = set()
        if args.mode == "transition":
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
            paths = [
                path
                for path in paths
                if path in changed or any(matches(path, scope) for scope in [*policy["completed"], *revoked])
            ]
        policy_owner_failure = (
            args.mode == "transition" and POLICY_PATH in changed and bool(expired_policy_findings(policy))
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
            "exceptions": [e for e in policy["exceptions"] if e in baseline_policy["exceptions"] or reviewed],
            "ownership_excluded_members": owned_exclusions,
        }
        base_paths = []
        if args.mode == "transition":
            base_paths = git(root, "ls-tree", "-r", "--name-only", "-z", args.base).decode().split("\0")
            effective_policy["external_members"] = set(base_paths)
        current = expired_policy_findings(policy) + scan_sources(root, sources, effective_policy)
        failed = current
        if args.mode == "transition":
            base_sources = {path: git(root, "show", f"{args.base}:{path}") for path in base_paths if path in sources}
            baseline = expired_policy_findings(baseline_policy) + scan_sources(
                root, base_sources, {**baseline_policy, "ownership_excluded_members": owned_exclusions}
            )
            failed = introduced(current, baseline, policy["completed"])
            if POLICY_PATH in changed:
                failed.extend(finding for finding in expired_policy_findings(policy) if finding not in failed)
        if args.json_out:
            args.json_out.write_text(
                json.dumps(
                    {
                        "mode": args.mode,
                        "base": args.base,
                        "findings": [asdict(f) for f in current],
                        "failed": [asdict(f) for f in failed],
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        for finding in failed[:100]:
            excerpt = finding.text.splitlines()[0][:160] if finding.text else ""
            print(plain_text(f"{finding.path}:{finding.line}: CP {finding.kind}: {excerpt}"))
        print(
            f"comment-policy: {len(sources)} source files, {len(current)} violations, {len(failed)} enforced failures ({args.mode})"
        )
        return exit_status(args.mode, baseline_policy, failed, policy_owner_failure=policy_owner_failure)
    except (OSError, UnicodeError, ValueError, subprocess.CalledProcessError) as exc:
        print(plain_text(f"comment-policy: configuration or parser failure: {exc}"), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
