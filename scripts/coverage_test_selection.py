#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from _project.scripts import preflight_targets

MAP_VERSION = 2
UNIDENTIFIED_TESTCASE = "<unidentified testcase>"
CONTEXT_PHASES = ("setup", "run", "teardown")
MAP_ARTIFACT_PREFIX = "t2-coverage-map-"
MAX_MAP_BEHIND_COMMITS = 50
MAX_DISCOVERY_RUNS = 60
MAX_SELECTION_RUNTIME_SECONDS = 900.0
MAX_ARGV_NODEIDS = 10000
UNKNOWN_TEST_SECONDS = 1.0
TESTS_ROOT = "tests"
TRUNK_WORKFLOW_PATH = ".github/workflows/trunk-coverage.yml"
TRUSTED_MAP_EVENTS = ("push", "schedule", "workflow_dispatch")
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


def split_phase(context: str) -> tuple[str | None, str]:
    node, separator, phase = context.rpartition("|")
    if separator and phase in CONTEXT_PHASES:
        return (node if node.strip() else None, phase)
    return (context if context.strip() else None, "run")


def normalize_context(context: str) -> str | None:
    return split_phase(context)[0]


def _relative_name(repo: Path, filename: str) -> str | None:
    try:
        return Path(filename).resolve().relative_to(repo.resolve()).as_posix()
    except (OSError, ValueError):
        return None


def _classname_filename(repo: Path, classname: str) -> str | None:
    parts = [part for part in classname.split(".") if part]
    if not parts:
        return None
    for width in range(len(parts), 0, -1):
        candidate = "/".join(parts[:width]) + ".py"
        if (repo / candidate).is_file():
            return candidate
    test_widths = [
        width
        for width in range(len(parts), 0, -1)
        if parts[width - 1].startswith("test_") or parts[width - 1].endswith("_test")
    ]
    return "/".join(parts[: test_widths[0] if test_widths else len(parts)]) + ".py"


def _junit_module(filename: str) -> str:
    return filename[:-3].replace("/", ".") if filename.endswith(".py") else filename


def junit_case_node_id(repo: Path, case: ET.Element) -> str:
    name = (case.get("name") or "").strip()
    classname = (case.get("classname") or "").strip()
    filename = (case.get("file") or "").strip() or None
    if filename is not None and classname:
        module = _junit_module(filename)
        if classname != module and not classname.startswith(module + "."):
            filename = None
    if filename is None:
        filename = _classname_filename(repo, classname)
    if filename is None:
        return name or UNIDENTIFIED_TESTCASE
    module = _junit_module(filename)
    extra = classname[len(module) + 1 :].split(".") if classname.startswith(module + ".") else []
    return "::".join(part for part in (filename, *extra, name) if part)


def _junit_failed_nodeids(junit_files: list[Path], repo: Path) -> list[str]:
    failed: set[str] = set()
    for junit in junit_files:
        try:
            root = ET.parse(str(junit)).getroot()
        except (OSError, ET.ParseError) as exc:
            raise SelectionError(f"shard junit {junit} is unreadable: {exc}") from exc
        for case in root.iter("testcase"):
            if case.find("failure") is None and case.find("error") is None:
                continue
            failed.add(junit_case_node_id(repo, case))
    return sorted(failed)


def _statement_lines(repo: Path, data_file: str, filenames: list[str]) -> dict[str, list[int]]:
    from coverage import Coverage

    try:
        cov = Coverage(data_file=data_file, config_file=False)
        cov.load()
    except Exception as exc:
        raise SelectionError(f"coverage data {data_file} is unreadable: {exc}") from exc
    statements: dict[str, list[int]] = {}
    for filename in filenames:
        rel = _relative_name(repo, filename)
        if rel is None:
            continue
        try:
            _, found, _, _, _ = cov.analysis2(filename)
        except Exception:
            continue
        statements[rel] = sorted(found)
    return statements


