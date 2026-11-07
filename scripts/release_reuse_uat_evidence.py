from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import traceback
import zipfile
from pathlib import Path
from typing import Any

FINAL_TAG = re.compile(r"v(\d+\.\d+\.\d+)")
RC_TAG = re.compile(r"v(\d+\.\d+\.\d+)rc(\d+)")
GATE_JOB_NAME = "release UAT gate"
WORKFLOW_FILE = "release-v2.yml"
GUARDED_TREES = (
    "tests/uat",
    "tests/utilities",
    "tests/__init__.py",
    "docker",
    ".github/actions/release-uat-env",
    "Makefile",
    "make",
    "scripts/uat-bring-up",
)
WORKFLOW_PATH = ".github/workflows/release-v2.yml"
STAGE_JOBS = ("uat-stage1", "uat-stage2", "uat-stage3")
SCHEDULING_KEYS = ("needs", "if")
VERDICT_GREEN = "green"
PLACEHOLDER = b"@@VERSION@@"
IGNORED = object()
MAX_MEMBER_BYTES = 512 * 1024 * 1024

_VERSION_LINE = "Version: {v}"
_RELEASE_LINE = "Current release: v{v}."
_RELEASE_LINE_CODE = "Current release: `v{v}`."
_DUNDER_LINE = '__version__ = "{v}"'
_PYPROJECT_LINE = 'version = "{v}"'

WHEEL_RULES: dict[str, Any] = {
    "benchbox/__init__.py": (_DUNDER_LINE,),
    "benchbox/utils/VERSION_MANAGEMENT.md": (_RELEASE_LINE_CODE,),
    "@dist-info/METADATA": (_VERSION_LINE, _RELEASE_LINE),
    "@dist-info/RECORD": IGNORED,
}
SDIST_RULES: dict[str, Any] = {
    "benchbox/__init__.py": (_DUNDER_LINE,),
    "benchbox/utils/VERSION_MANAGEMENT.md": (_RELEASE_LINE_CODE,),
    "PKG-INFO": (_VERSION_LINE, _RELEASE_LINE),
    "benchbox.egg-info/PKG-INFO": (_VERSION_LINE, _RELEASE_LINE),
    "pyproject.toml": (_PYPROJECT_LINE,),
    "README.md": (_RELEASE_LINE,),
    "CHANGELOG.md": IGNORED,
}


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, bool, str]] = []

    def add(self, condition: str, ok: bool, detail: str) -> bool:
        self.rows.append((condition, ok, detail))
        marker = "PASS" if ok else "FAIL"
        print(f"[reuse] {marker} {condition}: {detail}", flush=True)
        return ok

    def markdown(self, reuse: bool) -> str:
        lines = [
            "### Release UAT evidence reuse",
            "",
            f"Decision: **{'reuse the release candidate evidence' if reuse else 'run the full UAT stages'}**",
            "",
            "| Condition | Result | Detail |",
            "| --- | --- | --- |",
        ]
        for condition, ok, detail in self.rows:
            lines.append(f"| {condition} | {'pass' if ok else 'FAIL'} | {detail.replace('|', '/')} |")
        return "\n".join(lines) + "\n"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def newest_release_candidate(final_tag: str, tags: list[str]) -> str | None:
    match = FINAL_TAG.fullmatch(final_tag)
    if match is None:
        return None
    best: tuple[int, str] | None = None
    for tag in tags:
        candidate = RC_TAG.fullmatch(tag)
        if candidate is not None and candidate.group(1) == match.group(1):
            number = int(candidate.group(2))
            if best is None or number > best[0]:
                best = (number, tag)
    return best[1] if best else None


