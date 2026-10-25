from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

OWNERSHIP_PATH = "quality/docs-ownership.json"
QUERY_ROOT = "docs/benchmarks/queries/"
PROHIBITED = re.compile(
    r"(^|/)(pyproject\.toml|uv\.lock|CNAME)$|\.(py|pyi|pem)$|^docs/blog/|^docs/CNAME$",
)
SESSION_URL = r"https?://claude\.ai/code/session_[A-Za-z0-9_-]+"
SESSION_URL_BYTES = re.compile(SESSION_URL.encode())
SESSION_TRAILER = re.compile(rb"^\s*Claude-Session:", re.I)
SESSION_LEAK = re.compile(SESSION_URL + r"|^\s*Claude-Session:", re.I | re.M)


def run(repo: Path, *args: str, data: bytes | None = None, check: bool = True) -> bytes:
    done = subprocess.run(["git", *args], cwd=repo, input=data, capture_output=True, check=False)
    if check and done.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {done.stderr.decode(errors='replace').strip()}")
    return done.stdout


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def strip_message(message: bytes) -> bytes:
    kept = []
    changed = False
    for line in message.split(b"\n"):
        if SESSION_TRAILER.match(line):
            changed = True
            continue
        if SESSION_URL_BYTES.search(line):
            changed = True
            rest = SESSION_URL_BYTES.sub(b"", line).rstrip()
            if not rest.strip():
                continue
            line = rest
        kept.append(line)
    if not changed:
        return message
    return b"\n".join(kept).rstrip() + b"\n"


def message_callback() -> str:
    return "\n".join(
        [
            "import sys",
            f"sys.path.insert(0, {str(Path(__file__).resolve().parent)!r})",
            "import docs_history_filter",
            "return docs_history_filter.strip_message(message)",
        ]
    )


def is_prohibited(path: str) -> bool:
    return bool(PROHIBITED.search(path)) or path.startswith(QUERY_ROOT)


def split_ownership(manifest: dict[str, Any]) -> tuple[set[str], set[str]]:
    owned: set[str] = set()
    excluded: set[str] = set()
    for path, owner in manifest["files"].items():
        (owned if owner == "site-owned" else excluded).add(path)
    for entry in manifest.get("generated", []):
        excluded.add(entry["path"])
    return owned, excluded