def _read_execution_evidence(execution_files: list[Path], sha: str) -> tuple[set[str], dict[str, list[dict]]]:
    assigned: set[str] = set()
    outcomes: dict[str, list[dict]] = {}
    for path in execution_files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SelectionError(f"shard execution evidence {path} is unreadable: {exc}") from exc
        if not isinstance(payload, dict) or payload.get("commit_sha") != sha:
            raise SelectionError(f"shard execution evidence {path} is not for {sha}")
        if payload.get("complete") is not True:
            raise SelectionError(f"shard execution evidence {path} is incomplete")
        ids = payload.get("assigned_node_ids")
        records = payload.get("node_outcomes")
        if not isinstance(ids, list) or not isinstance(records, list):
            raise SelectionError(f"shard execution evidence {path} is malformed")
        if any(not isinstance(node, str) or not node for node in ids):
            raise SelectionError(f"shard execution evidence {path} has an invalid node id")
        duplicated = assigned.intersection(ids)
        if duplicated:
            raise SelectionError(f"node ids assigned to more than one shard: {sorted(duplicated)[:3]}")
        assigned.update(ids)
        for record in records:
            if isinstance(record, dict) and isinstance(record.get("node_id"), str):
                outcomes[record["node_id"]] = [r for r in record.get("reports") or [] if isinstance(r, dict)]
    return assigned, outcomes


def reconcile_with_execution(nodes: list[str], execution_files: list[Path], sha: str) -> dict[str, str]:
    assigned, outcomes = _read_execution_evidence(execution_files, sha)
    ghosts = sorted(set(nodes) - assigned)
    if ghosts:
        raise SelectionError(
            f"{len(ghosts)} map node(s) are not collected medium ids (mangled coverage contexts?): "
            + ", ".join(repr(node) for node in ghosts[:5])
        )
    uncovered: dict[str, str] = {}
    unexplained: list[str] = []
    recorded = set(nodes)
    for node in sorted(assigned - recorded):
        reports = outcomes.get(node)
        if not reports:
            unexplained.append(node)
        elif any(report.get("outcome") == "skipped" for report in reports):
            uncovered[node] = "skipped"
        elif any(report.get("phase") == "call" and report.get("outcome") in ("passed", "failed") for report in reports):
            uncovered[node] = "no-project-coverage"
        else:
            unexplained.append(node)
    if unexplained:
        raise SelectionError(
            f"{len(unexplained)} collected medium id(s) are absent from the map with no recorded outcome: "
            + ", ".join(repr(node) for node in unexplained[:5])
        )
    return uncovered


def build_map(
    data_files: list[Path],
    repo: Path,
    sha: str,
    junit_files: list[Path] | None = None,
    execution_files: list[Path] | None = None,
) -> dict:
    from coverage import CoverageData

    by_line: dict[str, dict[int, set[str]]] = {}
    setup_by_line: dict[str, dict[int, set[str]]] = {}
    with tempfile.TemporaryDirectory() as tmp:
        merged_path = str(Path(tmp) / "merged.dat")
        merged = CoverageData(merged_path)
        for path in data_files:
            shard = CoverageData(str(path))
            shard.read()
            merged.update(shard)
        merged.write()
        for filename in merged.measured_files():
            rel = _relative_name(repo, filename)
            if rel is None:
                continue
            for lineno, contexts in merged.contexts_by_lineno(filename).items():
                for context in contexts:
                    node, phase = split_phase(context)
                    if node is None:
                        continue
                    by_line.setdefault(rel, {}).setdefault(lineno, set()).add(node)
                    if phase == "setup":
                        setup_by_line.setdefault(rel, {}).setdefault(lineno, set()).add(node)
        nodes = sorted({node for lines in by_line.values() for hit in lines.values() for node in hit})
        positions = {node: index for index, node in enumerate(nodes)}
        statements = _statement_lines(repo, merged_path, list(merged.measured_files()))
    files: dict[str, dict[str, object]] = {}
    for rel, lines in by_line.items():
        entry: dict[str, object] = {
            "lines": {str(lineno): sorted(positions[node] for node in hit) for lineno, hit in sorted(lines.items())},
        }
        if rel in statements:
            entry["statements"] = statements[rel]
        setup = setup_by_line.get(rel, {})
        if setup:
            entry["setup"] = {
                str(lineno): sorted(positions[node] for node in hit) for lineno, hit in sorted(setup.items())
            }
        files[rel] = entry
    mapping: dict = {
        "version": MAP_VERSION,
        "sha": sha,
        "nodes": nodes,
        "files": files,
        "failed_nodes": _junit_failed_nodeids(list(junit_files or []), repo),
    }
    if execution_files is not None:
        mapping["uncovered_nodes"] = reconcile_with_execution(nodes, execution_files, sha)
    return mapping


