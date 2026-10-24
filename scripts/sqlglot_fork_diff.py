from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIN = "benchbox/_vendor/sqlglot.json"
REPOSITORY = "https://github.com/BenchBox-dev/benchbox-sqlglot"
SHA = re.compile(r"[a-f0-9]{40}")
PRIORITY = ("downstream/", "CHANGELOG", "sqlglot/benchbox/")
DEFAULT_MAX_BYTES = 50_000


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "--literal-pathspecs", "-c", "core.quotePath=false", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError(f"git {' '.join(args[:2])} failed: {result.stderr.strip()}")
    return result.stdout


def pin_sha(text: str) -> str:
    sha = json.loads(text)["fork_sha"]
    if not isinstance(sha, str) or not SHA.fullmatch(sha):
        raise ValueError("Runtime pin has no valid fork_sha")
    return sha


def pinned_sha_at(ref: str) -> str:
    return pin_sha(_git(ROOT, "show", f"{ref}:{PIN}"))


def working_tree_sha() -> str:
    return pin_sha((ROOT / PIN).read_text(encoding="utf-8"))


def _priority(path: str) -> tuple[int, str]:
    for rank, prefix in enumerate(PRIORITY):
        if path.startswith(prefix):
            return rank, path
    return len(PRIORITY), path


def _fence(body: str, info: str = "") -> str:
    longest = max((len(run) for run in re.findall(r"`+", body)), default=0)
    mark = "`" * max(3, longest + 1)
    return f"{mark}{info}\n{body}\n{mark}"


def render(fork: Path, old: str, new: str, max_bytes: int, repository: str) -> str:
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", old, new], cwd=fork, capture_output=True, check=False
    ).returncode
    direction = "forward" if ancestor == 0 else "not-descendant (rollback or divergent history)"
    names = sorted(_git(fork, "diff", "--name-only", "-z", old, new).split("\0"), key=_priority)
    names = [name for name in names if name]
    commits = _git(fork, "log", "--no-merges", "--format=- %h %s", f"{old}..{new}").strip()
    stat = _git(fork, "diff", "--stat=100", old, new).rstrip()
    lines = [
        "## SQLGlot fork diff",
        "",
        f"Fork: {repository}",
        f"Old fork_sha: `{old}`",
        f"New fork_sha: `{new}`",
        f"Direction: {direction}",
        "",
        "### Commits",
        "",
        _fence(commits or "(none reachable from the new sha)"),
        "",
        "### Files",
        "",
        _fence(stat or "(no file changes)"),
        "",
        "### Diff",
    ]
    used = sum(len(line) + 1 for line in lines)
    omitted: list[str] = []
    for name in names:
        patch = _git(fork, "diff", old, new, "--", name)
        if not patch.strip():
            raise ValueError(f"git produced no diff for the changed path {name!r}")
        block = "\n" + _fence(patch.rstrip(), "diff")
        if used + len(block) > max_bytes:
            omitted.append(name)
            continue
        lines.append(block)
        used += len(block)
    if omitted:
        lines.extend(
            [
                "",
                f"Omitted for size ({len(omitted)} files); read them with "
                f"`git --literal-pathspecs diff {old} {new} -- <path>` in a clone of the fork:",
                "",
                _fence("\n".join(omitted)),
            ]
        )
    return "\n".join(lines) + "\n"


def fork_diff(old: str, new: str, repository: str, max_bytes: int) -> str:
    with tempfile.TemporaryDirectory() as directory:
        fork = Path(directory)
        _git(fork, "init", "--bare", "--quiet")
        _git(fork, "fetch", "--quiet", "--no-tags", "--filter=blob:none", repository, old, new)
        return render(fork, old, new, max_bytes, repository)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Print the fork diff between two benchbox-sqlglot commits for an adoption PR body."
    )
    parser.add_argument("--old", help="Previous fork sha (default: the pin at --base-ref)")
    parser.add_argument("--new", help="New fork sha (default: the pin in the working tree)")
    parser.add_argument("--base-ref", default="origin/develop")
    parser.add_argument("--repository", default=REPOSITORY, help="Fork URL or local path")
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args(argv)
    try:
        old = arguments.old or pinned_sha_at(arguments.base_ref)
        new = arguments.new or working_tree_sha()
        for sha in (old, new):
            if not SHA.fullmatch(sha):
                raise ValueError(f"Expected a full 40-character fork sha, got {sha!r}")
        text = fork_diff(old, new, arguments.repository, arguments.max_bytes)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"SQLGlot fork diff: {error}", file=sys.stderr)
        return 1
    if arguments.output:
        arguments.output.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
