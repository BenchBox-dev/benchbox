#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import os
import re
import shlex
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from _project.scripts import preflight_targets

MAP_VERSION = 1
MAP_ARTIFACT_PREFIX = "t2-coverage-map-"
MAX_MAP_BEHIND_COMMITS = 50
MAX_SELECTED_NODEIDS = 2000
TESTS_ROOT = "tests"
INERT_PATH_PATTERNS = ("docs/*", "*.md", "_project/todo-db-export/*")
TRUNK_WORKFLOW_PATH = ".github/workflows/trunk.yml"
SELECTION_SHADOW = True

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_HUNK_RE = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_SLUG_RE = re.compile(r"^[^/\s]+/[^/\s]+$")


class SelectionError(RuntimeError):
    pass


def _mono_time() -> float:
    try:
        from benchbox.utils.clock import mono_time
    except ImportError:
        from time import perf_counter as mono_time
    return mono_time()


def _peak_rss_mb() -> float:
    import resource

    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        return rss / (1024 * 1024)
    return rss / 1024


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, check=True, text=True, capture_output=True, timeout=120)
    return proc.stdout


def normalize_context(context: str) -> str | None:
    node = context.split("|", 1)[0].strip()
    return node or None


def _relative_name(repo: Path, filename: str) -> str | None:
    try:
        return Path(filename).resolve().relative_to(repo.resolve()).as_posix()
    except (OSError, ValueError):
        return None


def build_map(data_files: list[Path], repo: Path, sha: str) -> dict:
    from coverage import CoverageData

    merged = CoverageData()
    for path in data_files:
        shard = CoverageData(str(path))
        shard.read()
        merged.update(shard)
    files: dict[str, dict[str, list[str]]] = {}
    for filename in merged.measured_files():
        rel = _relative_name(repo, filename)
        if rel is None:
            continue
        lines: dict[str, set[str]] = {}
        for lineno, contexts in merged.contexts_by_lineno(filename).items():
            nodes = {node for context in contexts if (node := normalize_context(context))}
            if nodes:
                lines.setdefault(str(lineno), set()).update(nodes)
        if lines:
            files[rel] = {lineno: sorted(nodes) for lineno, nodes in lines.items()}
    return {"version": MAP_VERSION, "sha": sha, "files": files}


