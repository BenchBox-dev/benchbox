#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

try:
    from _project.scripts import theseus_bootstrap as bootstrap
except ModuleNotFoundError:
    import theseus_bootstrap as bootstrap

CURL_TIMEOUT_SECONDS = 120
HOST_HOME_ENV = "BENCHBOX_TEST_HOST_HOME"
WARNING = "WARNING: Theseus install not verified locally"


def install_cache(env: dict[str, str] | None = None) -> Path:
    environment = os.environ if env is None else env
    configured = environment.get(bootstrap.CACHE_ENV, "").strip()
    if configured:
        return Path(configured)
    host_home = environment.get(HOST_HOME_ENV, "").strip()
    account_home = Path(host_home) if host_home else Path(os.path.expanduser(f"~{getpass.getuser()}"))
    return account_home / ".cache" / "theseus" / "installs"


def curl_fetch(url: str) -> bytes:
    if not url.startswith("https://"):
        raise bootstrap.BootstrapError("artifacts are downloaded over https only")
    command = [
        "curl",
        "--fail",
        "--silent",
        "--show-error",
        "--location",
        "--proto",
        "=https",
        "--proto-redir",
        "=https",
        "--max-filesize",
        str(bootstrap.MAX_BYTES),
        "--max-time",
        str(CURL_TIMEOUT_SECONDS),
        "--user-agent",
        bootstrap.USER_AGENT,
        url,
    ]
    try:
        result = subprocess.run(command, capture_output=True, check=False)
    except OSError as exc:
        raise bootstrap.BootstrapError(f"cannot run curl for {url}: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()[:300]
        raise bootstrap.BootstrapError(f"cannot download {url}: curl exit {result.returncode}: {detail}")
    return result.stdout


def prefetch(root: Path, commit: str, cache: Path, fetch: Callable[[str], bytes] = curl_fetch) -> bootstrap.Plan:
    lock = bootstrap.read_lock(root, commit)
    (cache / "artifacts").mkdir(parents=True, exist_ok=True)
    return bootstrap.build_plan(lock, cache, fetch)


def run(root: Path, ref: str, cache: Path, fetch: Callable[[str], bytes] = curl_fetch) -> int:
    reached: list[str] = []

    def tracked(url: str) -> bytes:
        data = fetch(url)
        reached.append(url)
        return data

    try:
        commit = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", f"{ref}^{{commit}}"],
            capture_output=True,
            encoding="utf-8",
            check=True,
        ).stdout.strip()
        plan = prefetch(root, commit, cache, tracked)
    except (bootstrap.BootstrapError, subprocess.CalledProcessError, OSError) as exc:
        if reached:
            print(f"theseus prefetch: refused: {exc}", file=sys.stderr)
            return 1
        print(f"{WARNING}: {exc}; the tests that need it are skipped and reported as not run locally")
        return 0
    names = ", ".join(f"{name} {version} sha256={digest[:12]}" for name, version, digest, _, _ in plan.wheels)
    print(f"theseus prefetch: {names} verified against .github/theseus-lock.json in {cache}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prefetch and hash-verify the locked Theseus wheels into the install cache."
    )
    parser.add_argument("--repo", default=".")
    parser.add_argument("--ref", default="HEAD")
    parser.add_argument("--cache-dir", default="")
    args = parser.parse_args(argv)
    cache = Path(args.cache_dir) if args.cache_dir else install_cache()
    return run(Path(args.repo), args.ref, cache)


if __name__ == "__main__":
    raise SystemExit(main())
