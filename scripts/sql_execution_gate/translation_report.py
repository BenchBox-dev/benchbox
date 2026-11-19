from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypedDict

EMIT = Path(__file__).with_name("emit.py")
VENDOR_ARCHIVE = re.compile(r"benchbox/_vendor/benchbox_sqlglot-\d+\.\d+\.\d+\.\d+-py3-none-any\.whl\.zip")
VENDOR_PIN = "benchbox/_vendor/sqlglot.json"
DEFAULT_SCALE = 0.01

Translations = dict[str, dict[str, dict[str, str]]]


class TranslationChange(TypedDict):
    platform: str
    benchmark: str
    query: str
    before: str | None
    after: str | None


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, encoding="utf-8", errors="replace"
    ).stdout.strip()


def default_jobs() -> int:
    return max(1, min(os.cpu_count() or 1, 8))


@contextmanager
def checkout(repo: Path, rev: str, scratch: Path, tag: str, vendor_from: str | None = None) -> Iterator[Path]:
    path = scratch / f"checkout-{tag}"
    _git(repo, "worktree", "add", "--detach", str(path), rev)
    try:
        if vendor_from is not None:
            for stale in (path / "benchbox" / "_vendor").glob("benchbox_sqlglot-*.whl.zip"):
                stale.unlink()
            archives = [
                name
                for name in _git(
                    repo, "ls-tree", "-r", "--name-only", vendor_from, "--", "benchbox/_vendor"
                ).splitlines()
                if VENDOR_ARCHIVE.fullmatch(name)
            ]
            _git(path, "checkout", vendor_from, "--", VENDOR_PIN, *archives)
        yield path
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(path)], cwd=repo, check=False)


def emit_translations(root: Path, scratch: Path, tag: str, jobs: int, scale: float = DEFAULT_SCALE) -> Translations:
    helper = scratch / f"emit_{tag}.py"
    shutil.copy(EMIT, helper)
    environment = {**os.environ, "PYTHONPATH": str(root)}
    launch = "import runpy, sys; sys.argv = sys.argv[1:]; runpy.run_path(sys.argv[0], run_name='__main__')"
    processes: list[tuple[subprocess.Popen[bytes], Path]] = []
    for shard in range(jobs):
        out = scratch / f"translations_{tag}_{shard}.json"
        command = [
            sys.executable,
            "-c",
            launch,
            str(helper),
            "--translations",
            "--out",
            str(out),
            "--scale",
            str(scale),
            "--shard",
            f"{shard}/{jobs}",
        ]
        processes.append((subprocess.Popen(command, cwd=root, env=environment, stdout=subprocess.DEVNULL), out))
    failed = [process.args for process, _ in processes if process.wait() != 0]
    if failed:
        raise RuntimeError(f"translation emission failed in {root} for {len(failed)} of {jobs} shard(s)")
    merged: Translations = {}
    for _, out in processes:
        payload = json.loads(out.read_text(encoding="utf-8"))
        loaded = Path(payload["benchbox_root"]).resolve()
        if loaded != root.resolve():
            raise RuntimeError(f"translation emission loaded benchbox from {loaded}, not {root}")
        merged.update(payload["translations"])
    return dict(sorted(merged.items()))


def diff_translations(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[TranslationChange]:
    changes: list[TranslationChange] = []
    for platform in sorted(set(before) | set(after)):
        old_platform, new_platform = before.get(platform, {}), after.get(platform, {})
        for benchmark in sorted(set(old_platform) | set(new_platform)):
            old, new = old_platform.get(benchmark, {}), new_platform.get(benchmark, {})
            for query in sorted(set(old) | set(new), key=_query_key):
                if old.get(query) != new.get(query):
                    changes.append(
                        {
                            "platform": platform,
                            "benchmark": benchmark,
                            "query": query,
                            "before": old.get(query),
                            "after": new.get(query),
                        }
                    )
    return changes


def _query_key(query: str) -> tuple[tuple[int, str], ...]:
    return tuple((0, part.zfill(12)) if part.isdigit() else (1, part) for part in re.split(r"(\d+)", query) if part)


def translation_changes(
    repo: Path,
    before_rev: str,
    after_rev: str,
    *,
    before_vendor_from: str | None = None,
    after_vendor_from: str | None = None,
    jobs: int | None = None,
) -> list[TranslationChange]:
    workers = jobs or default_jobs()
    scratch = Path(tempfile.mkdtemp(prefix="translation_report_"))
    try:
        with (
            checkout(repo, before_rev, scratch, "before", before_vendor_from) as before_root,
            checkout(repo, after_rev, scratch, "after", after_vendor_from) as after_root,
        ):
            before = emit_translations(before_root, scratch, "before", workers)
            after = emit_translations(after_root, scratch, "after", workers)
        return diff_translations(before, after)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--before", required=True)
    parser.add_argument("--after", required=True)
    parser.add_argument("--before-vendor-from")
    parser.add_argument("--after-vendor-from")
    parser.add_argument("--jobs", type=int, default=default_jobs())
    parser.add_argument("--out", type=Path)
    parser.add_argument("--render", action="store_true")
    args = parser.parse_args(argv)
    started = time.monotonic()
    changes = translation_changes(
        args.repo.resolve(),
        args.before,
        args.after,
        before_vendor_from=args.before_vendor_from,
        after_vendor_from=args.after_vendor_from,
        jobs=args.jobs,
    )
    elapsed = time.monotonic() - started
    if args.out:
        args.out.write_text(json.dumps(changes, indent=1, sort_keys=True), encoding="utf-8")
    if args.render:
        from scripts import sqlglot_delivery

        print(sqlglot_delivery.render_translation_changes(changes), end="")
    else:
        for change in changes:
            print(f"{change['platform']} / {change['benchmark']} / {change['query']}")
    print(f"{len(changes)} changed translation(s) in {elapsed:.0f}s with {args.jobs} job(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