def write_map(mapping: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(mapping, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def read_map(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SelectionError(f"coverage map {path} is unreadable: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("version") != MAP_VERSION:
        raise SelectionError(f"coverage map {path} has an unsupported version")
    sha = payload.get("sha")
    files = payload.get("files")
    if not isinstance(sha, str) or not _SHA_RE.match(sha) or not isinstance(files, dict):
        raise SelectionError(f"coverage map {path} has an invalid envelope")
    return payload


@dataclass
class ChangedFile:
    path: str
    old_path: str
    status: str
    old_ranges: list[tuple[int, int]] = field(default_factory=list)


def _strip_git_prefix(token: str) -> str:
    if token.startswith("a/") or token.startswith("b/"):
        return token[2:]
    return token


def _split_diff_header(header: str) -> tuple[str, str]:
    rest = header[len("diff --git ") :] if header.startswith("diff --git ") else ""
    try:
        parts = shlex.split(rest)
    except ValueError:
        return "", ""
    if len(parts) != 2:
        return "", ""
    return _strip_git_prefix(parts[0]), _strip_git_prefix(parts[1])


def _parse_block(header: str, block: list[str]) -> ChangedFile:
    old = ""
    new = ""
    rename_from = ""
    rename_to = ""
    renamed = False
    added = False
    deleted = False
    ranges: list[tuple[int, int]] = []
    for line in block:
        if line.startswith("rename from "):
            renamed = True
            rename_from = line[len("rename from ") :].strip()
        elif line.startswith("rename to "):
            rename_to = line[len("rename to ") :].strip()
        elif line.startswith("new file mode"):
            added = True
        elif line.startswith("deleted file mode"):
            deleted = True
        elif line.startswith("--- "):
            old = _strip_git_prefix(line[4:].split("\t", 1)[0].strip())
        elif line.startswith("+++ "):
            new = _strip_git_prefix(line[4:].split("\t", 1)[0].strip())
        elif line.startswith("Binary files "):
            sides = line[len("Binary files ") :].split(" and ", 1)
            if len(sides) == 2:
                old = _strip_git_prefix(sides[0])
                new = _strip_git_prefix(sides[1].rsplit(" differ", 1)[0])
        else:
            match = _HUNK_RE.match(line)
            if match:
                start = int(match.group(1))
                count = int(match.group(2)) if match.group(2) is not None else 1
                ranges.append((start, count))
    if rename_from and rename_to:
        old, new = rename_from, rename_to
    elif not old or not new:
        header_old, header_new = _split_diff_header(header)
        old, new = old or header_old, new or header_new
    if not old or not new:
        raise SelectionError(f"cannot parse diff block starting with: {header[:120]}")
    return _finish_entry(old, new, renamed, added, deleted, ranges)


def _finish_entry(
    old: str, new: str, renamed: bool, added: bool, deleted: bool, ranges: list[tuple[int, int]]
) -> ChangedFile:
    if old == "/dev/null":
        return ChangedFile(path=new, old_path=new, status="added", old_ranges=ranges)
    if new == "/dev/null":
        return ChangedFile(path=old, old_path=old, status="deleted", old_ranges=ranges)
    if renamed or old != new:
        return ChangedFile(path=new, old_path=old, status="renamed", old_ranges=ranges)
    if added:
        return ChangedFile(path=new, old_path=old, status="added", old_ranges=ranges)
    if deleted:
        return ChangedFile(path=new, old_path=old, status="deleted", old_ranges=ranges)
    return ChangedFile(path=new, old_path=old, status="modified", old_ranges=ranges)


def parse_diff(diff: str) -> list[ChangedFile]:
    blocks: list[tuple[str, list[str]]] = []
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            blocks.append((line, []))
        elif blocks:
            blocks[-1][1].append(line)
    return [_parse_block(header, block) for header, block in blocks]


def changed_files(repo: Path, map_sha: str, head: str = "HEAD") -> list[ChangedFile]:
    try:
        diff = _git(
            repo,
            "-c",
            "core.quotePath=false",
            "diff",
            "-U0",
            "--find-renames",
            "--no-color",
            "--no-ext-diff",
            f"{map_sha}...{head}",
            "--",
        )
    except subprocess.CalledProcessError as exc:
        raise SelectionError(f"cannot diff {map_sha}...{head}: {exc.stderr.strip()}") from exc
    return parse_diff(diff)


def named_changed_files(repo: Path, base_sha: str, head: str = "HEAD") -> list[ChangedFile]:
    try:
        out = _git(repo, "diff", "--name-only", "--no-renames", f"{base_sha}...{head}", "--")
    except subprocess.CalledProcessError as exc:
        raise SelectionError(f"cannot list {base_sha}...{head}: {exc.stderr.strip()}") from exc
    return [ChangedFile(path=name, old_path=name, status="modified") for name in out.splitlines() if name]


def _emergency_fallback(repo: Path, base_sha: str) -> list[str]:
    if not base_sha:
        raise SelectionError("cannot compute a fallback selection without a base SHA")
    try:
        out = _git(repo, "diff", "--name-only", "--no-renames", f"{base_sha}...HEAD", "--")
    except subprocess.CalledProcessError as exc:
        raise SelectionError(f"cannot list {base_sha}...HEAD: {exc.stderr.strip()}") from exc
    names = [line for line in out.splitlines() if line]
    entries = [ChangedFile(path=name, old_path=name, status="modified") for name in names]
    fallback = preflight_targets.map_tests(repo, _fallback_inputs(entries))
    sources = [name for name in names if name.endswith(".py") and not _is_test_file(name)]
    importers = source_importers(repo, sources)
    return sorted(set(fallback) | {test for tests in importers.values() for test in tests})


def probe_lines(start: int, count: int) -> list[int]:
    if count > 0:
        return list(range(start, start + count))
    return [line for line in (start, start + 1) if line >= 1]


def lookup_hits(index: dict, path: str, lines: list[int]) -> tuple[set[str], list[int]]:
    covered = index.get(path)
    hits: set[str] = set()
    if not isinstance(covered, dict):
        return hits, list(lines)
    missed: list[int] = []
    for lineno in lines:
        nodes = covered.get(str(lineno))
        if isinstance(nodes, list) and nodes:
            names = {node for node in nodes if isinstance(node, str) and node}
            if names:
                hits.update(names)
                continue
        missed.append(lineno)
    return hits, missed


def _is_test_file(path: str) -> bool:
    pure = PurePosixPath(path)
    return pure.parts[:1] == (TESTS_ROOT,) and pure.name.startswith("test_") and pure.suffix == ".py"


def _is_inert(path: str) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in INERT_PATH_PATTERNS)


def _module_name(path: str) -> str:
    parts = PurePosixPath(path).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _imported_modules(repo: Path, path: str) -> set[str]:
    try:
        tree = ast.parse((repo / path).read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return set()
    package = PurePosixPath(path).parent.parts
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            prefix = package[: len(package) - node.level + 1] if node.level else ()
            base = ".".join([*prefix, *(node.module.split(".") if node.module else [])])
            modules.add(base)
            modules.update(f"{base}.{alias.name}" for alias in node.names)
    return modules


def source_importers(repo: Path, sources: list[str]) -> dict[str, list[str]]:
    wanted = {source: _module_name(source) for source in sources}
    found: dict[str, set[str]] = {source: set() for source in sources}
    try:
        tests = sorted((repo / TESTS_ROOT).rglob("test_*.py"))
    except OSError:
        return {source: [] for source in sources}
    for test in tests:
        try:
            rel = test.relative_to(repo).as_posix()
        except ValueError:
            continue
        imported = _imported_modules(repo, rel)
        for source, name in wanted.items():
            if any(module == name or module.startswith(f"{name}.") for module in imported):
                found[source].add(rel)
    return {source: sorted(paths) for source, paths in found.items()}


def behind_commits(repo: Path, map_sha: str, base_sha: str) -> int:
    try:
        out = _git(repo, "rev-list", "--count", f"{map_sha}..{base_sha}", "--")
    except subprocess.CalledProcessError as exc:
        raise SelectionError(f"cannot count commits {map_sha}..{base_sha}: {exc.stderr.strip()}") from exc
    try:
        return int(out.strip())
    except ValueError as exc:
        raise SelectionError(f"cannot parse commit count {out.strip()!r}") from exc


def read_decision(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SelectionError(f"decision {path} is unreadable: {exc}") from exc
    if not isinstance(payload, dict):
        raise SelectionError(f"decision {path} is not an object")
    return payload


def _fallback_inputs(files: list[ChangedFile]) -> list[str]:
    inputs: list[str] = []
    for entry in files:
        inputs.append(entry.path)
        if entry.old_path != entry.path:
            inputs.append(entry.old_path)
    return inputs


def _path_hits(index: dict, path: str) -> set[str]:
    lines = index.get(path)
    if not isinstance(lines, dict):
        return set()
    hits: set[str] = set()
    for nodes in lines.values():
        if isinstance(nodes, list):
            hits.update(node for node in nodes if isinstance(node, str))
    return hits


def _classify_files(
    repo: Path, mapping: dict | None, files: list[ChangedFile], index: dict
) -> tuple[set[str], set[str], set[str]]:
    coverage_hits: set[str] = set()
    uncovered: set[str] = set()
    non_python: set[str] = set()
    for entry in files:
        if _is_inert(entry.path):
            continue
        if not entry.path.endswith(".py"):
            non_python.add(entry.path)
            continue
        if _is_test_file(entry.path) or _is_test_file(entry.old_path):
            continue
        if mapping is None:
            uncovered.add(entry.path)
            continue
        if entry.status == "added":
            uncovered.add(entry.path)
            continue
        if entry.status == "renamed":
            coverage_hits.update(_path_hits(index, entry.old_path))
            uncovered.add(entry.path)
        if entry.status == "modified" and not entry.old_ranges:
            uncovered.add(entry.old_path)
            continue
        lines = [line for old_start, old_count in entry.old_ranges for line in probe_lines(old_start, old_count)]
        hits, _missed = lookup_hits(index, entry.old_path, lines)
        coverage_hits.update(hits)
        if _missed:
            uncovered.add(entry.old_path)
    coverage_hits = {node for node in coverage_hits if (repo / node.split("::", 1)[0]).is_file()}
    return coverage_hits, uncovered, non_python


def prune_missing_nodeids(repo: Path, node_ids: list[str], collected: list[str]) -> list[str]:
    have = set(collected)
    pruned: list[str] = []
    for node in node_ids:
        if node in have:
            candidate = node
        else:
            candidate = node.split("::", 1)[0]
            if not (repo / candidate).is_file():
                continue
        if candidate not in pruned:
            pruned.append(candidate)
    return pruned


def _apply_develop_overlap(
    repo: Path, mapping: dict | None, map_sha: str | None, base_sha: str, files: list[ChangedFile]
) -> tuple[list[ChangedFile], str | None]:
    if mapping is None or map_sha is None:
        return files, None
    if map_sha == base_sha or not _is_ancestor(repo, map_sha, base_sha):
        return files, None
    try:
        develop_files = changed_files(repo, map_sha, base_sha)
    except SelectionError as exc:
        return files, str(exc)
    overlap = {entry.path for entry in develop_files} | {entry.old_path for entry in develop_files}
    if not overlap:
        return files, None
    remapped = [
        ChangedFile(path=entry.path, old_path=entry.old_path, status="added", old_ranges=[])
        if entry.path in overlap or entry.old_path in overlap
        else entry
        for entry in files
    ]
    return remapped, None


def select_tests(
    repo: Path,
    mapping: dict | None,
    files: list[ChangedFile],
    base_sha: str,
    decision: dict | None = None,
    map_note: str | None = None,
) -> dict:
    start = _mono_time()
    fallback = preflight_targets.map_tests(repo, _fallback_inputs(files))
    reasons: list[str] = []
    behind: int | None = None
    map_sha: str | None = None
    if mapping is None:
        reasons.append(map_note or "coverage map is missing")
    else:
        map_sha = mapping.get("sha") if isinstance(mapping.get("sha"), str) else None
        if map_sha is None:
            reasons.append("coverage map names no develop SHA")
        else:
            if not _is_ancestor(repo, map_sha, base_sha):
                reasons.append(f"coverage map {map_sha} is not an ancestor of the base")
            else:
                try:
                    behind = behind_commits(repo, map_sha, base_sha)
                except SelectionError as exc:
                    reasons.append(str(exc))
                else:
                    if behind > MAX_MAP_BEHIND_COMMITS:
                        reasons.append(
                            f"coverage map is {behind} develop commits behind the base (limit {MAX_MAP_BEHIND_COMMITS})"
                        )
    files, overlap_note = _apply_develop_overlap(repo, mapping, map_sha, base_sha, files)
    if overlap_note is not None:
        reasons.append(overlap_note)
    index = mapping.get("files") if isinstance(mapping, dict) and isinstance(mapping.get("files"), dict) else {}
    coverage_hits, uncovered, non_python = _classify_files(repo, mapping, files, index)
    code_routed = decision is None or decision.get("needs_code_ci") is not False
    if non_python and code_routed:
        reasons.append(f"non-Python changes need the full tier: {', '.join(sorted(non_python))}")
    if uncovered and code_routed:
        reasons.append(f"changed Python code no recorded test executed: {', '.join(sorted(uncovered))}")
    changed_tests = sorted(
        {
            entry.path
            for entry in files
            if entry.path.endswith(".py") and _is_test_file(entry.path) and (repo / entry.path).is_file()
        }
    )
    importers = source_importers(repo, sorted(uncovered)) if uncovered else {}
    importer_tests = sorted({test for tests in importers.values() for test in tests})
    union = set(coverage_hits) | set(changed_tests) | set(fallback) | set(importer_tests)
    if len(union) > MAX_SELECTED_NODEIDS:
        reasons.append(f"selection of {len(union)} tests exceeds the {MAX_SELECTED_NODEIDS} limit")
        selected = set(fallback) | set(importer_tests)
    else:
        selected = union
    if not selected and any(not _is_inert(entry.path) for entry in files):
        reasons.append("no tests selected for the changed files")
    elapsed = _mono_time() - start
    peak_rss = _peak_rss_mb()
    counts = (
        f"coverage={len(coverage_hits)} changed_tests={len(changed_tests)} "
        f"fallback={len(fallback)} importers={len(importer_tests)} "
        f"selected={len(selected)} files={len(files)} behind={behind} elapsed={elapsed:.1f}s rss={peak_rss:.1f}MB"
    )
    would_escalate = bool(reasons)
    if reasons:
        reason = f"escalated to the full tier: {'; '.join(reasons)} ({counts})"
    else:
        reason = f"selected by coverage impact ({counts})"
    result = {
        "escalate": would_escalate,
        "reason": reason,
        "map_sha": map_sha,
        "base_sha": base_sha,
        "behind_commits": behind,
        "coverage_tests": sorted(coverage_hits),
        "changed_tests": changed_tests,
        "fallback_tests": sorted(fallback),
        "importer_tests": importer_tests,
        "uncovered_sources": sorted(uncovered),
        "selected": sorted(selected),
        "elapsed_seconds": elapsed,
        "peak_rss_mb": peak_rss,
    }
    if SELECTION_SHADOW:
        if reasons:
            shadow_reason = f"shadow mode: would escalate ({'; '.join(reasons)}); escalated to the full tier ({counts})"
        else:
            shadow_reason = (
                f"shadow mode: selection of {len(selected)} tests recorded; escalated to the full tier ({counts})"
            )
        result.update(
            {
                "escalate": True,
                "shadow": True,
                "would_escalate": would_escalate,
                "would_escalate_reasons": list(reasons),
                "reason": shadow_reason,
            }
        )
    return result


def _github_slug(repo: Path) -> str:
    proc = subprocess.run(
        ["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"],
        cwd=repo,
        text=True,
        capture_output=True,
        timeout=60,
    )
    if proc.returncode != 0:
        raise SelectionError(f"cannot resolve repository slug: {proc.stderr.strip()}")
    slug = proc.stdout.strip()
    if not _SLUG_RE.match(slug):
        raise SelectionError(f"invalid repository slug {slug!r}")
    return slug


def _gh_get(repo: Path, endpoint: str, params: dict[str, str], query: str) -> list[str]:
    command = ["gh", "api", "--method", "GET", endpoint, "--jq", query]
    for key, value in params.items():
        command += ["-f", f"{key}={value}"]
    proc = subprocess.run(command, cwd=repo, text=True, capture_output=True, timeout=120)
    if proc.returncode != 0:
        raise SelectionError(f"cannot query {endpoint}: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout.splitlines()


def _list_map_artifacts(repo: Path, slug: str, prefix: str) -> Iterator[tuple[str, int, str]]:
    runs = _gh_get(
        repo,
        f"repos/{slug}/actions/workflows/{PurePosixPath(TRUNK_WORKFLOW_PATH).name}/runs",
        {"branch": "develop", "event": "push", "per_page": str(MAX_MAP_BEHIND_COMMITS)},
        '.workflow_runs[] | "\\(.id)\\t\\(.head_sha)"',
    )
    for line in runs:
        run, head, _ = _split_row(line)
        if not run.isdigit() or not _SHA_RE.match(head):
            continue
        rows = _gh_get(
            repo,
            f"repos/{slug}/actions/runs/{run}/artifacts",
            {"name": f"{prefix}{head}"},
            '.artifacts[] | select(.expired == false) | "\\(.name)\\t\\(.created_at)"',
        )
        for row in rows:
            name, created, _ = _split_row(row)
            if name == f"{prefix}{head}":
                yield name, int(run), created


def _split_row(line: str) -> tuple[str, str, str]:
    parts = line.split("\t")
    while len(parts) < 3:
        parts.append("")
    return parts[0], parts[1], parts[2]


def _trusted_run_head(repo: Path, slug: str, run_id: int) -> str | None:
    proc = subprocess.run(
        [
            "gh",
            "api",
            f"repos/{slug}/actions/runs/{run_id}",
            "--jq",
            ".head_sha, .path, .event, .head_branch, .head_repository.full_name",
        ],
        cwd=repo,
        text=True,
        capture_output=True,
        timeout=60,
    )
    if proc.returncode != 0:
        return None
    fields = [line.strip() for line in proc.stdout.splitlines()]
    if len(fields) != 5:
        return None
    sha, path, event, branch, head_slug = fields
    if path != TRUNK_WORKFLOW_PATH or event != "push" or branch != "develop" or head_slug != slug:
        return None
    return sha if _SHA_RE.match(sha) else None


def _is_ancestor(repo: Path, sha: str, base_sha: str) -> bool:
    proc = subprocess.run(
        ["git", "merge-base", "--is-ancestor", sha, base_sha], cwd=repo, capture_output=True, timeout=120
    )
    return proc.returncode == 0


def _download_artifact(repo: Path, slug: str, run_id: int, name: str, target: Path) -> bool:
    proc = subprocess.run(
        ["gh", "run", "download", str(run_id), "-R", slug, "-n", name, "--dir", str(target)],
        cwd=repo,
        text=True,
        capture_output=True,
        timeout=600,
    )
    return proc.returncode == 0


def _read_downloaded_map(target: Path, head: str) -> tuple[dict, Path] | None:
    if not target.is_dir():
        return None
    candidates = sorted(target.rglob("*.json"))
    preferred = target / "map.json"
    if preferred in candidates:
        candidates.remove(preferred)
        candidates.insert(0, preferred)
    for candidate in candidates:
        try:
            payload = read_map(candidate)
        except SelectionError:
            continue
        if payload.get("sha") == head:
            return payload, candidate
    return None


def fetch_map(
    repo: Path, base_sha: str, dest: Path, prefix: str = MAP_ARTIFACT_PREFIX, slug: str | None = None
) -> dict:
    try:
        resolved = slug or _github_slug(repo)
        for name, run_id, _created in _list_map_artifacts(repo, resolved, prefix):
            head = _trusted_run_head(repo, resolved, run_id)
            if head is None or not _is_ancestor(repo, head, base_sha):
                continue
            target = dest / name
            if not _download_artifact(repo, resolved, run_id, name, target):
                continue
            found = _read_downloaded_map(target, head)
            if found is not None:
                return {
                    "status": "found",
                    "sha": head,
                    "path": str(found[1]),
                    "reason": f"newest coverage map from develop ancestor {head}",
                }
        return {
            "status": "missing",
            "sha": None,
            "path": None,
            "reason": "no coverage map artifact from a develop SHA that is an ancestor of the base",
        }
    except SelectionError as exc:
        return {"status": "missing", "sha": None, "path": None, "reason": str(exc)}


def _write_github_output(path: Path | None, values: dict[str, str]) -> None:
    if path is None:
        return
    with path.open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def _append_summary(path: Path | None, text: str) -> None:
    if path is None:
        return
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text + "\n")


def _cmd_build_map(args: argparse.Namespace) -> int:
    if not _SHA_RE.match(args.sha):
        print(f"invalid SHA {args.sha!r}", file=sys.stderr)
        return 2
    data_files = [path for pattern in args.data for path in sorted(pattern.parent.glob(pattern.name))]
    data_files = [path for path in data_files if path.is_file()]
    if args.expected_shards is not None and len(data_files) != args.expected_shards:
        print(f"expected {args.expected_shards} coverage data files, found {len(data_files)}", file=sys.stderr)
        return 1
    if not data_files:
        print("no coverage data files matched", file=sys.stderr)
        return 1
    try:
        mapping = build_map(data_files, args.repo_root, args.sha)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"cannot build coverage map: {exc}", file=sys.stderr)
        return 1
    write_map(mapping, args.out)
    print(f"coverage map for {args.sha}: {len(mapping['files'])} files from {len(data_files)} shard(s)")
    return 0


def _cmd_fetch_map(args: argparse.Namespace) -> int:
    result = fetch_map(args.repo_root, args.base_sha, args.dest_dir, slug=args.slug)
    payload = {"status": result["status"], "sha": result["sha"], "path": result["path"], "reason": result["reason"]}
    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    _write_github_output(
        args.github_output,
        {"map-status": result["status"], "map-sha": result["sha"] or "", "map-path": result["path"] or ""},
    )
    print(f"coverage map {result['status']}: {result['reason']}")
    return 0


def _resolve_base(repo: Path, base_sha: str | None, base_ref: str) -> tuple[str, str | None]:
    if base_sha:
        return base_sha, None
    try:
        return _git(repo, "rev-parse", "--verify", f"{base_ref}^{{commit}}").strip(), None
    except subprocess.CalledProcessError as exc:
        return "", f"cannot resolve base ref {base_ref}: {(exc.stderr or '').strip()}"


def _load_map_and_note(map_arg: Path | None) -> tuple[dict | None, str | None]:
    if map_arg is None:
        return None, "coverage map is missing"
    if not map_arg.is_file():
        return None, f"coverage map {map_arg} is missing"
    try:
        return read_map(map_arg), None
    except SelectionError as exc:
        return None, str(exc)


def _cmd_select(args: argparse.Namespace) -> int:
    repo = args.repo_root
    select_start = _mono_time()
    try:
        decision = read_decision(args.decision_in) if args.decision_in is not None else None
    except SelectionError as exc:
        decision = None
        decision_note = str(exc)
    else:
        decision_note = None
    base_sha, base_note = _resolve_base(repo, args.base_sha, args.base_ref)
    mapping, map_note = _load_map_and_note(args.map)
    if decision_note is not None:
        map_note = "; ".join(note for note in (map_note, decision_note) if note)
    try:
        if mapping is not None and base_sha:
            files = changed_files(repo, base_sha)
        elif base_sha:
            files = named_changed_files(repo, base_sha)
        else:
            files = []
            map_note = "; ".join(note for note in (map_note, base_note) if note) or map_note
        result = select_tests(repo, mapping, files, base_sha, decision=decision, map_note=map_note)
    except Exception as exc:
        try:
            fallback = _emergency_fallback(repo, base_sha)
        except Exception as fallback_exc:
            print(f"coverage selection failed: {exc}; fallback failed: {fallback_exc}", file=sys.stderr)
            return 2
        result = {
            "escalate": True,
            "reason": f"escalated to the full tier: coverage selection failed: {exc}",
            "map_sha": None,
            "base_sha": base_sha,
            "behind_commits": None,
            "coverage_tests": [],
            "changed_tests": [],
            "fallback_tests": fallback,
            "importer_tests": [],
            "uncovered_sources": [],
            "selected": fallback,
            "elapsed_seconds": _mono_time() - select_start,
            "peak_rss_mb": _peak_rss_mb(),
        }
        if SELECTION_SHADOW:
            result.update(
                {
                    "shadow": True,
                    "would_escalate": True,
                    "would_escalate_reasons": [f"coverage selection failed: {exc}"],
                }
            )
    if args.nodeids_out is not None:
        args.nodeids_out.parent.mkdir(parents=True, exist_ok=True)
        args.nodeids_out.write_text("".join(f"{node}\n" for node in result["selected"]), encoding="utf-8")
    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    if result.get("map_sha"):
        map_line = f"coverage map found: {result['map_sha']}"
    else:
        map_line = f"coverage map missing: {map_note}"
    summary_lines = [
        f"Coverage selection: {result['reason']}.",
        f"Map SHA: {result.get('map_sha') or 'none'}.",
        f"Would-be selected count: {len(result['selected'])}.",
        f"Would-escalate reasons: {'; '.join(result.get('would_escalate_reasons', [])) or 'none'}.",
        f"Elapsed: {result.get('elapsed_seconds', 0.0):.1f}s, peak RSS: {result.get('peak_rss_mb', 0.0):.1f}MB.",
    ]
    for line in summary_lines:
        _append_summary(args.summary, line)
    print(map_line)
    print(result["reason"])
    print(f"escalate={str(result['escalate']).lower()} selected={len(result['selected'])}")
    print(f"would_escalate={str(result.get('would_escalate', result['escalate'])).lower()}")
    print(f"elapsed={result.get('elapsed_seconds', 0.0):.1f}s rss={result.get('peak_rss_mb', 0.0):.1f}MB")
    return 0


def _cmd_prune(args: argparse.Namespace) -> int:
    try:
        node_ids = args.nodeids_in.read_text(encoding="utf-8").splitlines()
        collected = args.collected_in.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        print(f"cannot read prune inputs: {exc}", file=sys.stderr)
        return 1
    pruned = prune_missing_nodeids(args.repo_root, [node for node in node_ids if node], collected)
    args.nodeids_out.parent.mkdir(parents=True, exist_ok=True)
    args.nodeids_out.write_text("".join(f"{node}\n" for node in pruned), encoding="utf-8")
    print(f"pruned {len(node_ids)} selected IDs to {len(pruned)} runnable targets")
    return 0


def _junit_module_parts(classname: str) -> tuple[list[str], list[str]]:
    parts = classname.split(".") if classname else []
    extra: list[str] = []
    while len(parts) > 1 and not parts[-1].startswith("test_") and not parts[-1].endswith("_test"):
        extra.insert(0, parts.pop())
    return parts, extra


def junit_case_node_id(classname: str, name: str, file_attr: str | None = None) -> tuple[str, str]:
    path: str | None = None
    extra: list[str] = []
    if file_attr:
        stem = file_attr[: -len(".py")] if file_attr.endswith(".py") else file_attr
        dotted = stem.replace("/", ".")
        if classname == dotted:
            path = file_attr
        elif classname.startswith(dotted + "."):
            path = file_attr
            extra = classname[len(dotted) + 1 :].split(".")
    if path is None:
        module, extra = _junit_module_parts(classname)
        path = "/".join(module) + ".py"
    node = path if not extra else f"{path}::{'::'.join(extra)}"
    return f"{node}::{name}", path


def failed_node_ids(paths: list[Path]) -> tuple[list[str], int, list[str]]:
    failed: set[str] = set()
    parsed = 0
    unparsed: list[str] = []
    for path in paths:
        if not path.is_file():
            continue
        try:
            root = ET.parse(path).getroot()
        except (ET.ParseError, UnicodeDecodeError):
            unparsed.append(str(path))
            continue
        parsed += 1
        for case in root.iter("testcase"):
            if case.find("failure") is None and case.find("error") is None:
                continue
            node, _ = junit_case_node_id(case.get("classname", ""), case.get("name", ""), case.get("file"))
            failed.add(node)
    return sorted(failed), parsed, unparsed


def shadow_misses(selected: list[str], failed: list[str]) -> list[str]:
    have = set(selected)
    files = {entry for entry in selected if "::" not in entry}
    return sorted({node for node in failed if node not in have and node.split("::")[0] not in files})


def miss_report(selected: list[str], failed: list[str]) -> dict:
    missed = shadow_misses(selected, failed)
    return {
        "selected_count": len(selected),
        "failed": sorted(set(failed)),
        "missed": missed,
        "would_escalate": bool(missed),
    }


def _miss_junit_paths(raw: list[Path]) -> list[Path]:
    paths: list[Path] = []
    for entry in raw or []:
        if entry.is_dir():
            paths.extend(sorted(entry.rglob("*-junit.xml")))
        elif entry.is_file():
            paths.append(entry)
    return paths


def _cmd_misses(args: argparse.Namespace) -> int:
    notes: list[str] = []
    selected: list[str] = []
    if args.selection_in is None:
        notes.append("no selection input")
    else:
        try:
            payload = json.loads(args.selection_in.read_text(encoding="utf-8"))
            selected = [str(node) for node in payload["selected"]]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            notes.append(f"cannot read selection {args.selection_in}: {exc}")
    junit_paths = _miss_junit_paths(args.junit)
    if not junit_paths:
        notes.append("no junit reports")
    failed, parsed, unparsed = failed_node_ids(junit_paths)
    if junit_paths and not parsed:
        notes.append("no junit reports parsed")
    elif args.expected_shards is not None and parsed < args.expected_shards:
        notes.append(f"only {parsed} of {args.expected_shards} shard junit reports parsed")
    notes.extend(f"cannot parse junit report {name}" for name in unparsed)
    report = miss_report(selected, failed)
    if notes:
        report.update({"missed": [], "would_escalate": None, "available": False, "reason": "; ".join(notes)})
    else:
        report.update({"available": True, "reason": ""})
    lines = ["## Shadow selection misses"]
    picked = f"{len(selected)} test" if len(selected) == 1 else f"{len(selected)} tests"
    broken = f"{len(failed)} test" if len(failed) == 1 else f"{len(failed)} tests"
    if notes:
        lines.append(f"Shadow selection misses: not available ({'; '.join(notes)}).")
    elif not failed:
        lines.append(f"No failures in the medium-test shards; shadow selection of {picked} has nothing to miss.")
        lines.append(f"Would escalate on misses: {'yes' if report['would_escalate'] else 'no'}.")
    elif not report["missed"]:
        lines.append(f"No misses: all {broken} were covered by the shadow selection of {picked}.")
        lines.append(f"Would escalate on misses: {'yes' if report['would_escalate'] else 'no'}.")
    else:
        lines.append(f"Shadow selection of {picked} missed {len(report['missed'])} of {broken}.")
        lines.extend(f"- {node}" for node in report["missed"])
        lines.append(f"Would escalate on misses: {'yes' if report['would_escalate'] else 'no'}.")
    for line in lines:
        _append_summary(args.summary, line)
    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"misses={len(report['missed'])} failed={len(failed)} selected={len(selected)}")
    print(f"available={str(report['available']).lower()}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest="command", required=True)
    build_map_parser = sub.add_parser("build-map")
    build_map_parser.add_argument("--data", type=Path, action="append", required=True)
    build_map_parser.add_argument("--sha", required=True)
    build_map_parser.add_argument("--expected-shards", type=int, default=None)
    build_map_parser.add_argument("--out", type=Path, required=True)
    build_map_parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    build_map_parser.set_defaults(func=_cmd_build_map)
    fetch_map_parser = sub.add_parser("fetch-map")
    fetch_map_parser.add_argument("--base-sha", required=True)
    fetch_map_parser.add_argument("--dest-dir", type=Path, required=True)
    fetch_map_parser.add_argument("--slug", default=None)
    fetch_map_parser.add_argument("--json-out", type=Path, default=None)
    fetch_map_parser.add_argument("--github-output", type=Path, default=None)
    fetch_map_parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    fetch_map_parser.set_defaults(func=_cmd_fetch_map)
    select_parser = sub.add_parser("select")
    select_parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    select_parser.add_argument("--map", type=Path, default=None)
    select_parser.add_argument("--base-sha", default=None)
    select_parser.add_argument("--base-ref", default=os.environ.get("GITHUB_BASE_REF", "origin/develop"))
    select_parser.add_argument("--decision-in", type=Path, default=None)
    select_parser.add_argument("--nodeids-out", type=Path, default=None)
    select_parser.add_argument("--json-out", type=Path, default=None)
    select_parser.add_argument("--summary", type=Path, default=None)
    select_parser.set_defaults(func=_cmd_select)
    prune_parser = sub.add_parser("prune")
    prune_parser.add_argument("--nodeids-in", type=Path, required=True)
    prune_parser.add_argument("--collected-in", type=Path, required=True)
    prune_parser.add_argument("--nodeids-out", type=Path, required=True)
    prune_parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    prune_parser.set_defaults(func=_cmd_prune)
    misses_parser = sub.add_parser("misses")
    misses_parser.add_argument("--selection-in", type=Path, default=None)
    misses_parser.add_argument("--junit", type=Path, action="append", default=None)
    misses_parser.add_argument("--expected-shards", type=int, default=None)
    misses_parser.add_argument("--json-out", type=Path, default=None)
    misses_parser.add_argument("--summary", type=Path, default=None)
    misses_parser.set_defaults(func=_cmd_misses)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