def distribution_files(directory: Path) -> tuple[Path, Path]:
    wheels = sorted(directory.glob("*.whl"))
    sdists = sorted(directory.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise ValueError(f"{directory} must hold exactly one wheel and one sdist")
    return wheels[0], sdists[0]


def distribution_digest(directory: Path) -> str:
    wheel, sdist = distribution_files(directory)
    text = "".join(f"{sha256_hex(path.read_bytes())}  {path.name}\n" for path in (wheel, sdist))
    return sha256_hex(text.encode("ascii"))


def _normalize(data: bytes, templates: tuple[str, ...], version: str) -> bytes:
    exact = {template.format(v=version).encode("utf-8") for template in templates}
    return b"\n".join(PLACEHOLDER if line in exact else line for line in data.split(b"\n"))


def _wheel_members(path: Path, version: str) -> dict[str, bytes]:
    members: dict[str, bytes] = {}
    prefix = f"benchbox-{version}.dist-info/"
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            if info.file_size > MAX_MEMBER_BYTES:
                raise ValueError(f"{path.name}: {info.filename} is too large")
            name = info.filename
            if name.startswith(prefix):
                name = "@dist-info/" + name[len(prefix) :]
            if name in members:
                raise ValueError(f"{path.name}: duplicate member {info.filename}")
            members[name] = archive.read(info)
    return members


def _sdist_members(path: Path, version: str) -> dict[str, bytes]:
    members: dict[str, bytes] = {}
    root = f"benchbox-{version}"
    with tarfile.open(path, "r:gz") as archive:
        for info in archive.getmembers():
            if info.isdir():
                continue
            if not info.isreg():
                raise ValueError(f"{path.name}: {info.name} is not a regular file")
            if info.size > MAX_MEMBER_BYTES:
                raise ValueError(f"{path.name}: {info.name} is too large")
            head, _, tail = info.name.partition("/")
            if head != root or not tail:
                raise ValueError(f"{path.name}: {info.name} is outside {root}/")
            if tail in members:
                raise ValueError(f"{path.name}: duplicate member {info.name}")
            stream = archive.extractfile(info)
            assert stream is not None
            members[tail] = stream.read()
    return members


def _compare_members(
    label: str,
    rc_members: dict[str, bytes],
    final_members: dict[str, bytes],
    rc_version: str,
    final_version: str,
    rules: dict[str, Any],
) -> list[str]:
    differences: list[str] = []
    for name in sorted(set(rc_members) - set(final_members)):
        differences.append(f"{label}: {name} exists only in the release candidate")
    for name in sorted(set(final_members) - set(rc_members)):
        differences.append(f"{label}: {name} exists only in the final distribution")
    for name in sorted(set(rc_members) & set(final_members)):
        rule = rules.get(name)
        if rule is IGNORED:
            continue
        rc_data, final_data = rc_members[name], final_members[name]
        if rule:
            rc_data = _normalize(rc_data, rule, rc_version)
            final_data = _normalize(final_data, rule, final_version)
        if rc_data != final_data:
            differences.append(f"{label}: {name} differs beyond the version references")
    return differences


def dist_differences(rc_dir: Path, final_dir: Path, rc_version: str, final_version: str) -> list[str]:
    rc_wheel, rc_sdist = distribution_files(rc_dir)
    final_wheel, final_sdist = distribution_files(final_dir)
    differences: list[str] = []
    if rc_wheel.name.replace(rc_version, "@") != final_wheel.name.replace(final_version, "@"):
        differences.append(f"wheel file names differ: {rc_wheel.name} vs {final_wheel.name}")
    if rc_sdist.name.replace(rc_version, "@") != final_sdist.name.replace(final_version, "@"):
        differences.append(f"sdist file names differ: {rc_sdist.name} vs {final_sdist.name}")
    differences.extend(
        _compare_members(
            "wheel",
            _wheel_members(rc_wheel, rc_version),
            _wheel_members(final_wheel, final_version),
            rc_version,
            final_version,
            WHEEL_RULES,
        )
    )
    differences.extend(
        _compare_members(
            "sdist",
            _sdist_members(rc_sdist, rc_version),
            _sdist_members(final_sdist, final_version),
            rc_version,
            final_version,
            SDIST_RULES,
        )
    )
    return differences


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
    )