def selected_projection(repo: Path, sha: str, owned: set[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(owned):
        if is_prohibited(path):
            raise ValueError(f"site-owned path is prohibited: {path}")
        blob = run(repo, "rev-parse", f"{sha}:{path}").decode().strip()
        result[path] = blob
    return result


def rename_edges(repo: Path, sha: str) -> list[tuple[str, str]]:
    out = run(
        repo,
        "log",
        "--topo-order",
        "--no-merges",
        "-M",
        "--diff-filter=R",
        "--name-status",
        "-z",
        "--format=%x00%x01%H",
        sha,
    )
    edges: list[tuple[str, str]] = []
    for block in out.split(b"\x00\x01")[1:]:
        parts = block.split(b"\x00")
        fields = [part.decode("utf-8", "surrogateescape").lstrip("\n") for part in parts[1:] if part.strip(b"\n")]
        i = 0
        while i + 2 < len(fields) + 0 and fields[i].startswith("R"):
            edges.append((fields[i + 1], fields[i + 2]))
            i += 3
    return edges


def historical_paths(repo: Path, sha: str, owned: set[str], excluded: set[str]) -> set[str]:
    wanted = set(owned)
    for old, new in rename_edges(repo, sha):
        if new in wanted:
            wanted.add(old)
    return {path for path in wanted if not is_prohibited(path) and path not in excluded}


def load_privacy_rules(text: str) -> dict[str, Any]:
    patterns = re.findall(r'\{\s*description:\s*"([^"]+)",\s*pattern:\s*/(.+?)/g\s*\}', text)
    if len(patterns) != 4:
        raise ValueError(f"expected 4 privacy patterns, found {len(patterns)}")
    connection = re.search(r"const CONNECTION = /(.+)/g;", text)
    aws = re.search(r"const AWS_EXAMPLES = new Set\(\[(.*?)\]\);", text)
    passwords = re.search(r"const PLACEHOLDER_PASSWORDS = new Set\(\[(.*?)\]\);", text)
    if not (connection and aws and passwords):
        raise ValueError("privacy.ts constants not found")
    return {
        "patterns": [(description, re.compile(pattern)) for description, pattern in patterns],
        "aws_examples": set(re.findall(r'"([^"]*)"', aws.group(1))),
        "connection": re.compile(connection.group(1)),
        "placeholder_passwords": set(re.findall(r'"([^"]*)"', passwords.group(1))),
    }


def privacy_findings(text: str, rules: dict[str, Any]) -> list[str]:
    findings: list[str] = []
    for description, pattern in rules["patterns"]:
        hits = [m.group(0) for m in pattern.finditer(text) if m.group(0) not in rules["aws_examples"]]
        if hits:
            findings.append(f"{description} x{len(hits)}")
    creds = [
        m
        for m in rules["connection"].finditer(text)
        if m.group(2).strip().lower() not in rules["placeholder_passwords"]
    ]
    if creds:
        findings.append(f"Database connection string with credentials x{len(creds)}")
    return findings


def history_blobs(repo: Path) -> dict[str, set[str]]:
    listing = run(repo, "rev-list", "--objects", "--all").decode("utf-8", "surrogateescape")
    paths: dict[str, set[str]] = defaultdict(set)
    for line in listing.splitlines():
        sha, _, path = line.partition(" ")
        if path:
            paths[sha].add(path)
    kinds = (
        run(repo, "cat-file", "--batch-check=%(objectname) %(objecttype)", data="\n".join(paths).encode())
        .decode()
        .splitlines()
    )
    return {sha: names for sha, names in paths.items() if any(k == f"{sha} blob" for k in kinds)}


def scan_blobs(repo: Path, rules: dict[str, Any]) -> list[dict[str, Any]]:
    blobs = history_blobs(repo)
    proc = subprocess.run(
        ["git", "cat-file", "--batch"],
        cwd=repo,
        input="\n".join(blobs).encode() + b"\n",
        capture_output=True,
        check=True,
    )
    data = proc.stdout
    findings: list[dict[str, Any]] = []
    pos = 0
    while pos < len(data):
        end = data.index(b"\n", pos)
        sha, _, size = data[pos:end].decode().split(" ")
        body = data[end + 1 : end + 1 + int(size)]
        pos = end + 1 + int(size) + 1
        text = body.decode("utf-8", "replace")
        hits = privacy_findings(text, rules)
        if SESSION_LEAK.search(text):
            hits.append("agent session reference")
        if hits:
            findings.append({"blob": sha, "paths": sorted(blobs[sha]), "findings": hits})
    return findings


def history_paths(repo: Path) -> set[str]:
    out = run(repo, "log", "--all", "--name-only", "--format=", "-z").decode("utf-8", "surrogateescape")
    return {name for name in out.split("\x00") if name.strip()}


def tool_versions(gitleaks: bool) -> dict[str, str]:
    def first(*cmd: str) -> str:
        done = subprocess.run(cmd, capture_output=True, text=True, check=False)
        return (
            (done.stdout or done.stderr).strip().splitlines()[0]
            if (done.stdout or done.stderr).strip()
            else "unavailable"
        )

    return {
        "git": first("git", "--version"),
        "git-filter-repo": first("git", "filter-repo", "--version"),
        "gitleaks": first("gitleaks", "version") if gitleaks else "not used",
        "python": sys.version.split()[0],
    }


def run_gitleaks(repo: Path, report: Path, triage: dict[str, str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    done = subprocess.run(
        [
            "gitleaks",
            "git",
            "--no-banner",
            "--redact",
            "--report-format",
            "json",
            "--report-path",
            str(report),
            "--exit-code",
            "0",
            str(repo),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        raise RuntimeError(f"gitleaks failed: {done.stderr.strip()}")
    unresolved: list[dict[str, Any]] = []
    triaged: list[dict[str, Any]] = []
    for finding in json.loads(report.read_text(encoding="utf-8") or "[]"):
        line = (
            run(repo, "show", f"{finding['Commit']}:{finding['File']}")
            .decode("utf-8", "replace")
            .splitlines()[finding["StartLine"] - 1]
        )
        digest = sha256(f"{finding['RuleID']}\0{finding['File']}\0{line.strip()}".encode())
        entry = {
            "rule": finding["RuleID"],
            "path": finding["File"],
            "commit": finding["Commit"],
            "line": finding["StartLine"],
            "fingerprint": digest,
        }
        if digest in triage:
            triaged.append({**entry, "reason": triage[digest]})
        else:
            unresolved.append(entry)
    return unresolved, triaged


def commit_map(repo: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for line in (repo / ".git" / "filter-repo" / "commit-map").read_text(encoding="utf-8").splitlines()[1:]:
        old, new = line.split()
        if new != "0" * 40:
            mapping[old] = new
    return mapping


def rehearse(args: argparse.Namespace) -> int:
    source = Path(args.source).resolve()
    work = Path(args.work).resolve()
    sha = run(source, "rev-parse", f"{args.sha}^{{commit}}").decode().strip()
    if work.exists():
        raise SystemExit(f"work directory exists: {work}")
    ownership_bytes = run(source, "show", f"{sha}:{OWNERSHIP_PATH}")
    manifest = json.loads(ownership_bytes)
    owned, excluded = split_ownership(manifest)
    projection = selected_projection(source, sha, owned)
    privacy_text = Path(args.privacy_ts).read_text(encoding="utf-8")
    rules = load_privacy_rules(privacy_text)

    work.mkdir(parents=True)
    clone = work / "repo"
    subprocess.run(
        ["git", "clone", "--quiet", "--no-local", "--no-hardlinks", "--no-checkout", str(source), str(clone)],
        check=True,
    )
    run(clone, "checkout", "--quiet", "--detach", sha)
    run(clone, "branch", "--force", "docs-history-rehearsal", sha)
    for ref in run(clone, "for-each-ref", "--format=%(refname)").decode().split():
        if ref != "refs/heads/docs-history-rehearsal":
            run(clone, "update-ref", "-d", ref)
    run(clone, "checkout", "--quiet", "docs-history-rehearsal")

    keep = historical_paths(clone, sha, owned, excluded)
    paths_file = work / "keep-paths.txt"
    paths_file.write_text("".join(f"literal:{p}\n" for p in sorted(keep)), encoding="utf-8")

    subprocess.run(
        [
            "git",
            "filter-repo",
            "--force",
            "--preserve-commit-hashes",
            "--refs",
            "refs/heads/docs-history-rehearsal",
            "--paths-from-file",
            str(paths_file),
            "--message-callback",
            message_callback(),
        ],
        cwd=clone,
        check=True,
        capture_output=True,
    )
    run(clone, "reflog", "expire", "--expire=now", "--all")
    run(clone, "gc", "--prune=now", "--quiet")

    tip = run(clone, "rev-parse", "docs-history-rehearsal").decode().strip()
    tip_entries = run(clone, "ls-tree", "-r", tip).decode("utf-8", "surrogateescape").splitlines()
    tip_tree = {line.split("\t", 1)[1]: line.split()[2] for line in tip_entries}
    if tip_tree != projection:
        missing = sorted(set(projection) - set(tip_tree))
        extra = sorted(set(tip_tree) - set(projection))
        differing = sorted(p for p in set(projection) & set(tip_tree) if projection[p] != tip_tree[p])
        raise SystemExit(f"tip tree differs from projection: missing={missing} extra={extra} differing={differing}")

    seen = history_paths(clone)
    bad = sorted(p for p in seen if is_prohibited(p) or p in excluded)
    if bad:
        raise SystemExit(f"prohibited paths in history: {bad}")
    objects = run(clone, "rev-list", "--objects", "--all").decode("utf-8", "surrogateescape").splitlines()
    object_paths = {line.partition(" ")[2] for line in objects if " " in line}
    object_bad = sorted(p for p in object_paths if p.endswith(("/",)) is False and (is_prohibited(p) or p in excluded))
    if object_bad:
        raise SystemExit(f"prohibited paths among history objects: {object_bad}")

    messages = run(clone, "log", "--all", "--format=%B").decode("utf-8", "replace")
    if SESSION_LEAK.search(messages):
        raise SystemExit("agent session references remain in commit messages")

    mapping = commit_map(clone)
    source_clone_metadata = verify_against_source(source, clone, mapping)

    blob_findings = scan_blobs(clone, rules)
    triage = json.loads(Path(args.triage).read_text(encoding="utf-8")) if args.triage else {}
    leak_findings, leak_triaged = (
        ([], []) if args.skip_gitleaks else run_gitleaks(clone, work / "gitleaks.json", triage)
    )
    commits = int(run(clone, "rev-list", "--count", tip).decode())
    receipt = {
        "input_sha": sha,
        "ownership_file": OWNERSHIP_PATH,
        "ownership_sha256": sha256(ownership_bytes),
        "privacy_policy_sha256": sha256(privacy_text.encode()),
        "tool_versions": tool_versions(not args.skip_gitleaks),
        "site_owned_paths": len(projection),
        "historical_paths_kept": len(keep),
        "keep_paths_sha256": sha256(paths_file.read_bytes()),
        "rehearsal_tip": tip,
        "rehearsal_tip_tree": run(clone, "rev-parse", f"{tip}^{{tree}}").decode().strip(),
        "tip_equals_projection": True,
        "filtered_commits": commits,
        "source_commits": int(run(source, "rev-list", "--count", sha).decode()),
        "metadata": source_clone_metadata,
        "prohibited_paths_in_history": 0,
        "privacy_rule_findings": blob_findings,
        "gitleaks_findings": leak_findings,
        "gitleaks_triaged": leak_triaged,
    }
    (work / "receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                k: v
                for k, v in receipt.items()
                if k not in ("privacy_rule_findings", "gitleaks_findings", "gitleaks_triaged")
            },
            indent=2,
        )
    )
    print(
        f"privacy_rule_findings={len(blob_findings)} gitleaks_findings={len(leak_findings)}"
        f" gitleaks_triaged={len(leak_triaged)}"
    )
    for finding in blob_findings:
        print("PRIVACY", json.dumps(finding))
    for finding in leak_findings:
        print("GITLEAKS", json.dumps(finding))
    return 1 if blob_findings or leak_findings else 0


def verify_against_source(source: Path, clone: Path, mapping: dict[str, str]) -> dict[str, int]:
    fmt = "%H%x00%an%x00%ae%x00%aI%x00%cn%x00%ce%x00%cI%x00%B%x01"

    def records(repo: Path, revs: list[str]) -> dict[str, list[str]]:
        out = run(
            repo, "log", "--no-walk=unsorted", f"--format={fmt}", "--stdin", data="\n".join(revs).encode()
        ).decode("utf-8", "replace")
        result = {}
        for rec in out.split("\x01"):
            if rec.strip():
                fields = rec.lstrip("\n").split("\x00")
                result[fields[0]] = fields
        return result

    old = records(source, list(mapping))
    new = records(clone, list(mapping.values()))
    checked = 0
    trailers = 0
    for before, after in mapping.items():
        a, b = old[before], new[after]
        if a[1:7] != b[1:7]:
            raise AssertionError(f"author/committer identity or dates changed for {before}")
        if strip_message(a[7].encode()).decode().strip() != b[7].strip():
            raise AssertionError(f"message changed beyond session stripping for {before}")
        co_a = [l for l in a[7].splitlines() if l.lower().startswith("co-authored-by:")]
        co_b = [l for l in b[7].splitlines() if l.lower().startswith("co-authored-by:")]
        if co_a != co_b:
            raise AssertionError(f"co-author trailers changed for {before}")
        trailers += len(co_b)
        checked += 1
    return {"commits_verified": checked, "co_author_trailers_retained": trailers}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("rehearse")
    cmd.add_argument("--source", required=True)
    cmd.add_argument("--sha", required=True)
    cmd.add_argument("--work", required=True)
    cmd.add_argument("--privacy-ts", required=True)
    cmd.add_argument("--skip-gitleaks", action="store_true")
    cmd.add_argument("--triage")
    cmd.set_defaults(func=rehearse)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