def write_map(mapping: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(mapping, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def read_map(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SelectionError(f"coverage map {path} is unreadable: {exc}") from exc
    if not isinstance(payload, dict):
        raise SelectionError(f"coverage map {path} has an invalid envelope")
    version = payload.get("version")
    if version != MAP_VERSION:
        raise SelectionError(
            f"coverage map {path} uses unsupported version {version!r} (expected {MAP_VERSION}); "
            "rebuild it from a trunk-coverage.yml run"
        )
    sha = payload.get("sha")
    files = payload.get("files")
    nodes = payload.get("nodes")
    failed_nodes = payload.get("failed_nodes", [])
    if not isinstance(sha, str) or not _SHA_RE.match(sha) or not isinstance(files, dict):
        raise SelectionError(f"coverage map {path} has an invalid envelope")
    if not isinstance(nodes, list) or any(not isinstance(node, str) for node in nodes):
        raise SelectionError(f"coverage map {path} has an invalid node table")
    if not isinstance(failed_nodes, list) or any(not isinstance(node, str) for node in failed_nodes):
        raise SelectionError(f"coverage map {path} has an invalid failed-node list")
    uncovered = payload.get("uncovered_nodes", {})
    if not isinstance(uncovered, dict) or any(
        not isinstance(node, str) or not isinstance(reason, str) for node, reason in uncovered.items()
    ):
        raise SelectionError(f"coverage map {path} has an invalid uncovered-node table")
    for rel, entry in files.items():
        if not isinstance(entry, dict):
            raise SelectionError(f"coverage map {path} has an invalid entry for {rel}")
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


def _entry_nodes(entry: dict, nodes: list[str], lineno: int, table: str) -> set[str]:
    raw = entry.get(table)
    if not isinstance(raw, dict):
        return set()
    indices = raw.get(str(lineno))
    if not isinstance(indices, list):
        return set()
    return {nodes[index] for index in indices if isinstance(index, int) and 0 <= index < len(nodes)}


def lookup_hits(entry: dict, nodes: list[str], lines: list[int]) -> tuple[set[str], list[int], set[str]]:
    covered = entry.get("lines")
    if not isinstance(covered, dict):
        return set(), list(lines), set()
    raw_statements = entry.get("statements")
    statements = set(raw_statements) if isinstance(raw_statements, list) else None
    hits: set[str] = set()
    setup_hits: set[str] = set()
    missed: list[int] = []
    for lineno in lines:
        if statements is not None and lineno not in statements:
            missed.append(lineno)
            continue
        names = _entry_nodes(entry, nodes, lineno, "lines")
        if names:
            hits.update(names)
            setup_hits.update(_entry_nodes(entry, nodes, lineno, "setup"))
        else:
            missed.append(lineno)
    return hits, missed, setup_hits


def _is_test_file(path: str) -> bool:
    pure = PurePosixPath(path)
    return pure.parts[:1] == (TESTS_ROOT,) and pure.name.startswith("test_") and pure.suffix == ".py"


def _is_inert(path: str) -> bool:
    from _project.scripts.inert_paths import is_inert_path

    return is_inert_path(path)


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


def _path_hits(entry: dict, nodes: list[str]) -> set[str]:
    lines = entry.get("lines")
    if not isinstance(lines, dict):
        return set()
    hits: set[str] = set()
    for indices in lines.values():
        if isinstance(indices, list):
            hits.update(nodes[index] for index in indices if isinstance(index, int) and 0 <= index < len(nodes))
    return hits


def _setup_module_nodes(mapping: dict, setup_nodes: set[str]) -> set[str]:
    nodes = mapping.get("nodes")
    if not isinstance(nodes, list):
        return set()
    by_module: dict[str, set[str]] = {}
    for node in nodes:
        if isinstance(node, str) and node:
            by_module.setdefault(node.split("::", 1)[0], set()).add(node)
    expanded: set[str] = set()
    for node in setup_nodes:
        expanded.update(by_module.get(node.split("::", 1)[0], set()))
    return expanded


def _classify_files(
    repo: Path,
    mapping: dict | None,
    files: list[ChangedFile],
    index: dict,
    readers: dict[str, list[str]] | None = None,
) -> tuple[set[str], set[str], set[str], set[str]]:
    coverage_hits: set[str] = set()
    uncovered: set[str] = set()
    non_python: set[str] = set()
    reader_tests: set[str] = set()
    known_readers = readers or {}
    nodes: list[str] = []
    if mapping is not None:
        raw_nodes = mapping.get("nodes")
        if isinstance(raw_nodes, list):
            nodes = raw_nodes
    from _project.scripts.inert_paths import is_machine_read_doc, matches_exemption

    for entry in files:
        if is_machine_read_doc(entry.path) and matches_exemption(entry.path):
            found = sorted(test for test in known_readers.get(entry.path, ()) if (repo / test).is_file())
            if found:
                reader_tests.update(found)
            else:
                uncovered.add(entry.path)
            continue
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
            old = index.get(entry.old_path)
            if isinstance(old, dict):
                coverage_hits.update(_path_hits(old, nodes))
            uncovered.add(entry.path)
        if entry.status == "modified" and not entry.old_ranges:
            uncovered.add(entry.old_path)
            continue
        old = index.get(entry.old_path)
        if not isinstance(old, dict):
            uncovered.add(entry.old_path)
            continue
        lines = [line for old_start, old_count in entry.old_ranges for line in probe_lines(old_start, old_count)]
        hits, missed, setup_nodes = lookup_hits(old, nodes, lines)
        coverage_hits.update(hits)
        coverage_hits.update(_setup_module_nodes(mapping, setup_nodes))
        if missed:
            uncovered.add(entry.old_path)
    coverage_hits = {node for node in coverage_hits if (repo / node.split("::", 1)[0]).is_file()}
    reader_tests = {test for test in reader_tests if (repo / test).is_file()}
    return coverage_hits, uncovered, non_python, reader_tests


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
) -> tuple[list[ChangedFile], str | None, list[str]]:
    if mapping is None or map_sha is None:
        return files, None, []
    if map_sha == base_sha or not _is_ancestor(repo, map_sha, base_sha):
        return files, None, []
    try:
        develop_files = changed_files(repo, map_sha, base_sha)
    except SelectionError as exc:
        return files, str(exc), []
    develop_tests = sorted(
        {entry.path for entry in develop_files if _is_test_file(entry.path) and (repo / entry.path).is_file()}
    )
    overlap = {entry.path for entry in develop_files} | {entry.old_path for entry in develop_files}
    if not overlap:
        return files, None, develop_tests
    remapped = [
        ChangedFile(path=entry.path, old_path=entry.old_path, status="added", old_ranges=[])
        if entry.path in overlap or entry.old_path in overlap
        else entry
        for entry in files
    ]
    return remapped, None, develop_tests


def load_durations(path: Path) -> tuple[dict[str, float], float]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SelectionError(f"durations {path} are unreadable: {exc}") from exc
    tests = payload.get("tests")
    if not isinstance(payload, dict) or not isinstance(tests, dict):
        raise SelectionError(f"durations {path} have an invalid envelope")
    durations: dict[str, float] = {}
    for node, entry in tests.items():
        if isinstance(entry, dict) and isinstance(entry.get("p95_seconds"), (int, float)):
            durations[node] = float(entry["p95_seconds"])
    default = payload.get("default_seconds", UNKNOWN_TEST_SECONDS)
    if not isinstance(default, (int, float)):
        default = UNKNOWN_TEST_SECONDS
    return durations, float(default)


def estimate_runtime(entries: set[str], durations: dict[str, float], default: float) -> float:
    by_file: dict[str, float] = {}
    for node, seconds in durations.items():
        by_file[node.split("::", 1)[0]] = by_file.get(node.split("::", 1)[0], 0.0) + seconds
    total = 0.0
    for entry in entries:
        if entry in durations:
            total += durations[entry]
        elif "::" in entry:
            total += default
        else:
            total += by_file.get(entry, default)
    return total


def _limit_selection(
    union: set[str],
    fallback: list[str],
    importer_tests: list[str],
    develop_changed: list[str],
    failed: list[str],
    readers: list[str],
    durations: dict[str, float],
    default: float,
) -> tuple[set[str], float, list[str]]:
    runtime = estimate_runtime(union, durations, default)
    core = set(fallback) | set(importer_tests) | set(develop_changed) | set(failed) | set(readers)
    if runtime > MAX_SELECTION_RUNTIME_SECONDS:
        return (
            core,
            runtime,
            [
                f"selection of {len(union)} tests has an estimated runtime of {runtime:.1f}s "
                f"(limit {MAX_SELECTION_RUNTIME_SECONDS:.0f}s)"
            ],
        )
    if len(union) > MAX_ARGV_NODEIDS:
        return (
            core,
            runtime,
            [f"selection of {len(union)} tests exceeds the {MAX_ARGV_NODEIDS} argv limit; pass them by file instead"],
        )
    return union, runtime, []


def select_tests(
    repo: Path,
    mapping: dict | None,
    files: list[ChangedFile],
    base_sha: str,
    decision: dict | None = None,
    map_note: str | None = None,
    durations: dict[str, float] | None = None,
    durations_default: float = UNKNOWN_TEST_SECONDS,
    durations_error: str | None = None,
) -> dict:
    start = _mono_time()
    fallback = preflight_targets.map_tests(repo, _fallback_inputs(files))
    reasons: list[str] = []
    if durations_error is not None:
        reasons.append(durations_error)
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
    files, overlap_note, develop_changed_tests = _apply_develop_overlap(repo, mapping, map_sha, base_sha, files)
    if overlap_note is not None:
        reasons.append(overlap_note)
    index = mapping.get("files") if isinstance(mapping, dict) and isinstance(mapping.get("files"), dict) else {}
    from _project.scripts.inert_paths import load_readers

    readers = load_readers(repo)
    coverage_hits, uncovered, non_python, reader_tests = _classify_files(repo, mapping, files, index, readers)
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
    failed_nodes: list[str] = []
    if mapping is not None:
        raw_failed = mapping.get("failed_nodes", [])
        if isinstance(raw_failed, list):
            failed_nodes = sorted(
                {node for node in raw_failed if isinstance(node, str) and (repo / node.split("::", 1)[0]).is_file()}
            )
    union = (
        set(coverage_hits)
        | set(changed_tests)
        | set(develop_changed_tests)
        | set(fallback)
        | set(importer_tests)
        | set(reader_tests)
        | set(failed_nodes)
    )
    selected, runtime, limit_reasons = _limit_selection(
        union,
        fallback,
        importer_tests,
        develop_changed_tests,
        failed_nodes,
        reader_tests,
        durations or {},
        durations_default,
    )
    reasons.extend(limit_reasons)
    if not selected and any(not _is_inert(entry.path) for entry in files):
        reasons.append("no tests selected for the changed files")
    elapsed = _mono_time() - start
    peak_rss = _peak_rss_mb()
    counts = (
        f"coverage={len(coverage_hits)} changed_tests={len(changed_tests)} "
        f"develop_changed={len(develop_changed_tests)} fallback={len(fallback)} importers={len(importer_tests)} "
        f"readers={len(reader_tests)} failed={len(failed_nodes)} selected={len(selected)} files={len(files)} behind={behind} "
        f"runtime={runtime:.1f}s elapsed={elapsed:.1f}s rss={peak_rss:.1f}MB"
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
        "develop_changed_tests": develop_changed_tests,
        "fallback_tests": sorted(fallback),
        "importer_tests": importer_tests,
        "reader_tests": sorted(reader_tests),
        "failed_nodes": failed_nodes,
        "uncovered_sources": sorted(uncovered),
        "selected": sorted(selected),
        "estimated_runtime_seconds": runtime,
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
        {"branch": "develop", "per_page": str(MAX_DISCOVERY_RUNS)},
        '.workflow_runs[] | "\\(.id)\\t\\(.head_sha)\\t\\(.event)"',
    )
    for line in runs:
        run, head, event = _split_row(line)
        if not run.isdigit() or not _SHA_RE.match(head) or event not in TRUSTED_MAP_EVENTS:
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


def _trusted_run(repo: Path, slug: str, run_id: int) -> tuple[str, str] | None:
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
        raise SelectionError(
            f"cannot query repos/{slug}/actions/runs/{run_id}: {proc.stderr.strip() or proc.stdout.strip()}"
        )
    fields = [line.strip() for line in proc.stdout.splitlines()]
    if len(fields) != 5:
        return None
    sha, path, event, branch, head_slug = fields
    if path != TRUNK_WORKFLOW_PATH or event not in TRUSTED_MAP_EVENTS or branch != "develop" or head_slug != slug:
        return None
    return (sha, event) if _SHA_RE.match(sha) else None


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
            trusted = _trusted_run(repo, resolved, run_id)
            if trusted is None or not _is_ancestor(repo, trusted[0], base_sha):
                continue
            head, event = trusted
            target = dest / name
            if not _download_artifact(repo, resolved, run_id, name, target):
                continue
            found = _read_downloaded_map(target, head)
            if found is not None:
                return {
                    "status": "found",
                    "sha": head,
                    "path": str(found[1]),
                    "run_id": run_id,
                    "event": event,
                    "artifact": name,
                    "reason": f"newest coverage map from develop ancestor {head} (run {run_id}, event {event})",
                }
        return {
            "status": "missing",
            "sha": None,
            "path": None,
            "run_id": None,
            "event": None,
            "artifact": None,
            "reason": "no coverage map artifact from a develop SHA that is an ancestor of the base",
        }
    except SelectionError as exc:
        return {
            "status": "lookup-failed",
            "sha": None,
            "path": None,
            "run_id": None,
            "event": None,
            "artifact": None,
            "reason": str(exc),
        }


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
    junit_files = [path for pattern in (args.junit or []) for path in sorted(pattern.parent.glob(pattern.name))]
    junit_files = [path for path in junit_files if path.is_file()]
    if args.junit is not None and len(junit_files) < len(data_files):
        print(
            f"expected one junit report per coverage data file, found {len(junit_files)} "
            f"for {len(data_files)} data file(s)",
            file=sys.stderr,
        )
        return 1
    execution_files = [path for pattern in args.execution for path in sorted(pattern.parent.glob(pattern.name))]
    execution_files = [path for path in execution_files if path.is_file()]
    if len(execution_files) != len(data_files):
        print(
            f"expected one execution evidence file per coverage data file, found {len(execution_files)} "
            f"for {len(data_files)} data file(s)",
            file=sys.stderr,
        )
        return 1
    try:
        mapping = build_map(
            data_files, args.repo_root, args.sha, junit_files=junit_files, execution_files=execution_files
        )
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"cannot build coverage map: {exc}", file=sys.stderr)
        return 1
    write_map(mapping, args.out)
    print(
        f"coverage map for {args.sha}: {len(mapping['files'])} files, "
        f"{len(mapping['nodes'])} tests from {len(data_files)} shard(s), "
        f"{len(mapping['failed_nodes'])} failed, {len(mapping.get('uncovered_nodes', {}))} without coverage"
    )
    return 0


def _cmd_fetch_map(args: argparse.Namespace) -> int:
    result = fetch_map(args.repo_root, args.base_sha, args.dest_dir, slug=args.slug)
    payload = {key: result[key] for key in ("status", "sha", "path", "run_id", "event", "artifact", "reason")}
    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    _write_github_output(
        args.github_output,
        {
            "map-status": result["status"],
            "map-sha": result["sha"] or "",
            "map-path": result["path"] or "",
            "map-run-id": str(result["run_id"] or ""),
            "map-event": result["event"] or "",
            "map-artifact": result["artifact"] or "",
        },
    )
    if result["status"] == "lookup-failed":
        print(f"::warning::coverage map lookup failed: {result['reason']}")
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
        durations_arg = (
            args.durations if args.durations is not None else REPO_ROOT / "tests/fixtures/medium_shard_durations.json"
        )
        durations, durations_default = load_durations(durations_arg)
        durations_error = None
    except SelectionError as exc:
        durations, durations_default = {}, UNKNOWN_TEST_SECONDS
        durations_error = str(exc)
    try:
        if mapping is not None and base_sha:
            files = changed_files(repo, base_sha)
        elif base_sha:
            files = named_changed_files(repo, base_sha)
        else:
            files = []
            map_note = "; ".join(note for note in (map_note, base_note) if note) or map_note
        result = select_tests(
            repo,
            mapping,
            files,
            base_sha,
            decision=decision,
            map_note=map_note,
            durations=durations,
            durations_default=durations_default,
            durations_error=durations_error,
        )
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
            "develop_changed_tests": [],
            "fallback_tests": fallback,
            "importer_tests": [],
            "reader_tests": [],
            "failed_nodes": [],
            "uncovered_sources": [],
            "selected": fallback,
            "estimated_runtime_seconds": 0.0,
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
        f"Estimated runtime: {result.get('estimated_runtime_seconds', 0.0):.1f}s.",
        f"Elapsed: {result.get('elapsed_seconds', 0.0):.1f}s, peak RSS: {result.get('peak_rss_mb', 0.0):.1f}MB.",
    ]
    for line in summary_lines:
        _append_summary(args.summary, line)
    print(map_line)
    print(result["reason"])
    print(f"escalate={str(result['escalate']).lower()} selected={len(result['selected'])}")
    print(f"would_escalate={str(result.get('would_escalate', result['escalate'])).lower()}")
    print(f"elapsed={result.get('elapsed_seconds', 0.0):.1f}s rss={result.get('peak_rss_mb', 0.0):.1f}MB")
    print(f"estimated_runtime={result.get('estimated_runtime_seconds', 0.0):.1f}s")
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


