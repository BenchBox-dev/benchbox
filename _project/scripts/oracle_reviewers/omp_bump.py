from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import candidate_checks, omp_candidate, omp_pin
from .omp_candidate import ChainFactory

BUILD_HOST = "https://build.stencil.so"
API_TIMEOUT_SECONDS = 30
CHANNEL = "stable"
HELP_DIFF_LINES = 120
POLICY_PATH = omp_candidate.POLICY_PATH
TARGETS = {
    "linux-x64": ("linux-x86_64", "omp-linux-x64"),
    "linux-arm64": ("linux-arm64", "omp-linux-arm64"),
    "darwin-arm64": ("macos-arm64", "omp-darwin-arm64"),
}
CURRENT, READY, RED = "current", "ready", "red"
_OMP_BLOCK = re.compile(r"(?m)^omp:\n(?:[ \t]+[^\n]*\n|\n(?=[ \t]))*")
_RELEASE_PATH = re.compile(r"/d/omp/(?P<build>[0-9]{8}-[0-9]{6}-[0-9a-f]{7,40})/(?P<name>[A-Za-z0-9._-]+)")

JsonGetter = Callable[[str], Any]


class BumpError(Exception):
    pass


@dataclass(frozen=True)
class Release:
    version: str
    build_id: str
    builds: Mapping[str, omp_pin.Build]

    def pin(self) -> omp_pin.Pin:
        return omp_pin.Pin(self.version, dict(self.builds))


@dataclass(frozen=True)
class Outcome:
    state: str
    version: str
    detail: str
    policy_text: str = ""
    body: str = ""


def get_json(url: str) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": omp_pin.USER_AGENT, "Accept": "application/json"})
    try:
        with omp_pin.build_opener().open(request, timeout=API_TIMEOUT_SECONDS) as response:
            return json.loads(response.read())
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise BumpError(
            f"the build host answer for {url.split('?')[0]} could not be read: {type(exc).__name__}"
        ) from exc


def _version_key(version: str) -> tuple[int, int, int]:
    major, minor, patch = (int(part) for part in version.split("-")[0].split("+")[0].split("."))
    return major, minor, patch


def _release_from(answers: Mapping[str, Any]) -> Release:
    builds: dict[str, omp_pin.Build] = {}
    identity: set[tuple[str, str]] = set()
    for target, answer in answers.items():
        build = answer.get("build") if isinstance(answer, dict) else None
        if (
            not isinstance(build, dict)
            or not isinstance(build.get("version"), str)
            or not isinstance(build.get("id"), str)
        ):
            raise BumpError(f"the build host answer for {target} names no build")
        if omp_pin.VERSION_PATTERN.fullmatch(build["version"]) is None or build.get("channel") != CHANNEL:
            raise BumpError(f"the build host answer for {target} is not a {CHANNEL} build with a valid version")
        identity.add((build["version"], build["id"]))
        files = {item.get("name"): item for item in build.get("files") or [] if isinstance(item, dict)}
        entry = files.get(TARGETS[target][1])
        if not isinstance(entry, dict):
            raise BumpError(f"the build host lists no {TARGETS[target][1]} file for {build['version']}")
        match = _RELEASE_PATH.fullmatch(str(entry.get("url")))
        if match is None or match["build"] != build["id"] or match["name"] != entry["name"]:
            raise BumpError(f"the build host url for {entry.get('name')} does not belong to build {build['id']}")
        digest = entry.get("sha256")
        if not isinstance(digest, str) or omp_pin.SHA256_PATTERN.fullmatch(digest) is None:
            raise BumpError(f"the build host publishes no valid sha256 for {entry['name']}")
        builds[target] = omp_pin.Build(BUILD_HOST + entry["url"], digest)
    if len(identity) != 1:
        raise BumpError(f"the build host answers disagree on the latest build: {sorted(identity)}")
    version, build_id = next(iter(identity))
    return Release(version, build_id, builds)


def fetch_release(getter: JsonGetter = get_json) -> Release:
    answers = {
        target: getter(f"{BUILD_HOST}/api/products/omp/latest/{api_target}?channel={CHANNEL}")
        for target, (api_target, _) in TARGETS.items()
    }
    return _release_from(answers)


def render_pin(pin: omp_pin.Pin) -> str:
    lines = ["omp:", f"  version: {pin.version}", "  builds:"]
    for target in TARGETS:
        if target in pin.builds:
            lines += [
                f"    {target}:",
                f"      url: {pin.builds[target].url}",
                f"      sha256: {pin.builds[target].sha256}",
            ]
    return "\n".join(lines) + "\n"


def replace_pin(policy_text: str, pin: omp_pin.Pin) -> str:
    blocks = _OMP_BLOCK.findall(policy_text)
    if len(blocks) != 1:
        raise BumpError("the reviewer policy must hold exactly one omp section")
    replaced = _OMP_BLOCK.sub(lambda _: render_pin(pin), policy_text, count=1)
    if omp_pin.pin_from_text(replaced) != pin:
        raise BumpError("the rewritten omp section does not parse back to the candidate pin")
    return replaced


def verify_downloads(release: Release, directory: Path, fetch: omp_pin.Fetch) -> tuple[dict[str, Path], list[str]]:
    paths: dict[str, Path] = {}
    problems: list[str] = []
    for target, build in release.builds.items():
        first, second = directory / f"{target}-1", directory / f"{target}-2"
        fetch(build.url, first)
        fetch(build.url, second)
        digests = (omp_pin.sha256_of(first), omp_pin.sha256_of(second))
        if len(set(digests)) != 1:
            problems.append(f"{target}: two downloads hash differently ({digests[0]} and {digests[1]})")
        elif digests[0] != build.sha256:
            problems.append(f"{target}: the download hashes to {digests[0]}, the build host publishes {build.sha256}")
        second.unlink()
        paths[target] = first
    return paths, problems


