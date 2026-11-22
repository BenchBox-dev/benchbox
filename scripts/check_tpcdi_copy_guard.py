#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

HASH_LIST = "_sources/tpc-di/databricks-tpc-di/FORBIDDEN_HASHES.json"
APPROVED_HASHES = "quality/comment-policy-approved-external-hashes.json"
TOOLS_DIR = "benchbox/_binaries/tpc-di/tools/"
TPC_DIRS = ("benchbox/_binaries/tpc-di/", "_sources/tpc-di/")
ETL_DIR = "benchbox/core/tpcdi/etl/"
ETL_PROVENANCE = "benchbox/core/tpcdi/etl/PROVENANCE.json"
FORBIDDEN_GROUPS = ("generator_and_tools", "audit_snapshots", "digen_output")
FORBIDDEN_NAMES = frozenset(
    {
        "pdgf.jar",
        "digen.jar",
        "digen.asp",
        "digen_build.zip",
        "tpc-di.jar",
        "tpc-di_src.jar",
        "commons-net-3.3.jar",
        "commons.lang3.time.jar",
        "javassist.jar",
        "log4j-1.2.15.jar",
        "xercesimpl.jar",
        "xml-apis.jar",
        "constants.properties",
        "pseudotextgenerator.properties",
        "log4j.xml",
        "pdgf.log",
    }
)
FORBIDDEN_NAME_PATTERNS = (re.compile(r"tpc-di-.*\.xml"), re.compile(r".*-audit\.xml"))
TOOLS_ZIP_MARKERS = ("tpc-di", "tpcdi", "digen", "pdgf")


def git_output(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True).stdout.decode("utf-8")


def tree_files(root: Path) -> list[str]:
    listed = git_output(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    return sorted(name for name in listed.split("\0") if name and (root / name).is_file())


def gitlink_entries(root: Path) -> list[str]:
    listed = git_output(root, "ls-files", "-s", "-z")
    return sorted(
        entry.split("\t", 1)[1] for entry in listed.split("\0") if entry.startswith("160000 ") and "\t" in entry
    )


def load_forbidden(root: Path) -> tuple[set[str], int]:
    hash_list = json.loads((root / HASH_LIST).read_text(encoding="utf-8"))
    entries = [entry for group in FORBIDDEN_GROUPS for entry in hash_list["forbidden_by_content"][group]]
    if not entries:
        raise ValueError("the forbidden hash list is empty")
    return {entry["sha256"] for entry in entries}, max(entry["bytes"] for entry in entries)


def load_official(root: Path) -> set[str]:
    return {entry["sha256"] for entry in json.loads((root / APPROVED_HASHES).read_text(encoding="utf-8"))["files"]}


def file_digests(root: Path, files: list[str], max_bytes: int) -> dict[str, str]:
    digests = {}
    for name in files:
        path = root / name
        if path.stat().st_size <= max_bytes:
            digests[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digests


def recorded_hash_violations(digests: dict[str, str], forbidden: set[str], official: set[str]) -> list[str]:
    return [
        name
        for name, digest in digests.items()
        if digest in forbidden and not (name.startswith(TOOLS_DIR) and digest in official)
    ]


def official_misplaced_violations(digests: dict[str, str], official: set[str]) -> list[str]:
    return [
        name
        for name, digest in digests.items()
        if digest in official and not any(name.startswith(directory) for directory in TPC_DIRS)
    ]


def misplaced_tool_violations(files: list[str]) -> list[str]:
    found = []
    for name in files:
        if name.startswith(TOOLS_DIR):
            continue
        base = name.rsplit("/", 1)[-1].lower()
        marked = base.replace("_", "-")
        is_tools_zip = base.endswith(".zip") and any(marker in marked for marker in TOOLS_ZIP_MARKERS)
        is_named = base in FORBIDDEN_NAMES or any(pattern.fullmatch(base) for pattern in FORBIDDEN_NAME_PATTERNS)
        if is_named or base.endswith(".dict") or is_tools_zip:
            found.append(name)
    return found


def listed_etl_sql(root: Path) -> set[str]:
    path = root / ETL_PROVENANCE
    if not path.is_file():
        return set()
    return {entry["path"] for entry in json.loads(path.read_text(encoding="utf-8"))["files"]}


def unlisted_sql_violations(root: Path, files: list[str]) -> list[str]:
    listed = listed_etl_sql(root)
    return [name for name in files if name.startswith(ETL_DIR) and name.endswith(".sql") and name not in listed]


def check(root: Path) -> list[str]:
    files = tree_files(root)
    forbidden, max_bytes = load_forbidden(root)
    official = load_official(root)
    digests = file_digests(root, files, max_bytes)
    problems = []
    problems += [
        f"recorded project file outside official tool files: {n}"
        for n in recorded_hash_violations(digests, forbidden, official)
    ]
    problems += [
        f"official archive file outside the TPC directories: {n}"
        for n in official_misplaced_violations(digests, official)
    ]
    problems += [f"tool file outside {TOOLS_DIR}: {n}" for n in misplaced_tool_violations(files)]
    problems += [f"ETL SQL not listed in {ETL_PROVENANCE}: {n}" for n in unlisted_sql_violations(root, files)]
    problems += [f"gitlink entry: {n}" for n in gitlink_entries(root)]
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fail when copied project files or misplaced TPC-DI tool files are in the tree."
    )
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        problems = check(args.root.resolve())
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print(f"copy guard: cannot run: {error!r}", file=sys.stderr)
        return 2
    if problems:
        print("\n".join(sorted(problems)), file=sys.stderr)
        print(f"copy guard: {len(problems)} violations", file=sys.stderr)
        return 1
    print("copy guard: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