def failed_node_ids(paths: list[Path], repo: Path) -> tuple[list[str], int, list[str]]:
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
            failed.add(junit_case_node_id(repo, case))
    return sorted(failed), parsed, unparsed


def shadow_misses(selected: list[str], failed: list[str]) -> list[str]:
    have = set(selected)
    files = {entry for entry in selected if "::" not in entry}
    return sorted({node for node in failed if node not in have and node.split("::")[0] not in files})


def selection_escalates(payload: dict) -> bool:
    key = "would_escalate" if payload.get("shadow") is True else "escalate"
    return payload.get(key) is True


def miss_report(selected: list[str], failed: list[str], escalated: bool = False) -> dict:
    outside = shadow_misses(selected, failed)
    missed = [] if escalated else outside
    return {
        "selected_count": len(selected),
        "failed": sorted(set(failed)),
        "missed": missed,
        "selection_escalates": escalated,
        "caught_by_escalation": outside if escalated else [],
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
    escalated = False
    if args.selection_in is None:
        notes.append("no selection input")
    else:
        try:
            payload = json.loads(args.selection_in.read_text(encoding="utf-8"))
            selected = [str(node) for node in payload["selected"]]
            escalated = selection_escalates(payload)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            notes.append(f"cannot read selection {args.selection_in}: {exc}")
    junit_paths = _miss_junit_paths(args.junit)
    if not junit_paths:
        notes.append("no junit reports")
    failed, parsed, unparsed = failed_node_ids(junit_paths, args.repo_root)
    if junit_paths and not parsed:
        notes.append("no junit reports parsed")
    elif args.expected_shards is not None and parsed < args.expected_shards:
        notes.append(f"only {parsed} of {args.expected_shards} shard junit reports parsed")
    notes.extend(f"cannot parse junit report {name}" for name in unparsed)
    report = miss_report(selected, failed, escalated)
    if notes:
        report.update(
            {
                "missed": [],
                "selection_escalates": None,
                "caught_by_escalation": [],
                "would_escalate": None,
                "available": False,
                "reason": "; ".join(notes),
            }
        )
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
        if report["caught_by_escalation"]:
            lines.append(
                f"No misses: the selection of {picked} would escalate to the full tier, "
                f"which runs all {broken}, including {len(report['caught_by_escalation'])} outside the selection."
            )
        else:
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
    build_map_parser.add_argument("--junit", type=Path, action="append", default=None)
    build_map_parser.add_argument("--execution", type=Path, action="append", required=True)
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
    select_parser.add_argument("--durations", type=Path, default=None)
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
