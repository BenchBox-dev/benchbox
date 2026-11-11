from __future__ import annotations

import argparse
import importlib.util
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

Fetch = Callable[..., None]


def _load_consumer() -> ModuleType:
    path = Path(__file__).with_name("release_artifact_consumer.py")
    spec = importlib.util.spec_from_file_location("_release_line_consumer", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"{path} cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


consumer = _load_consumer()


def _git(root: Path, *args: str, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        env={**os.environ, **(env or {})},
        timeout=600,
    )
    return result.stdout.strip()


def _no_pull_requests(_path: str) -> list[dict[str, Any]]:
    return []


def _final_version(tag: str) -> None:
    from packaging.version import InvalidVersion, Version

    if re.fullmatch(consumer.TAG_PATTERN, tag) is None:
        raise ValueError("invalid version tag")
    try:
        version = Version(tag[1:])
    except InvalidVersion as exc:
        raise ValueError(f"{tag} is not a version tag") from exc
    if version.is_prerelease or version.is_devrelease or version.is_postrelease or version.local:
        raise ValueError(f"{tag} is not a final release; a line starts from a final tag")


def _creation_tree(root: Path, base: str, source: str) -> str:
    paths = sorted(consumer.creation_review_paths(root, base, source))
    with tempfile.TemporaryDirectory(prefix="release-line-index-") as temporary:
        env = {"GIT_INDEX_FILE": str(Path(temporary) / "index")}
        _git(root, "read-tree", base, env=env)
        for path in paths:
            entry = _git(root, "ls-tree", source, "--", path)
            if not entry:
                _git(root, "update-index", "--force-remove", "--", path, env=env)
                continue
            header, _ = entry.split("\t", 1)
            mode, _kind, blob = header.split()
            _git(root, "update-index", "--add", "--cacheinfo", f"{mode},{blob},{path}", env=env)
        return _git(root, "write-tree", env=env)


def create_line(root: Path, tag: str, fetch: Fetch = consumer.fetch_hosted_refs) -> tuple[str, str]:
    _final_version(tag)
    fetch(root, tag, prune=True)
    branch = consumer.maintenance_branch(tag[1:])
    if consumer.remote_branch_tip(root, branch) is not None:
        raise ValueError(f"{branch} already exists on the hosted repository")
    base = _git(root, "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}")
    source = _git(root, "rev-parse", "--verify", f"refs/remotes/origin/{consumer.PRODUCER_BRANCH}^{{commit}}")
    tree = _creation_tree(root, base, source)
    commit = _git(
        root,
        "commit-tree",
        tree,
        "-p",
        base,
        "-m",
        f"chore(release): carry {consumer.PRODUCER_BRANCH}'s reviewer files onto {branch}",
        "-m",
        f"{consumer.CREATION_SOURCE_TRAILER}: {source}",
    )
    consumer.verify_line(root, branch, commit, _no_pull_requests)
    return branch, commit


def main(argv: list[str] | None = None, fetch: Fetch = consumer.fetch_hosted_refs) -> int:
    parser = argparse.ArgumentParser(description="Create the verified creation commit of a release/X.Y.x line.")
    parser.add_argument("--source", type=Path, default=Path("."))
    parser.add_argument("--tag", required=True, help="final vX.Y.Z tag the line starts from")
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--push", action="store_true", help="push the verified commit as the new line")
    args = parser.parse_args(argv)
    try:
        root = args.source.resolve()
        branch, commit = create_line(root, args.tag, fetch)
        print(f"{branch} creation commit {commit} verified")
        if args.push:
            _git(root, "push", args.remote, f"{commit}:refs/heads/{branch}")
            print(f"pushed {commit} to {args.remote} as {branch}")
        else:
            print("not pushed; rerun with --push to create the branch")
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"Release line creation failed: {exc}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