def _git_out(*args: str, cwd: Path | None = None) -> str:
    result = _git(*args, cwd=cwd)
    if result.returncode != 0:
        raise ValueError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def normalize_lock(text: str, version: str) -> str | None:
    lines = text.split("\n")
    found = 0
    in_benchbox = False
    for index, line in enumerate(lines):
        if line == "[[package]]":
            in_benchbox = False
        elif line == 'name = "benchbox"':
            in_benchbox = True
        elif in_benchbox and line.startswith("version = "):
            in_benchbox = False
            if line == f'version = "{version}"':
                lines[index] = "version = @@VERSION@@"
                found += 1
    return "\n".join(lines) if found == 1 else None


def _stage_job_definitions(text: str) -> dict[str, Any]:
    import yaml

    workflow = yaml.safe_load(text)
    jobs = workflow["jobs"]
    definitions: dict[str, Any] = {"env": workflow.get("env")}
    for name in STAGE_JOBS:
        job = jobs.get(name)
        definitions[name] = None if job is None else {k: v for k, v in job.items() if k not in SCHEDULING_KEYS}
    return definitions


def workflow_differences(rc_sha: str, final_sha: str, repo: Path) -> list[str]:
    rc = _stage_job_definitions(_git_out("show", f"{rc_sha}:{WORKFLOW_PATH}", cwd=repo))
    final = _stage_job_definitions(_git_out("show", f"{final_sha}:{WORKFLOW_PATH}", cwd=repo))
    differences: list[str] = []
    for name in STAGE_JOBS:
        if rc[name] is None or final[name] is None:
            differences.append(f"{WORKFLOW_PATH}: {name} is missing")
        elif rc[name] != final[name]:
            differences.append(f"{WORKFLOW_PATH}: {name} differs beyond needs and if")
    if rc["env"] != final["env"]:
        differences.append(f"{WORKFLOW_PATH}: workflow-level env differs")
    return differences


def tree_differences(rc_sha: str, final_sha: str, rc_version: str, final_version: str, repo: Path) -> list[str]:
    differences: list[str] = []
    diff = _git("diff", "--quiet", f"{rc_sha}..{final_sha}", "--", *GUARDED_TREES, cwd=repo)
    if diff.returncode == 1:
        names = _git_out("diff", "--name-only", f"{rc_sha}..{final_sha}", "--", *GUARDED_TREES, cwd=repo).split()
        differences.append(f"guarded trees changed since the release candidate: {', '.join(names[:10])}")
    elif diff.returncode != 0:
        raise ValueError(f"git diff failed: {diff.stderr.strip()}")
    differences.extend(workflow_differences(rc_sha, final_sha, repo))
    rc_lock = normalize_lock(_git_out("show", f"{rc_sha}:uv.lock", cwd=repo), rc_version)
    final_lock = normalize_lock(_git_out("show", f"{final_sha}:uv.lock", cwd=repo), final_version)
    if rc_lock is None or final_lock is None:
        differences.append("uv.lock has no single benchbox package version line matching its release")
    elif rc_lock != final_lock:
        differences.append("uv.lock differs beyond the benchbox package version line")
    return differences


def _gh_bytes(*args: str) -> bytes:
    result = subprocess.run(["gh", *args], capture_output=True, check=False)
    if result.returncode != 0:
        raise ValueError(f"gh {' '.join(args)} failed: {result.stderr.decode('utf-8', 'replace').strip()}")
    return result.stdout


def _gh_json(*args: str) -> Any:
    return json.loads(_gh_bytes("api", *args))


def _artifact_listing(repo: str, run_id: int) -> list[dict[str, Any]]:
    payload = _gh_json(f"repos/{repo}/actions/runs/{run_id}/artifacts?per_page=100")
    return list(payload.get("artifacts", []))


