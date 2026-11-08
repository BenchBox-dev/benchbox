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
from typing import Any, NamedTuple

RELEASE_TAG = re.compile(r"v(\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?)")
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
TRUNK_WORKFLOW_FILE = "trunk-uat.yml"
TRUNK_WORKFLOW_PATH = ".github/workflows/trunk-uat.yml"
TRUNK_GUARDED_TREES = (*GUARDED_TREES, TRUNK_WORKFLOW_PATH)
TRUNK_BRANCH = "develop"
TRUNK_REF = f"refs/remotes/origin/{TRUNK_BRANCH}"
TRUNK_EVENTS = ("schedule", "workflow_dispatch")
TRUNK_RUN_LIMIT = 15
TRUNK_RUN_PAGE = 50
TRUNK_GATE_ARTIFACT = re.compile(r"trunk-uat-gate-([0-9a-f]{40})")
TRUNK_EXECUTED_ARTIFACT = re.compile(r"(?:release-uat-trunk|trunk-uat-gate|trunk-uat-record)-([0-9a-f]{40})(?:-|$)")
TRUNK_STAGE_JOB = re.compile(r"UAT stage ([123]) .+")
TRUNK_GATE_JOB = "trunk UAT gate"
TRUNK_RECORD_JOB = "trunk UAT record"
STAGE_JOBS = ("uat-stage1", "uat-stage2", "uat-stage3")
SCHEDULING_KEYS = ("needs", "if")
VERDICT_GREEN = "green"
RETRY_PASSED = "retry_passed"
RETRYABLE_FIRST_ATTEMPT_STATUSES = ("failed", "timed-out")
PLACEHOLDER = b"@@VERSION@@"
IGNORED = object()
MAX_MEMBER_BYTES = 512 * 1024 * 1024

KIND_TRUNK = "trunk"
KIND_RELEASE_CANDIDATE = "release-candidate"

_VERSION_LINE = "Version: {v}"
_RELEASE_LINE = "Current release: v{v}."
_RELEASE_LINE_CODE = "Current release: `v{v}`."
_DUNDER_LINE = '__version__ = "{v}"'
_PYPROJECT_LINE = 'version = "{v}"'
_WHEEL_NAME = re.compile(r"benchbox-([^-]+)-[^/\\]+\.whl")
_SDIST_NAME = re.compile(r"benchbox-(.+)\.tar\.gz")

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
            f"Decision: **{'reuse earlier UAT evidence' if reuse else 'run the full UAT stages'}**",
            "",
            "| Condition | Result | Detail |",
            "| --- | --- | --- |",
        ]
        for condition, ok, detail in self.rows:
            lines.append(f"| {condition} | {'pass' if ok else 'FAIL'} | {detail.replace('|', '/')} |")
        return "\n".join(lines) + "\n"


class Context(NamedTuple):
    repo_dir: Path
    repository: str
    head_sha: str
    final_version: str
    final_dist: Path
    work: Path


class Source(NamedTuple):
    kind: str
    tag: str
    sha: str
    run_id: int
    digest: str


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


def distribution_version(directory: Path) -> str:
    wheel, sdist = distribution_files(directory)
    wheel_match = _WHEEL_NAME.fullmatch(wheel.name)
    sdist_match = _SDIST_NAME.fullmatch(sdist.name)
    if wheel_match is None or sdist_match is None:
        raise ValueError(f"{wheel.name} and {sdist.name} are not benchbox distribution file names")
    if wheel_match.group(1) != sdist_match.group(1):
        raise ValueError(f"{wheel.name} and {sdist.name} carry different versions")
    return wheel_match.group(1)


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
    candidate_members: dict[str, bytes],
    final_members: dict[str, bytes],
    candidate_version: str,
    final_version: str,
    rules: dict[str, Any],
) -> list[str]:
    differences: list[str] = []
    for name in sorted(set(candidate_members) - set(final_members)):
        differences.append(f"{label}: {name} exists only in the candidate distribution")
    for name in sorted(set(final_members) - set(candidate_members)):
        differences.append(f"{label}: {name} exists only in the final distribution")
    for name in sorted(set(candidate_members) & set(final_members)):
        rule = rules.get(name)
        if rule is IGNORED:
            continue
        candidate_data, final_data = candidate_members[name], final_members[name]
        if rule:
            candidate_data = _normalize(candidate_data, rule, candidate_version)
            final_data = _normalize(final_data, rule, final_version)
        if candidate_data != final_data:
            differences.append(f"{label}: {name} differs beyond the version references")
    return differences


