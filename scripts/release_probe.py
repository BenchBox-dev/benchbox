from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

PROBE_TAG = "v0.0.0rc0"
PROBE_VERSION = PROBE_TAG.removeprefix("v")
VARIANTS = ("reuse", "fallback")
PROBE_TREE_REF = "refs/probe/trunk"
GATE_ARTIFACT_ID = 9001
DIST_ARTIFACT_ID = 9002
STAGE_ARTIFACT_ID = 9100
SCRIPTS = Path(__file__).resolve().parent
_LOCK_VERSION = re.compile(r'(\[\[package\]\]\nname = "benchbox"\nversion = )"[^"]*"')


def load_sibling(name: str) -> ModuleType:
    path = SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_probe_{name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"{path} cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def classify_run(event: str, probe: str, tag: str, ref_type: str) -> tuple[str, list[str]]:
    if event == "push" or (event == "workflow_dispatch" and probe == "none"):
        mode = "real"
    elif event == "workflow_dispatch" and probe in VARIANTS:
        mode = "probe"
    else:
        return "refused", [f"event {event!r} with probe input {probe!r} is neither a release nor a probe run"]
    problems: list[str] = []
    if mode == "real" and tag == PROBE_TAG:
        problems.append(f"{PROBE_TAG} is the synthetic probe tag and cannot be released")
    if mode == "probe":
        if tag != PROBE_TAG:
            problems.append(f"a probe runs only with the synthetic tag {PROBE_TAG}, not {tag!r}")
        if ref_type != "branch":
            problems.append(f"a probe runs only from a branch, not a {ref_type or 'missing'} ref")
    return mode, problems


def bump_version(root: Path, version: str = PROBE_VERSION) -> None:
    _run([sys.executable, "scripts/update_version.py", "--version", version, "--update-pyproject"], root)
    lock = root / "uv.lock"
    updated, count = _LOCK_VERSION.subn(rf'\g<1>"{version}"', lock.read_text(encoding="utf-8"), count=1)
    if count != 1:
        raise ValueError(f"{lock} has no benchbox package version line")
    lock.write_text(updated, encoding="utf-8")


def checked_out_commit(root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, encoding="utf-8", errors="replace"
    )
    return result.stdout.strip()


def commit_probe_release(root: Path, version: str = PROBE_VERSION) -> str:
    bump_version(root, version)
    identity = ["-c", "user.name=release-probe", "-c", "user.email=release-probe@localhost"]
    subprocess.run(["git", *identity, "commit", "-q", "-a", "-m", f"probe release {version}"], cwd=root, check=True)
    return checked_out_commit(root)


def _utc_stamp(moment: dt.datetime) -> str:
    return moment.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_admitted(dist: Path, output: Path, tag: str, head_sha: str, environ: dict[str, str] | None = None) -> None:
    env = os.environ if environ is None else environ
    if tag != PROBE_TAG:
        raise ValueError(f"only the synthetic tag {PROBE_TAG} can be admitted by a probe")
    admitted = load_sibling("release_admitted_dist")
    consumer = admitted._consumer
    attempt = int(env.get("GITHUB_RUN_ATTEMPT", "1"))
    repository_id = int(env.get("GITHUB_REPOSITORY_ID", "1"))
    sums = dist / admitted.SHA256SUMS
    producer = {
        "schema": 1,
        "repository": consumer.REPOSITORY,
        "repository_id": repository_id,
        "head_repository_id": repository_id,
        "workflow_path": consumer.WORKFLOW,
        "head_sha": head_sha,
        "run_id": int(env.get("GITHUB_RUN_ID", "1")),
        "run_attempt": attempt,
        "job_id": 1,
        "job_name": consumer.JOB_NAME,
        "artifact_name": f"dist-{head_sha}-attempt-{attempt}",
        "sha256sums_sha256": hashlib.sha256(sums.read_bytes()).hexdigest(),
        "files": consumer._files(dist),
    }
    output.mkdir(parents=True)
    for name in (*producer["files"], admitted.SHA256SUMS):
        (output / name).write_bytes((dist / name).read_bytes())
    (output / consumer.PRODUCER_RECEIPT).write_text(json.dumps(producer, indent=2) + "\n", encoding="utf-8")
    receipt = {
        "tag": tag,
        "tag_object": head_sha,
        "head_sha": head_sha,
        "version": tag.removeprefix("v"),
        "producer": producer,
        "artifact_id": 1,
        "artifact_digest": f"sha256:{hashlib.sha256(sums.read_bytes()).hexdigest()}",
        "expires_at": _utc_stamp(dt.datetime.now(dt.UTC) + dt.timedelta(days=1)),
    }
    (output / admitted.ADMISSION_RECEIPT).write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    admitted.verify_admitted(output, tag, head_sha)


@dataclass
class FakeGitHub:
    repository: str
    head_sha: str
    run_id: int
    candidate_dist: Path | None = None
    canary_conclusion: str = "success"
    canary_age_hours: float = 1.0
    canary_commit: str | None = None
    now: dt.datetime | None = None

    def _clock(self) -> dt.datetime:
        return self.now or dt.datetime.now(dt.UTC)

    def _candidate_files(self) -> dict[str, bytes]:
        if self.candidate_dist is None:
            raise ValueError("this fake GitHub holds no candidate distribution")
        return {path.name: path.read_bytes() for path in sorted(self.candidate_dist.iterdir()) if path.is_file()}

    def _digest_lines(self) -> bytes:
        reuse = load_sibling("release_reuse_uat_evidence")
        assert self.candidate_dist is not None
        wheel, sdist = reuse.distribution_files(self.candidate_dist)
        return "".join(f"{reuse.sha256_hex(path.read_bytes())}  {path.name}\n" for path in (wheel, sdist)).encode()

    @property
    def tag(self) -> str:
        return f"trunk-{self.head_sha}"

    def stage_names(self) -> list[str]:
        return [f"release-uat-{self.tag}-stage{stage}-probe" for stage in (1, 2, 3)]

    def gh_json(self, *args: str) -> Any:
        path = args[0]
        prefix = f"repos/{self.repository}/actions/"
        if path.startswith(f"{prefix}workflows/trunk-uat.yml/runs?"):
            if self.candidate_dist is None:
                return {"workflow_runs": []}
            started = _utc_stamp(self._clock())
            return {
                "workflow_runs": [
                    {
                        "id": self.run_id,
                        "status": "completed",
                        "conclusion": "success",
                        "event": "workflow_dispatch",
                        "head_branch": "develop",
                        "head_sha": self.head_sha,
                        "created_at": started,
                        "run_attempt": 1,
                    }
                ]
            }
        if path == f"{prefix}runs/{self.run_id}/jobs?filter=latest&per_page=100":
            names = ["plan", "admit", "UAT stage 1 probe", "UAT stage 2 probe", "UAT stage 3 probe"]
            names += ["trunk UAT gate", "trunk UAT record"]
            return {"jobs": [{"name": name, "conclusion": "success", "run_attempt": 1} for name in names]}
        match = re.fullmatch(
            rf"{re.escape(prefix)}artifacts\?name=(dist-[0-9a-f]{{40}}-attempt-(\d+))&per_page=\d+", path
        )
        if match is not None:
            found = self.candidate_dist is not None and match.group(2) == "1"
            listed = [{"id": DIST_ARTIFACT_ID, "name": match.group(1), "expired": False}] if found else []
            return {"artifacts": listed}
        if path.startswith(f"{prefix}workflows/release-canary.yml/runs?"):
            started = self._clock() - dt.timedelta(hours=self.canary_age_hours)
            return {
                "workflow_runs": [
                    {
                        "id": self.run_id,
                        "status": "completed",
                        "conclusion": self.canary_conclusion,
                        "head_sha": self.canary_commit or self.head_sha,
                        "event": "schedule",
                        "head_branch": "develop",
                        "created_at": _utc_stamp(started),
                        "run_started_at": _utc_stamp(started),
                    }
                ]
            }
        raise ValueError(f"the probe's fake GitHub has no route for {path}")

    def artifact_listing(self, repository: str, run_id: int) -> list[dict[str, Any]]:
        if self.candidate_dist is None or run_id != self.run_id:
            return []
        gate = {"id": GATE_ARTIFACT_ID, "name": f"trunk-uat-gate-{self.head_sha}", "expired": False}
        stages = [
            {"id": STAGE_ARTIFACT_ID + number, "name": name, "expired": False}
            for number, name in enumerate(self.stage_names(), start=1)
        ]
        return [gate, *stages]

    def download_artifact(self, repository: str, artifact_id: int) -> dict[str, bytes]:
        if artifact_id == DIST_ARTIFACT_ID:
            return self._candidate_files()
        if artifact_id == GATE_ARTIFACT_ID:
            digest_file = self._digest_lines()
            summary = {
                "verdict": "green",
                "source_commit_sha": self.head_sha,
                "distribution_digest": hashlib.sha256(digest_file).hexdigest(),
            }
            return {"dist-digest.txt": digest_file, "uat-gate-summary.json": json.dumps(summary).encode()}
        raise ValueError(f"the probe's fake GitHub holds no artifact {artifact_id}")

    def install(self, module: ModuleType) -> None:
        module._gh_json = self.gh_json
        module._artifact_listing = self.artifact_listing
        module._download_artifact = self.download_artifact


def run_canary(head_sha: str, repository: str, repo_dir: Path, fake: FakeGitHub | None = None) -> int:
    canary = load_sibling("release_canary_freshness")
    (fake or FakeGitHub(repository, head_sha, int(os.environ.get("GITHUB_RUN_ID", "1")))).install(canary._reuse)
    return canary.main(
        ["check", "--head-sha", head_sha, "--github-repository", repository, "--repo-dir", str(repo_dir)]
    )


def _read_outputs(path: Path) -> dict[str, str]:
    outputs: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            outputs[key] = value
    return outputs


def expected_decision_problems(variant: str, outputs: dict[str, str], run_id: int, head_sha: str) -> list[str]:
    problems: list[str] = []
    if variant == "reuse":
        expected = {
            "reuse": "true",
            "source_kind": "trunk",
            "source_tag": f"trunk-{head_sha}",
            "source_sha": head_sha,
            "source_run_id": str(run_id),
        }
        problems = [
            f"the reuse variant expected {key}={value!r}, got {outputs.get(key)!r}"
            for key, value in expected.items()
            if outputs.get(key) != value
        ]
    elif outputs.get("reuse") != "false" or any(key.startswith("source_") for key in outputs):
        problems.append(f"the fallback variant expected reuse=false and no source, got {outputs}")
    return problems


def run_decide(
    variant: str,
    tag: str,
    head_sha: str,
    repository: str,
    candidate_dist: Path,
    final_dist: Path,
    work: Path,
    repo_dir: Path,
    github_output: Path,
    run_id: int,
) -> int:
    if tag != PROBE_TAG or variant not in VARIANTS:
        print(f"::error::A probe decides only for {PROBE_TAG} with a reuse or fallback variant.", file=sys.stderr)
        return 1
    checked_out = checked_out_commit(repo_dir)
    if checked_out != head_sha:
        print(f"::error::The checkout is at {checked_out}, not the probed commit {head_sha}.", file=sys.stderr)
        return 1
    release_commit = commit_probe_release(repo_dir)
    subprocess.run(["git", "update-ref", PROBE_TREE_REF, release_commit], cwd=repo_dir, check=True)
    reuse = load_sibling("release_reuse_uat_evidence")
    FakeGitHub(repository, head_sha, run_id, candidate_dist if variant == "reuse" else None).install(reuse)
    reuse.TRUNK_REF = PROBE_TREE_REF
    status = reuse.main(
        [
            "decide",
            "--tag",
            tag,
            "--head-sha",
            release_commit,
            "--github-repository",
            repository,
            "--final-dist",
            str(final_dist),
            "--work",
            str(work),
            "--repo-dir",
            str(repo_dir),
            "--github-output",
            str(github_output),
        ]
    )
    problems = expected_decision_problems(variant, _read_outputs(github_output), run_id, head_sha)
    for problem in problems:
        print(f"::error::{problem}", file=sys.stderr)
    return 1 if status or problems else 0


def _handle_guard(args: argparse.Namespace) -> int:
    mode, problems = classify_run(args.event, args.probe, args.tag, args.ref_type)
    for problem in problems:
        print(f"::error::{problem}", file=sys.stderr)
    if not problems:
        print(f"release-v2 run mode: {mode}")
    return 1 if problems else 0


def _run(argv: list[str], cwd: Path) -> None:
    subprocess.run(argv, cwd=cwd, check=True)


def _verify_distributions(root: Path, directory: Path) -> None:
    files = [str(path) for path in sorted(directory.iterdir()) if path.name.endswith((".whl", ".tar.gz"))]
    _run([sys.executable, "scripts/verify_distribution_binaries.py", *files], root)
    _run([sys.executable, "-m", "scripts.verify_sqlglot_runtime_distribution", *files], root)


def _build_distributions(root: Path, directory: Path) -> None:
    _run(["uv", "build", "--out-dir", str(directory)], root)
    _verify_distributions(root, directory)


def prepare(root: Path, work: Path, tag: str, head_sha: str) -> None:
    if tag != PROBE_TAG:
        raise ValueError(f"only the synthetic tag {PROBE_TAG} can be prepared by a probe")
    _run([sys.executable, "scripts/bundled_binary_manifest.py"], root)
    _build_distributions(root, work / "candidate")
    bump_version(root)
    dist = work / "dist"
    _build_distributions(root, dist)
    lines = [
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
        for path in sorted(dist.iterdir())
        if path.name.endswith((".whl", ".tar.gz"))
    ]
    (dist / "SHA256SUMS").write_text("".join(lines), encoding="utf-8")
    build_admitted(dist, work / "admitted", tag, head_sha)


def _handle_prepare(args: argparse.Namespace) -> int:
    prepare(args.root, args.work, args.tag, args.head_sha)
    return 0


def _handle_canary(args: argparse.Namespace) -> int:
    return run_canary(args.head_sha, args.github_repository, args.repo_dir)


def _handle_decide(args: argparse.Namespace) -> int:
    return run_decide(
        args.variant,
        args.tag,
        args.head_sha,
        args.github_repository,
        args.candidate_dist,
        args.final_dist,
        args.work,
        args.repo_dir,
        args.github_output,
        args.source_run,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seams that let release-v2 run as a probe without publishing.")
    sub = parser.add_subparsers(dest="command", required=True)

    guard = sub.add_parser("guard")
    guard.add_argument("--event", required=True)
    guard.add_argument("--probe", default="")
    guard.add_argument("--tag", required=True)
    guard.add_argument("--ref-type", default="")
    guard.set_defaults(handler=_handle_guard)

    prep = sub.add_parser("prepare")
    prep.add_argument("--root", type=Path, default=Path("."))
    prep.add_argument("--work", type=Path, required=True)
    prep.add_argument("--tag", required=True)
    prep.add_argument("--head-sha", required=True)
    prep.set_defaults(handler=_handle_prepare)

    canary = sub.add_parser("canary")
    canary.add_argument("--head-sha", required=True)
    canary.add_argument("--github-repository", required=True)
    canary.add_argument("--repo-dir", type=Path, default=Path("."))
    canary.set_defaults(handler=_handle_canary)

    decide = sub.add_parser("decide")
    decide.add_argument("--variant", required=True, choices=VARIANTS)
    decide.add_argument("--tag", required=True)
    decide.add_argument("--head-sha", required=True)
    decide.add_argument("--github-repository", required=True)
    decide.add_argument("--candidate-dist", type=Path, required=True)
    decide.add_argument("--final-dist", type=Path, required=True)
    decide.add_argument("--work", type=Path, required=True)
    decide.add_argument("--repo-dir", type=Path, default=Path("."))
    decide.add_argument("--github-output", type=Path, required=True)
    decide.add_argument("--source-run", type=int, required=True)
    decide.set_defaults(handler=_handle_decide)

    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