def _download_artifact(repo: str, artifact_id: int) -> dict[str, bytes]:
    blob = _gh_bytes("api", f"repos/{repo}/actions/artifacts/{artifact_id}/zip")
    files: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        for info in archive.infolist():
            if info.is_dir() or "/" in info.filename or "\\" in info.filename or info.file_size > MAX_MEMBER_BYTES:
                continue
            files[info.filename] = archive.read(info)
    return files


def find_release_run(repo: str, rc_tag: str, rc_sha: str, report: Report) -> int | None:
    payload = _gh_json(
        f"repos/{repo}/actions/workflows/{WORKFLOW_FILE}/runs?event=push&head_branch={rc_tag}&per_page=100"
    )
    runs = [
        run
        for run in payload.get("workflow_runs", [])
        if run.get("head_sha") == rc_sha and run.get("event") == "push" and run.get("head_branch") == rc_tag
    ]
    if not runs:
        report.add("(a) release candidate run", False, f"no {WORKFLOW_FILE} push run for {rc_tag} at {rc_sha[:10]}")
        return None
    newest = max(runs, key=lambda run: (int(run["run_number"]), int(run.get("run_attempt", 1))))
    run_id = int(newest["id"])
    if newest.get("status") != "completed":
        report.add("(a) release candidate run", False, f"run {run_id} is {newest.get('status')}, not completed")
        return None
    jobs = _gh_json(f"repos/{repo}/actions/runs/{run_id}/jobs?filter=latest&per_page=100").get("jobs", [])
    gate = [job for job in jobs if job.get("name") == GATE_JOB_NAME]
    if len(gate) != 1 or gate[0].get("conclusion") != "success":
        conclusion = gate[0].get("conclusion") if len(gate) == 1 else f"{len(gate)} matching jobs"
        report.add("(a) release candidate run", False, f"run {run_id} release-uat job: {conclusion}")
        return None
    report.add("(a) release candidate run", True, f"run {run_id} release-uat concluded success")
    return run_id


def acquire_rc_evidence_and_dist(
    repo: str, rc_tag: str, rc_sha: str, run_id: int, destination: Path, report: Report
) -> str | None:
    by_name = {a["name"]: a for a in _artifact_listing(repo, run_id) if not a.get("expired")}
    for number in (1, 2, 3):
        prefix = f"release-uat-{rc_tag}-stage{number}-"
        if not any(name.startswith(prefix) for name in by_name):
            report.add("(a) release candidate shard evidence", False, f"no unexpired stage {number} shard artifacts")
            return None
    gate_name = f"release-uat-{rc_tag}"
    if gate_name not in by_name:
        report.add("(a) release candidate gate evidence", False, f"artifact {gate_name} is missing or expired")
        return None
    files = _download_artifact(repo, int(by_name[gate_name]["id"]))
    if "dist-digest.txt" not in files or "uat-gate-summary.json" not in files:
        report.add("(a) release candidate gate evidence", False, "dist-digest.txt or uat-gate-summary.json missing")
        return None
    rc_digest = sha256_hex(files["dist-digest.txt"])
    problems = gate_evidence_problems(json.loads(files["uat-gate-summary.json"]), rc_sha, rc_digest)
    if problems:
        report.add("(a) release candidate gate evidence", False, "; ".join(problems))
        return None
    report.add(
        "(a) release candidate gate evidence",
        True,
        f"verdict green, source {rc_sha[:10]}, digest {rc_digest[:16]}",
    )
    rc_dist = destination / "rc-dist"
    source = _fetch_rc_dist(repo, rc_tag, rc_sha, run_id, by_name, rc_digest, rc_dist)
    if source is None:
        report.add(
            "(c) release candidate distribution", False, "unavailable or expired; digest-matching dist not found"
        )
        return None
    report.add("(c) release candidate distribution", True, f"obtained from {source}, digest matches the admitted one")
    return rc_digest