def help_diff(previous: Path, candidate: Path, home: Path, expected_sha256: str) -> str:
    if omp_pin.sha256_of(candidate) != expected_sha256:
        raise BumpError("the candidate download does not hash to the published build; its --help was not run")
    candidate.chmod(0o755)
    _, before = candidate_checks.run_omp(previous, home, "--help")
    _, after = candidate_checks.run_omp(candidate, home, "--help")
    lines = list(
        difflib.unified_diff(before.splitlines(), after.splitlines(), "previous", "candidate", lineterm="", n=0)
    )
    if not lines:
        return "no difference"
    shown = lines[:HELP_DIFF_LINES]
    return "\n".join(
        shown + ([f"... {len(lines) - HELP_DIFF_LINES} more lines"] if len(lines) > HELP_DIFF_LINES else [])
    )


def render_body(
    release: Release, previous: omp_pin.Pin, report: omp_candidate.Report, flags_diff: str, host: str
) -> str:
    rows = "\n".join(
        f"| {target} | `{build.sha256}` | {'verified on this runner' if target == host else 'hashed only'} |"
        for target, build in release.builds.items()
    )
    return (
        f"## Description\n\nBumps the pinned omp build from {previous.version} to {release.version} "
        f"(build `{release.build_id}`). Only the `omp:` section of the reviewer policy changes.\n\n"
        "The compatibility suite carries the behavioural evidence; the oracle reviewing this pull request runs the "
        "previous build and reads only the YAML.\n\n"
        "## Per-platform builds\n\nEach build was downloaded twice over TLS; both downloads and the checksum the build "
        "host publishes agree.\n\n| Platform | SHA-256 | Execution |\n| --- | --- | --- |\n"
        f"{rows}\n\n`--version` on {host}: `{report.transcript}`\n\n"
        f"## Compatibility suite on {host}\n\n```\n{report.render()}\n```\n\n"
        f"## `--help` against {previous.version}\n\n```diff\n{flags_diff}\n```\n\n"
        "## Before merge\n\nPost the line printed by `make omp-candidate-check VERSION="
        f"{release.version}` on the owner's Mac (darwin-arm64) as a comment on this pull request. `make pr-arm` "
        "refuses this pull request until that line from the owner matches the pin it carries.\n"
    )


def prepare(
    policy_text: str,
    *,
    getter: JsonGetter = get_json,
    fetch: omp_pin.Fetch | None = None,
    chain_factory: ChainFactory | None = None,
    cache_root: Path | None = None,
) -> Outcome:
    previous = omp_pin.pin_from_text(policy_text)
    release = fetch_release(getter)
    if _version_key(release.version) <= _version_key(previous.version):
        return Outcome(CURRENT, release.version, f"omp {previous.version} is the newest published stable build")
    host = omp_pin.current_platform()
    download = fetch or omp_pin.download
    with tempfile.TemporaryDirectory(prefix="omp-bump-") as root_name:
        root = Path(root_name)
        paths, problems = verify_downloads(release, root, download)
        if problems:
            return Outcome(RED, release.version, "; ".join(problems))
        by_url = {build.url: paths[target] for target, build in release.builds.items()}

        def local_copy(url: str, destination: Path) -> None:
            shutil.copyfile(by_url[url], destination)

        candidate = release.pin()
        binary = paths[host] if host in paths else None
        kwargs: dict[str, Any] = {"fetch": local_copy, "binary": binary}
        if chain_factory is not None:
            kwargs["chain_factory"] = chain_factory
        report = omp_candidate.check_candidate(candidate, **kwargs)
        if not report.releasable:
            return Outcome(RED, release.version, report.render())
        home = root / "help-home"
        home.mkdir()
        installed = omp_pin.install(previous, host, cache_root, download)
        if host not in release.builds:
            raise BumpError(f"the release publishes no {host} build to run --help from")
        diff = help_diff(installed, paths[host], home, release.builds[host].sha256)
        policy = replace_pin(policy_text, candidate)
        return Outcome(
            READY,
            release.version,
            "the candidate passed the compatibility suite",
            policy,
            render_body(release, previous, report, diff, host),
        )


def _publish(values: Mapping[str, str]) -> None:
    for key, value in values.items():
        print(f"{key}={value}")
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.writelines(f"{key}={value}\n" for key, value in values.items())


def command_prepare(args: argparse.Namespace) -> int:
    out = Path(args.out_dir)
    try:
        outcome = prepare(Path(args.policy).read_text(encoding="utf-8"))
    except (OSError, BumpError, omp_pin.PinError) as exc:
        print(f"omp-bump: error: {exc}", file=sys.stderr)
        return 1
    out.mkdir(parents=True, exist_ok=True)
    (out / "detail.txt").write_text(outcome.detail + "\n", encoding="utf-8")
    if outcome.state == READY:
        (out / "policy.yml").write_text(outcome.policy_text, encoding="utf-8")
        (out / "pr-body.md").write_text(outcome.body, encoding="utf-8")
    _publish({"state": outcome.state, "version": outcome.version})
    print(outcome.detail, file=sys.stderr if outcome.state == RED else sys.stdout)
    return 1 if outcome.state == RED else 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="omp-bump")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser("prepare")
    prepare_parser.add_argument("--policy", default=str(POLICY_PATH))
    prepare_parser.add_argument("--out-dir", required=True)
    prepare_parser.set_defaults(handler=command_prepare)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