def dist_differences(candidate_dir: Path, final_dir: Path, candidate_version: str, final_version: str) -> list[str]:
    candidate_wheel, candidate_sdist = distribution_files(candidate_dir)
    final_wheel, final_sdist = distribution_files(final_dir)
    differences: list[str] = []
    if candidate_wheel.name.replace(candidate_version, "@") != final_wheel.name.replace(final_version, "@"):
        differences.append(f"wheel file names differ: {candidate_wheel.name} vs {final_wheel.name}")
    if candidate_sdist.name.replace(candidate_version, "@") != final_sdist.name.replace(final_version, "@"):
        differences.append(f"sdist file names differ: {candidate_sdist.name} vs {final_sdist.name}")
    differences.extend(
        _compare_members(
            "wheel",
            _wheel_members(candidate_wheel, candidate_version),
            _wheel_members(final_wheel, final_version),
            candidate_version,
            final_version,
            WHEEL_RULES,
        )
    )
    differences.extend(
        _compare_members(
            "sdist",
            _sdist_members(candidate_sdist, candidate_version),
            _sdist_members(final_sdist, final_version),
            candidate_version,
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


def _is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    result = _git("merge-base", "--is-ancestor", ancestor, descendant, cwd=repo)
    if result.returncode not in (0, 1):
        raise ValueError(f"git merge-base failed: {result.stderr.strip()}")
    return result.returncode == 0


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


def workflow_differences(base_sha: str, final_sha: str, repo: Path) -> list[str]:
    base = _stage_job_definitions(_git_out("show", f"{base_sha}:{WORKFLOW_PATH}", cwd=repo))
    final = _stage_job_definitions(_git_out("show", f"{final_sha}:{WORKFLOW_PATH}", cwd=repo))
    differences: list[str] = []
    for name in STAGE_JOBS:
        if base[name] is None or final[name] is None:
            differences.append(f"{WORKFLOW_PATH}: {name} is missing")
        elif base[name] != final[name]:
            differences.append(f"{WORKFLOW_PATH}: {name} differs beyond needs and if")
    if base["env"] != final["env"]:
        differences.append(f"{WORKFLOW_PATH}: workflow-level env differs")
    return differences


def guarded_differences(base_sha: str, final_sha: str, repo: Path, trees: tuple[str, ...] = GUARDED_TREES) -> list[str]:
    differences: list[str] = []
    diff = _git("diff", "--quiet", f"{base_sha}..{final_sha}", "--", *trees, cwd=repo)
    if diff.returncode == 1:
        names = _git_out("diff", "--name-only", f"{base_sha}..{final_sha}", "--", *trees, cwd=repo).split()
        differences.append(f"guarded trees changed since the evidence commit: {', '.join(names[:10])}")
    elif diff.returncode != 0:
        raise ValueError(f"git diff failed: {diff.stderr.strip()}")
    differences.extend(workflow_differences(base_sha, final_sha, repo))
    return differences


def lock_differences(base_sha: str, final_sha: str, base_version: str, final_version: str, repo: Path) -> list[str]:
    base_lock = normalize_lock(_git_out("show", f"{base_sha}:uv.lock", cwd=repo), base_version)
    final_lock = normalize_lock(_git_out("show", f"{final_sha}:uv.lock", cwd=repo), final_version)
    if base_lock is None or final_lock is None:
        return ["uv.lock has no single benchbox package version line matching its release"]
    if base_lock != final_lock:
        return ["uv.lock differs beyond the benchbox package version line"]
    return []


def tree_differences(
    base_sha: str,
    final_sha: str,
    base_version: str,
    final_version: str,
    repo: Path,
    trees: tuple[str, ...] = GUARDED_TREES,
) -> list[str]:
    return [
        *guarded_differences(base_sha, final_sha, repo, trees),
        *lock_differences(base_sha, final_sha, base_version, final_version, repo),
    ]


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


def gate_evidence_problems(summary: dict[str, Any], source_sha: str, source_digest: str) -> list[str]:
    problems: list[str] = []
    if summary.get("verdict") != VERDICT_GREEN:
        problems.append(f"verdict is {summary.get('verdict')!r}")
    if summary.get("source_commit_sha") != source_sha:
        problems.append(f"source commit {summary.get('source_commit_sha')!r} is not {source_sha}")
    if summary.get("distribution_digest") != source_digest:
        problems.append(f"distribution digest {summary.get('distribution_digest')!r} is not {source_digest}")
    return problems


def _retry_row_problem(row: Any) -> str | None:
    if not isinstance(row, dict):
        return "row is not an object"
    outcome = row.get("retry_outcome")
    attempt = row.get("attempt") or 1
    if outcome is None:
        return None if attempt == 1 else f"attempt {attempt} has no recorded retry outcome"
    if outcome != RETRY_PASSED:
        return f"retry outcome is {outcome!r}, not {RETRY_PASSED!r}"
    problems: list[str] = []
    if attempt != 2:
        problems.append(f"attempt is {attempt}, not 2")
    if row.get("status") != "passed":
        problems.append(f"attempt 2 finished as {row.get('status')!r}")
    if not row.get("retry_signature"):
        problems.append("no infrastructure failure signature")
    if row.get("first_attempt_status") not in RETRYABLE_FIRST_ATTEMPT_STATUSES:
        problems.append(f"attempt 1 finished as {row.get('first_attempt_status')!r}")
    return "; ".join(problems) or None


def retry_problems(evidence: Path, tag: str) -> list[str]:
    cell_files = sorted(evidence.glob(f"release-uat-{tag}-stage*-*/run/cells.jsonl"))
    if not cell_files:
        return [f"no cells.jsonl under {evidence} for {tag}"]
    problems: list[str] = []
    for path in cell_files:
        shard = path.parent.parent.name
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError as exc:
                problems.append(f"{shard} line {number}: unreadable cell row: {exc}")
                continue
            problem = _retry_row_problem(row)
            if problem is not None:
                cell = (
                    f"{row.get('platform')}/{row.get('benchmark')}/{row.get('scale')}" if isinstance(row, dict) else "?"
                )
                problems.append(f"{shard} line {number} ({cell}): {problem}")
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


def _fetch_candidate_dist(
    repo: str, sha: str, admitted: list[tuple[str, int]], digest: str, target: Path
) -> str | None:
    candidates = list(admitted)
    for attempt in range(1, 10):
        name = f"dist-{sha}-attempt-{attempt}"
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
        if distribution_digest(scratch) == digest:
            scratch.rename(target)
            return label
    return None


def _acquire_evidence(
    ctx: Context,
    report: Report,
    label: str,
    tag: str,
    sha: str,
    run_id: int,
    artifacts: list[dict[str, Any]],
    gate_name: str,
    admitted_names: list[str],
) -> tuple[str, Path] | None:
    live = {a["name"]: a for a in artifacts if not a.get("expired")}
    expired = sorted(a["name"] for a in artifacts if a.get("expired"))
    for number in (1, 2, 3):
        prefix = f"release-uat-{tag}-stage{number}-"
        if not any(name.startswith(prefix) for name in live):
            lapsed = [name for name in expired if name.startswith(prefix)]
            detail = f"expired artifact: {lapsed[0]}" if lapsed else f"no stage {number} shard artifacts"
            report.add(f"{label}: shard evidence", False, detail)
            return None
    if gate_name not in live:
        detail = f"expired artifact: {gate_name}" if gate_name in expired else f"artifact {gate_name} is missing"
        report.add(f"{label}: gate evidence", False, detail)
        return None
    files = _download_artifact(ctx.repository, int(live[gate_name]["id"]))
    if "dist-digest.txt" not in files or "uat-gate-summary.json" not in files:
        report.add(f"{label}: gate evidence", False, "dist-digest.txt or uat-gate-summary.json missing")
        return None
    digest = sha256_hex(files["dist-digest.txt"])
    problems = gate_evidence_problems(json.loads(files["uat-gate-summary.json"]), sha, digest)
    if problems:
        report.add(f"{label}: gate evidence", False, "; ".join(problems))
        return None
    report.add(f"{label}: gate evidence", True, f"verdict green, source {sha[:10]}, digest {digest[:16]}")
    admitted = [(f"{name} of run {run_id}", int(live[name]["id"])) for name in admitted_names if name in live]
    target = ctx.work / f"{label.replace(' ', '-')}-dist"
    source = _fetch_candidate_dist(ctx.repository, sha, admitted, digest, target)
    if source is None:
        lapsed = [name for name in expired if name in admitted_names]
        detail = "unavailable or expired; digest-matching dist not found"
        if lapsed:
            detail += f" (expired artifact: {lapsed[0]})"
        report.add(f"{label}: distribution", False, detail)
        return None
    report.add(f"{label}: distribution", True, f"obtained from {source}, digest matches the gate evidence")
    return digest, target


def _verify_source(
    ctx: Context,
    report: Report,
    *,
    kind: str,
    label: str,
    tag: str,
    sha: str,
    run_id: int,
    artifacts: list[dict[str, Any]],
    gate_name: str,
    admitted_names: list[str],
    trees: tuple[str, ...],
) -> Source | None:
    ancestor = _is_ancestor(ctx.repo_dir, sha, ctx.head_sha)
    relation = "is an ancestor of" if ancestor else "is not an ancestor of"
    if not report.add(
        f"{label}: ancestry", ancestor, f"evidence commit {sha[:10]} {relation} the tagged commit {ctx.head_sha[:10]}"
    ):
        return None
    drift = guarded_differences(sha, ctx.head_sha, ctx.repo_dir, trees)
    if not report.add(
        f"{label}: UAT inputs", not drift, "; ".join(drift) or "tests/uat, docker, env action, workflows unchanged"
    ):
        return None
    acquired = _acquire_evidence(ctx, report, label, tag, sha, run_id, artifacts, gate_name, admitted_names)
    if acquired is None:
        return None
    digest, dist_dir = acquired
    candidate_version = distribution_version(dist_dir)
    lock = lock_differences(sha, ctx.head_sha, candidate_version, ctx.final_version, ctx.repo_dir)
    if not report.add(f"{label}: uv.lock", not lock, "; ".join(lock) or "differs only in the benchbox version line"):
        return None
    differences = dist_differences(dist_dir, ctx.final_dist, candidate_version, ctx.final_version)
    shown = "; ".join(differences[:10]) + (f"; and {len(differences) - 10} more" if len(differences) > 10 else "")
    if not report.add(
        f"{label}: distribution equivalence", not differences, shown or "wheel and sdist match member by member"
    ):
        return None
    return Source(kind=kind, tag=tag, sha=sha, run_id=run_id, digest=digest)


def _trunk_run_problem(run: dict[str, Any]) -> str | None:
    if run.get("event") not in TRUNK_EVENTS:
        return f"event {run.get('event')!r} is not schedule or workflow_dispatch"
    if run.get("head_branch") != TRUNK_BRANCH:
        return f"ran on {run.get('head_branch')!r}, not {TRUNK_BRANCH}"
    return None


def _trunk_attempt_problem(run: dict[str, Any], jobs: list[dict[str, Any]]) -> str | None:
    attempt = int(run.get("run_attempt") or 1)
    stages: set[str] = set()
    seen: set[str] = set()
    for job in jobs:
        name = str(job.get("name"))
        stage = TRUNK_STAGE_JOB.fullmatch(name)
        if stage is None and name not in (TRUNK_GATE_JOB, TRUNK_RECORD_JOB):
            continue
        if job.get("conclusion") != "success":
            return f"job {name!r} concluded {job.get('conclusion')!r} in the latest attempt, not 'success'"
        job_attempt = int(job.get("run_attempt") or attempt)
        if job_attempt != attempt:
            return f"job {name!r} ran in attempt {job_attempt}, not the run's latest attempt {attempt}"
        if stage is not None:
            stages.add(stage.group(1))
        else:
            seen.add(name)
    for name in (TRUNK_GATE_JOB, TRUNK_RECORD_JOB):
        if name not in seen:
            return f"no {name!r} job in the latest attempt"
    missing = sorted({"1", "2", "3"} - stages)
    if missing:
        return f"no UAT stage {', '.join(missing)} shard job in the latest attempt"
    return None


def _executed_sources(artifacts: list[dict[str, Any]]) -> list[str]:
    found: list[str] = []
    for artifact in artifacts:
        match = TRUNK_EXECUTED_ARTIFACT.match(str(artifact.get("name")))
        if match is not None and match.group(1) not in found:
            found.append(match.group(1))
    return found


class TrunkOutcome(NamedTuple):
    source: Source | None
    decisive: bool
    red: bool


def _evaluate_trunk_run(ctx: Context, run: dict[str, Any], report: Report) -> TrunkOutcome:
    run_id = int(run["id"])
    label = f"trunk run {run_id}"
    problem = _trunk_run_problem(run)
    if problem is not None:
        report.add(f"{label}: run", False, problem)
        return TrunkOutcome(None, False, False)
    artifacts = _artifact_listing(ctx.repository, run_id)
    executed = _executed_sources(artifacts)
    decisive = any(_is_ancestor(ctx.repo_dir, sha, ctx.head_sha) for sha in executed)
    if run.get("conclusion") != "success":
        report.add(f"{label}: run", False, f"concluded {run.get('conclusion')!r}, not 'success'")
        return TrunkOutcome(None, decisive, decisive)
    if decisive and len(executed) > 1:
        report.add(
            f"{label}: gate evidence",
            False,
            f"the run holds evidence for {len(executed)} source commits, one of them an ancestor of the tagged commit",
        )
        return TrunkOutcome(None, True, True)
    gates = [a["name"] for a in artifacts if TRUNK_GATE_ARTIFACT.fullmatch(str(a.get("name")))]
    if len(gates) != 1:
        detail = (
            "no trunk UAT gate artifact; the run skipped, was deferred or kept no evidence"
            if not gates
            else f"{len(gates)} trunk UAT gate artifacts"
        )
        report.add(f"{label}: gate evidence", False, detail)
        return TrunkOutcome(None, decisive, False)
    sha = gates[0].removeprefix("trunk-uat-gate-")
    jobs = _gh_json(f"repos/{ctx.repository}/actions/runs/{run_id}/jobs?filter=latest&per_page=100").get("jobs", [])
    attempt_problem = _trunk_attempt_problem(run, jobs)
    if attempt_problem is not None:
        report.add(f"{label}: attempt", False, attempt_problem)
        return TrunkOutcome(None, decisive, True)
    report.add(f"{label}: run", True, f"{run['event']} run on {TRUNK_BRANCH} concluded success, source {sha[:10]}")
    run_head = run.get("head_sha")
    if not isinstance(run_head, str) or not run_head:
        report.add(f"{label}: {TRUNK_WORKFLOW_FILE}", False, "the run records no head commit")
        return TrunkOutcome(None, decisive, False)
    workflow_diff = _git("diff", "--quiet", run_head, ctx.head_sha, "--", TRUNK_WORKFLOW_PATH, cwd=ctx.repo_dir)
    if workflow_diff.returncode not in (0, 1):
        raise ValueError(f"git diff failed: {workflow_diff.stderr.strip()}")
    if not report.add(
        f"{label}: {TRUNK_WORKFLOW_FILE}",
        workflow_diff.returncode == 0,
        f"the workflow file the run executed, at {run_head[:10]}, "
        f"{'equals' if workflow_diff.returncode == 0 else 'differs from'} the one at the tagged commit",
    ):
        return TrunkOutcome(None, decisive, False)
    source = _verify_source(
        ctx,
        report,
        kind=KIND_TRUNK,
        label=label,
        tag=f"trunk-{sha}",
        sha=sha,
        run_id=run_id,
        artifacts=artifacts,
        gate_name=gates[0],
        admitted_names=sorted(a["name"] for a in artifacts if str(a.get("name")).startswith("admitted-")),
        trees=TRUNK_GUARDED_TREES,
    )
    return TrunkOutcome(source, decisive, False)


def find_trunk_candidate(ctx: Context, report: Report) -> tuple[Source | None, bool]:
    payload = _gh_json(
        f"repos/{ctx.repository}/actions/workflows/{TRUNK_WORKFLOW_FILE}/runs?status=completed&per_page={TRUNK_RUN_PAGE}"
    )
    runs = [run for run in payload.get("workflow_runs", []) if run.get("status") == "completed"]
    runs.sort(key=lambda run: (str(run.get("created_at")), int(run["id"])), reverse=True)
    if not runs:
        report.add("trunk UAT runs", False, f"no completed {TRUNK_WORKFLOW_FILE} run found")
        return None, False
    for run in runs[:TRUNK_RUN_LIMIT]:
        try:
            outcome = _evaluate_trunk_run(ctx, run, report)
        except Exception as exc:
            report.add(f"trunk run {run.get('id')}: evaluation", False, f"{type(exc).__name__}: {exc}")
            report.add("trunk UAT runs", False, "the newest run could not be evaluated, so no older run is used")
            return None, False
        if outcome.source is not None:
            return outcome.source, False
        if outcome.decisive:
            report.add(
                "trunk UAT runs",
                False,
                f"run {run.get('id')} is the newest run that executed cells for an ancestor of the tagged commit "
                "and it does not qualify, so no older run is used",
            )
            return None, outcome.red
    report.add("trunk UAT runs", False, f"none of the newest {min(len(runs), TRUNK_RUN_LIMIT)} runs qualified")
    return None, False


def find_release_run(repo: str, rc_tag: str, rc_sha: str, report: Report) -> int | None:
    label = f"release candidate {rc_tag}"
    payload = _gh_json(
        f"repos/{repo}/actions/workflows/{WORKFLOW_FILE}/runs?event=push&head_branch={rc_tag}&per_page=100"
    )
    runs = [
        run
        for run in payload.get("workflow_runs", [])
        if run.get("head_sha") == rc_sha and run.get("event") == "push" and run.get("head_branch") == rc_tag
    ]
    if not runs:
        report.add(f"{label}: run", False, f"no {WORKFLOW_FILE} push run for {rc_tag} at {rc_sha[:10]}")
        return None
    newest = max(runs, key=lambda run: (int(run["run_number"]), int(run.get("run_attempt", 1))))
    run_id = int(newest["id"])
    if newest.get("status") != "completed":
        report.add(f"{label}: run", False, f"run {run_id} is {newest.get('status')}, not completed")
        return None
    jobs = _gh_json(f"repos/{repo}/actions/runs/{run_id}/jobs?filter=latest&per_page=100").get("jobs", [])
    gate = [job for job in jobs if job.get("name") == GATE_JOB_NAME]
    if len(gate) != 1 or gate[0].get("conclusion") != "success":
        conclusion = gate[0].get("conclusion") if len(gate) == 1 else f"{len(gate)} matching jobs"
        report.add(f"{label}: run", False, f"run {run_id} release-uat job: {conclusion}")
        return None
    report.add(f"{label}: run", True, f"run {run_id} release-uat concluded success")
    return run_id


def find_release_candidate(ctx: Context, report: Report) -> Source | None:
    tags = _git_out("tag", "--list", f"v{ctx.final_version}rc*", cwd=ctx.repo_dir).split()
    rc_tag = newest_release_candidate(f"v{ctx.final_version}", tags)
    if rc_tag is None:
        report.add("release candidate", False, f"no v{ctx.final_version}rcN tag exists")
        return None
    if _git_out("cat-file", "-t", rc_tag, cwd=ctx.repo_dir).strip() != "tag":
        report.add("release candidate", False, f"{rc_tag} is not an annotated tag")
        return None
    rc_sha = _git_out("rev-parse", f"{rc_tag}^{{commit}}", cwd=ctx.repo_dir).strip()
    report.add("release candidate", True, f"newest candidate {rc_tag} at {rc_sha[:10]}")
    run_id = find_release_run(ctx.repository, rc_tag, rc_sha, report)
    if run_id is None:
        return None
    return _verify_source(
        ctx,
        report,
        kind=KIND_RELEASE_CANDIDATE,
        label=f"release candidate {rc_tag}",
        tag=rc_tag,
        sha=rc_sha,
        run_id=run_id,
        artifacts=_artifact_listing(ctx.repository, run_id),
        gate_name=f"release-uat-{rc_tag}",
        admitted_names=[f"admitted-{rc_tag}"],
        trees=GUARDED_TREES,
    )


def _on_trunk(ctx: Context) -> tuple[bool, str]:
    if _git("rev-parse", "--verify", "--quiet", TRUNK_REF, cwd=ctx.repo_dir).returncode != 0:
        return False, f"{TRUNK_REF} is not fetched, so the tagged commit cannot be placed on {TRUNK_BRANCH}"
    on_trunk = _is_ancestor(ctx.repo_dir, ctx.head_sha, TRUNK_REF)
    detail = (
        f"tagged commit {ctx.head_sha[:10]} is on {TRUNK_BRANCH}"
        if on_trunk
        else f"tagged commit {ctx.head_sha[:10]} is not on {TRUNK_BRANCH}; trunk UAT evidence does not cover it"
    )
    return on_trunk, detail


def decide(args: argparse.Namespace, report: Report, outputs: dict[str, str]) -> bool:
    match = RELEASE_TAG.fullmatch(args.tag)
    if match is None:
        return report.add("release tag", False, f"{args.tag} is not a vX.Y.Z or vX.Y.Z(a|b|rc)N tag")
    ctx = Context(
        repo_dir=Path(args.repo_dir),
        repository=args.github_repository,
        head_sha=args.head_sha,
        final_version=match.group(1),
        final_dist=Path(args.final_dist),
        work=Path(args.work),
    )
    ctx.work.mkdir(parents=True, exist_ok=True)
    source: Source | None = None
    red_trunk_run = False
    on_trunk, detail = _on_trunk(ctx)
    if report.add("trunk line", on_trunk, detail):
        source, red_trunk_run = find_trunk_candidate(ctx, report)
    if source is None and not red_trunk_run and FINAL_TAG.fullmatch(args.tag):
        source = find_release_candidate(ctx, report)
    if source is None:
        return False
    outputs.update(
        source_kind=source.kind,
        source_tag=source.tag,
        source_sha=source.sha,
        source_run_id=str(source.run_id),
        source_dist_digest=source.digest,
    )
    origin = "trunk UAT run" if source.kind == KIND_TRUNK else f"release candidate run of {source.tag}"
    return report.add("reused evidence", True, f"reused {origin} {source.run_id} for {source.sha}")


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
    differences = dist_differences(
        Path(args.candidate_dist), Path(args.final_dist), args.candidate_version, args.final_version
    )
    for difference in differences:
        print(f"[reuse] DIFFERENT {difference}", file=sys.stderr)
    return 1 if differences else 0


def _handle_verify_gate(args: argparse.Namespace) -> int:
    summary = json.loads(Path(args.summary).read_text(encoding="utf-8"))
    problems = gate_evidence_problems(summary, args.source_sha, args.source_digest)
    problems.extend(retry_problems(Path(args.evidence), args.source_tag))
    for problem in problems:
        print(f"[reuse] gate evidence: {problem}", file=sys.stderr)
    if problems:
        return 1
    receipt = {
        "reused_from_kind": args.source_kind,
        "reused_from_tag": args.source_tag,
        "reused_from_run_id": args.source_run_id,
        "source_commit_sha": args.source_sha,
        "distribution_digest": args.source_digest,
    }
    Path(args.receipt).write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return 0


def _handle_digest(args: argparse.Namespace) -> int:
    print(distribution_digest(Path(args.dist)))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Decide and verify reuse of earlier UAT evidence for a release.")
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
    equivalent.add_argument("--candidate-dist", required=True)
    equivalent.add_argument("--final-dist", required=True)
    equivalent.add_argument("--candidate-version", required=True)
    equivalent.add_argument("--final-version", required=True)
    equivalent.set_defaults(handler=_handle_equivalent)

    verify = sub.add_parser("verify-gate")
    verify.add_argument("--summary", required=True)
    verify.add_argument("--evidence", required=True)
    verify.add_argument("--source-kind", required=True, choices=(KIND_TRUNK, KIND_RELEASE_CANDIDATE))
    verify.add_argument("--source-tag", required=True)
    verify.add_argument("--source-run-id", required=True)
    verify.add_argument("--source-sha", required=True)
    verify.add_argument("--source-digest", required=True)
    verify.add_argument("--receipt", required=True)
    verify.set_defaults(handler=_handle_verify_gate)

    digest = sub.add_parser("digest")
    digest.add_argument("--dist", required=True)
    digest.set_defaults(handler=_handle_digest)

    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