def gate_evidence_problems(summary: dict[str, Any], rc_sha: str, rc_digest: str) -> list[str]:
    problems: list[str] = []
    if summary.get("verdict") != VERDICT_GREEN:
        problems.append(f"verdict is {summary.get('verdict')!r}")
    if summary.get("source_commit_sha") != rc_sha:
        problems.append(f"source commit {summary.get('source_commit_sha')!r} is not {rc_sha}")
    if summary.get("distribution_digest") != rc_digest:
        problems.append(f"distribution digest {summary.get('distribution_digest')!r} is not {rc_digest}")
    return problems


def _stage_candidate(files: dict[str, bytes], target: Path) -> bool:
    wheels = [n for n in files if n.endswith(".whl")]
    sdists = [n for n in files if n.endswith(".tar.gz")]
    if len(wheels) != 1 or len(sdists) != 1:
        return False
    target.mkdir(parents=True)
    for name in (wheels[0], sdists[0]):
        (target / name).write_bytes(files[name])
    return True


def _fetch_rc_dist(
    repo: str,
    rc_tag: str,
    rc_sha: str,
    run_id: int,
    by_name: dict[str, dict[str, Any]],
    rc_digest: str,
    target: Path,
) -> str | None:
    candidates: list[tuple[str, int]] = []
    admitted = by_name.get(f"admitted-{rc_tag}")
    if admitted is not None:
        candidates.append((f"admitted-{rc_tag} of run {run_id}", int(admitted["id"])))
    for attempt in range(1, 10):
        name = f"dist-{rc_sha}-attempt-{attempt}"
        listing = _gh_json(f"repos/{repo}/actions/artifacts?name={name}&per_page=10").get("artifacts", [])
        candidates.extend((name, int(a["id"])) for a in listing if not a.get("expired"))
    for label, artifact_id in candidates:
        try:
            files = _download_artifact(repo, artifact_id)
        except (ValueError, zipfile.BadZipFile):
            continue
        scratch = target.with_name(f"{target.name}.candidate")
        if scratch.exists():
            for child in scratch.iterdir():
                child.unlink()
            scratch.rmdir()
        if not _stage_candidate(files, scratch):
            continue
        if distribution_digest(scratch) == rc_digest:
            scratch.rename(target)
            return label
    return None


def decide(args: argparse.Namespace, report: Report, outputs: dict[str, str]) -> bool:
    repo = Path(args.repo_dir)
    match = FINAL_TAG.fullmatch(args.tag)
    if match is None:
        return report.add("final tag", False, f"{args.tag} is not a vX.Y.Z tag")
    final_version = match.group(1)
    tags = _git_out("tag", "--list", f"v{final_version}rc*", cwd=repo).split()
    rc_tag = newest_release_candidate(args.tag, tags)
    if rc_tag is None:
        return report.add("release candidate", False, f"no v{final_version}rcN tag exists")
    rc_version = rc_tag[1:]
    if _git_out("cat-file", "-t", rc_tag, cwd=repo).strip() != "tag":
        return report.add("release candidate", False, f"{rc_tag} is not an annotated tag")
    rc_sha = _git_out("rev-parse", f"{rc_tag}^{{commit}}", cwd=repo).strip()
    report.add("release candidate", True, f"newest candidate {rc_tag} at {rc_sha[:10]}")
    outputs.update(rc_tag=rc_tag, rc_sha=rc_sha)

    run_id = find_release_run(args.github_repository, rc_tag, rc_sha, report)
    if run_id is None:
        return False
    outputs["rc_run_id"] = str(run_id)

    ancestor = _git("merge-base", "--is-ancestor", rc_sha, args.head_sha, cwd=repo)
    if ancestor.returncode not in (0, 1):
        raise ValueError(f"git merge-base failed: {ancestor.stderr.strip()}")
    if not report.add("(b) ancestry", ancestor.returncode == 0, f"{rc_tag} commit ancestor of {args.tag} commit"):
        return False

    drift = tree_differences(rc_sha, args.head_sha, rc_version, final_version, repo)
    if not report.add(
        "(d) UAT inputs", not drift, "; ".join(drift) or "tests/uat, docker, env action, uv.lock unchanged"
    ):
        return False

    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    rc_digest = acquire_rc_evidence_and_dist(args.github_repository, rc_tag, rc_sha, run_id, work, report)
    if rc_digest is None:
        return False
    outputs["rc_dist_digest"] = rc_digest

    differences = dist_differences(work / "rc-dist", Path(args.final_dist), rc_version, final_version)
    shown = "; ".join(differences[:10]) + (f"; and {len(differences) - 10} more" if len(differences) > 10 else "")
    return report.add(
        "(c) distribution equivalence", not differences, shown or "wheel and sdist match member by member"
    )


def _write_outputs(path: str | None, outputs: dict[str, str]) -> None:
    if not path:
        return
    with open(path, "a", encoding="utf-8") as stream:
        for key, value in outputs.items():
            stream.write(f"{key}={value}\n")


def _handle_decide(args: argparse.Namespace) -> int:
    report = Report()
    outputs: dict[str, str] = {}
    try:
        reuse = decide(args, report, outputs)
    except Exception as exc:
        traceback.print_exc()
        report.add("internal", False, f"{type(exc).__name__}: {exc}")
        reuse = False
    if not reuse:
        outputs = {}
    outputs["reuse"] = "true" if reuse else "false"
    _write_outputs(args.github_output, outputs)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as stream:
            stream.write(report.markdown(reuse))
    return 0


def _handle_equivalent(args: argparse.Namespace) -> int:
    differences = dist_differences(Path(args.rc_dist), Path(args.final_dist), args.rc_version, args.final_version)
    for difference in differences:
        print(f"[reuse] DIFFERENT {difference}", file=sys.stderr)
    return 1 if differences else 0


def _handle_verify_gate(args: argparse.Namespace) -> int:
    summary = json.loads(Path(args.summary).read_text(encoding="utf-8"))
    problems = gate_evidence_problems(summary, args.rc_sha, args.rc_digest)
    for problem in problems:
        print(f"[reuse] gate evidence: {problem}", file=sys.stderr)
    if problems:
        return 1
    receipt = {
        "reused_from_tag": args.rc_tag,
        "reused_from_run_id": args.rc_run_id,
        "source_commit_sha": args.rc_sha,
        "distribution_digest": args.rc_digest,
    }
    Path(args.receipt).write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return 0


def _handle_digest(args: argparse.Namespace) -> int:
    print(distribution_digest(Path(args.dist)))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Decide and verify reuse of release candidate UAT evidence.")
    sub = parser.add_subparsers(dest="command", required=True)

    decide_parser = sub.add_parser("decide")
    decide_parser.add_argument("--tag", required=True)
    decide_parser.add_argument("--head-sha", required=True)
    decide_parser.add_argument("--github-repository", required=True)
    decide_parser.add_argument("--final-dist", required=True)
    decide_parser.add_argument("--work", required=True)
    decide_parser.add_argument("--repo-dir", default=".")
    decide_parser.add_argument("--github-output")
    decide_parser.set_defaults(handler=_handle_decide)

    equivalent = sub.add_parser("equivalent")
    equivalent.add_argument("--rc-dist", required=True)
    equivalent.add_argument("--final-dist", required=True)
    equivalent.add_argument("--rc-version", required=True)
    equivalent.add_argument("--final-version", required=True)
    equivalent.set_defaults(handler=_handle_equivalent)

    verify = sub.add_parser("verify-gate")
    verify.add_argument("--summary", required=True)
    verify.add_argument("--rc-tag", required=True)
    verify.add_argument("--rc-run-id", required=True)
    verify.add_argument("--rc-sha", required=True)
    verify.add_argument("--rc-digest", required=True)
    verify.add_argument("--receipt", required=True)
    verify.set_defaults(handler=_handle_verify_gate)

    digest = sub.add_parser("digest")
    digest.add_argument("--dist", required=True)
    digest.set_defaults(handler=_handle_digest)

    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
