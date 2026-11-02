from __future__ import annotations

import difflib
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import github, omp_pin
from .verdict import valid_path

if TYPE_CHECKING:
    from .classifier import ChangedFile

LOCK_PATH = ".github/theseus-lock.json"
INSTALLER_PATH = "_project/scripts/theseus_bootstrap.py"
STAGED_PROMOTION_DIR = ".oracle-promotion"
PROMOTION_PLAN_DIR = "promotion"
UPSTREAM_DIFF_NAME = "upstream.diff"
SOURCES_ENV = "ORACLE_PROMOTION_SOURCES"
BOOTSTRAP_ARTIFACT_SHA256 = "0" * 64
RECORD_VERSION = 1
MAX_TREE_FILES = 5000
MAX_TREE_BYTES = 64 << 20

_COMMIT_SHA = re.compile(r"[0-9a-f]{40}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_REPO_SLUG = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_PACKAGE_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")

LOCK_REQUIRED_KEYS = frozenset(
    {
        "schema",
        "package",
        "version",
        "repository",
        "source_commit",
        "source_sha256",
        "delta_sha256",
        "artifact",
        "dependencies",
        "workflow_sha",
    }
)
LOCK_OPTIONAL_KEYS = frozenset({"matcher", "bootstrap"})
ARTIFACT_KEYS = frozenset({"sha256", "urls"})
DEPENDENCY_KEYS = frozenset({"name", "version", "sha256", "urls"})
MATCHER_KEYS = frozenset({"path", "sha256"})
BOOTSTRAP_KEYS = frozenset(
    {
        "baseline_repo",
        "baseline_commit",
        "baseline_source_sha256",
        "path_map",
        "reviewer",
        "approved_head",
    }
)
PROMOTION_RECORD_KEYS = frozenset(
    {
        "v",
        "head_sha",
        "old_source_sha256",
        "new_source_sha256",
        "old_artifact_sha256",
        "new_artifact_sha256",
        "delta_sha256",
        "bootstrap",
    }
)

SourceLoader = Callable[[str, str], dict[str, bytes]]
AncestorCheck = Callable[[str, str], bool]


class PromotionError(ValueError):
    pass


@dataclass(frozen=True)
class DependencyLock:
    name: str
    version: str
    sha256: str
    urls: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "sha256": self.sha256,
            "urls": list(self.urls),
        }


@dataclass(frozen=True)
class MatcherLock:
    path: str
    sha256: str

    def to_json(self) -> dict[str, str]:
        return {"path": self.path, "sha256": self.sha256}


@dataclass(frozen=True)
class BootstrapRecord:
    baseline_repo: str
    baseline_commit: str
    baseline_source_sha256: str
    path_map: Mapping[str, str]
    reviewer: str
    approved_head: str

    def to_json(self) -> dict[str, Any]:
        return {
            "baseline_repo": self.baseline_repo,
            "baseline_commit": self.baseline_commit,
            "baseline_source_sha256": self.baseline_source_sha256,
            "path_map": dict(sorted(self.path_map.items())),
            "reviewer": self.reviewer,
            "approved_head": self.approved_head,
        }


@dataclass(frozen=True)
class TheseusLock:
    schema: int
    package: str
    version: str
    repository: str
    source_commit: str
    source_sha256: str
    delta_sha256: str
    artifact_sha256: str
    artifact_urls: tuple[str, ...]
    dependencies: tuple[DependencyLock, ...]
    workflow_sha: str
    matcher: MatcherLock | None = None
    bootstrap: BootstrapRecord | None = None

    def canonical(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": self.schema,
            "package": self.package,
            "version": self.version,
            "repository": self.repository,
            "source_commit": self.source_commit,
            "source_sha256": self.source_sha256,
            "delta_sha256": self.delta_sha256,
            "artifact": {"sha256": self.artifact_sha256, "urls": list(self.artifact_urls)},
            "dependencies": [dep.to_json() for dep in self.dependencies],
            "workflow_sha": self.workflow_sha,
        }
        if self.matcher is not None:
            payload["matcher"] = self.matcher.to_json()
        if self.bootstrap is not None:
            payload["bootstrap"] = self.bootstrap.to_json()
        return payload


@dataclass(frozen=True)
class PromotionRecord:
    head_sha: str
    old_source_sha256: str
    new_source_sha256: str
    old_artifact_sha256: str
    new_artifact_sha256: str
    delta_sha256: str
    bootstrap: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "v": RECORD_VERSION,
            "head_sha": self.head_sha,
            "old_source_sha256": self.old_source_sha256,
            "new_source_sha256": self.new_source_sha256,
            "old_artifact_sha256": self.old_artifact_sha256,
            "new_artifact_sha256": self.new_artifact_sha256,
            "delta_sha256": self.delta_sha256,
            "bootstrap": self.bootstrap,
        }


@dataclass(frozen=True)
class PromotionBundle:
    record: PromotionRecord
    changed_files: tuple[ChangedFile, ...]
    diff_text: str
    old_tree: Mapping[str, bytes]
    new_tree: Mapping[str, bytes]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PromotionError(message)


def _https_urls(value: Any, label: str, *, minimum: int) -> tuple[str, ...]:
    _require(isinstance(value, list) and len(value) >= minimum, f"{label} needs at least {minimum} https url(s)")
    urls: list[str] = []
    for item in value:
        _require(
            isinstance(item, str) and item.startswith("https://") and not item.isspace(), f"{label} needs https urls"
        )
        urls.append(item)
    _require(len(set(urls)) == len(urls), f"{label} urls must be distinct")
    return tuple(urls)


def _parse_dependency(data: Any) -> DependencyLock:
    _require(isinstance(data, Mapping) and set(data) == DEPENDENCY_KEYS, "dependency lock has invalid keys")
    name = data.get("name")
    version = data.get("version")
    sha256 = data.get("sha256")
    _require(isinstance(name, str) and _PACKAGE_NAME.fullmatch(name) is not None, f"invalid dependency name {name!r}")
    _require(
        isinstance(version, str) and omp_pin.VERSION_PATTERN.fullmatch(version) is not None,
        f"invalid dependency version for {name}",
    )
    _require(isinstance(sha256, str) and _SHA256.fullmatch(sha256) is not None, f"invalid sha256 for {name}")
    urls = _https_urls(data.get("urls"), f"dependency {name}", minimum=1)
    return DependencyLock(name=name, version=version, sha256=sha256, urls=urls)


def _parse_matcher(data: Any) -> MatcherLock:
    _require(isinstance(data, Mapping) and set(data) == MATCHER_KEYS, "matcher lock has invalid keys")
    path = data.get("path")
    sha256 = data.get("sha256")
    _require(isinstance(path, str) and valid_path(path), "matcher lock needs a valid repository-relative path")
    _require(isinstance(sha256, str) and _SHA256.fullmatch(sha256) is not None, "matcher lock needs a 64-hex sha256")
    return MatcherLock(path=path, sha256=sha256)


def _clean_map_path(value: Any, label: str) -> str:
    _require(isinstance(value, str), f"{label} must be a string path")
    stripped = value.strip("/")
    _require(valid_path(stripped), f"{label} must be a valid relative path")
    return stripped


def _parse_bootstrap(data: Any) -> BootstrapRecord:
    _require(isinstance(data, Mapping) and set(data) == BOOTSTRAP_KEYS, "bootstrap record has invalid keys")
    baseline_repo = data.get("baseline_repo")
    baseline_commit = data.get("baseline_commit")
    baseline_source_sha256 = data.get("baseline_source_sha256")
    raw_map = data.get("path_map")
    reviewer = data.get("reviewer")
    approved_head = data.get("approved_head")
    _require(
        isinstance(baseline_repo, str) and _REPO_SLUG.fullmatch(baseline_repo) is not None,
        "bootstrap baseline_repo must be owner/name",
    )
    _require(
        isinstance(baseline_commit, str) and _COMMIT_SHA.fullmatch(baseline_commit) is not None,
        "bootstrap baseline_commit must be 40-char lowercase hex",
    )
    _require(
        isinstance(baseline_source_sha256, str) and _SHA256.fullmatch(baseline_source_sha256) is not None,
        "bootstrap baseline_source_sha256 must be 64-char lowercase hex",
    )
    _require(isinstance(raw_map, Mapping) and bool(raw_map), "bootstrap path_map must be a non-empty mapping")
    cleaned_map: dict[str, str] = {}
    for src, dst in raw_map.items():
        cleaned_src = _clean_map_path(src, "bootstrap path_map source")
        _require(cleaned_src not in cleaned_map, f"duplicate bootstrap path_map source {cleaned_src!r}")
        cleaned_map[cleaned_src] = _clean_map_path(dst, "bootstrap path_map target")
    _require(isinstance(reviewer, str) and bool(reviewer.strip()), "bootstrap reviewer must be non-empty")
    _require(
        isinstance(approved_head, str) and _COMMIT_SHA.fullmatch(approved_head) is not None,
        "bootstrap approved_head must be 40-char lowercase hex",
    )
    return BootstrapRecord(
        baseline_repo=baseline_repo,
        baseline_commit=baseline_commit,
        baseline_source_sha256=baseline_source_sha256,
        path_map=cleaned_map,
        reviewer=reviewer.strip(),
        approved_head=approved_head,
    )


def parse_lock(text: str) -> TheseusLock:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PromotionError(f"theseus lock is not valid JSON: {exc}") from exc
    _require(isinstance(data, Mapping), "theseus lock must be a JSON object")
    keys = set(data)
    _require(
        LOCK_REQUIRED_KEYS <= keys <= LOCK_REQUIRED_KEYS | LOCK_OPTIONAL_KEYS,
        "theseus lock has missing or unexpected keys",
    )
    _require(data.get("schema") == 1 and not isinstance(data.get("schema"), bool), "theseus lock schema must be 1")
    package = data.get("package")
    version = data.get("version")
    repository = data.get("repository")
    source_commit = data.get("source_commit")
    source_sha256 = data.get("source_sha256")
    delta_sha256 = data.get("delta_sha256")
    workflow_sha = data.get("workflow_sha")
    artifact = data.get("artifact")
    dependencies_raw = data.get("dependencies")
    _require(isinstance(package, str) and _PACKAGE_NAME.fullmatch(package) is not None, "invalid theseus package name")
    _require(
        isinstance(version, str) and omp_pin.VERSION_PATTERN.fullmatch(version) is not None,
        "invalid theseus lock version",
    )
    _require(
        isinstance(repository, str) and _REPO_SLUG.fullmatch(repository) is not None,
        "invalid theseus repository slug",
    )
    _require(
        isinstance(source_commit, str) and _COMMIT_SHA.fullmatch(source_commit) is not None,
        "theseus lock source_commit must be 40-char lowercase hex",
    )
    _require(
        isinstance(source_sha256, str) and _SHA256.fullmatch(source_sha256) is not None,
        "theseus lock source_sha256 must be 64-char lowercase hex",
    )
    _require(
        isinstance(delta_sha256, str) and _SHA256.fullmatch(delta_sha256) is not None,
        "theseus lock delta_sha256 must be 64-char lowercase hex",
    )
    _require(
        isinstance(workflow_sha, str) and _COMMIT_SHA.fullmatch(workflow_sha) is not None,
        "theseus lock workflow_sha must be 40-char lowercase hex",
    )
    _require(isinstance(artifact, Mapping) and set(artifact) == ARTIFACT_KEYS, "theseus lock artifact is invalid")
    artifact_sha256 = artifact.get("sha256")
    _require(
        isinstance(artifact_sha256, str) and _SHA256.fullmatch(artifact_sha256) is not None,
        "theseus lock artifact sha256 must be 64-char lowercase hex",
    )
    artifact_urls = _https_urls(artifact.get("urls"), "theseus artifact", minimum=2)
    _require(isinstance(dependencies_raw, list), "theseus lock dependencies must be a list")
    dependencies = tuple(_parse_dependency(item) for item in dependencies_raw)
    seen_deps = [dep.name for dep in dependencies]
    _require(len(set(seen_deps)) == len(seen_deps), "theseus lock lists duplicate dependencies")
    matcher = _parse_matcher(data["matcher"]) if "matcher" in data and data["matcher"] is not None else None
    bootstrap = _parse_bootstrap(data["bootstrap"]) if "bootstrap" in data and data["bootstrap"] is not None else None
    return TheseusLock(
        schema=1,
        package=package,
        version=version,
        repository=repository,
        source_commit=source_commit,
        source_sha256=source_sha256,
        delta_sha256=delta_sha256,
        artifact_sha256=artifact_sha256,
        artifact_urls=artifact_urls,
        dependencies=dependencies,
        workflow_sha=workflow_sha,
        matcher=matcher,
        bootstrap=bootstrap,
    )


def lock_digest(lock: TheseusLock) -> str:
    encoded = json.dumps(lock.canonical(), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def valid_promotion_record(value: Any) -> bool:
    if not isinstance(value, Mapping) or set(value) != PROMOTION_RECORD_KEYS:
        return False
    return (
        value.get("v") == RECORD_VERSION
        and not isinstance(value.get("v"), bool)
        and isinstance(value.get("bootstrap"), bool)
        and isinstance(value.get("head_sha"), str)
        and _COMMIT_SHA.fullmatch(value["head_sha"]) is not None
        and all(
            isinstance(value.get(key), str) and _SHA256.fullmatch(value[key]) is not None
            for key in (
                "old_source_sha256",
                "new_source_sha256",
                "old_artifact_sha256",
                "new_artifact_sha256",
                "delta_sha256",
            )
        )
    )


def tree_sha256(files: Mapping[str, bytes]) -> str:
    digest = hashlib.sha256()
    for path in sorted(files):
        _require(valid_path(path), f"invalid source tree path {path!r}")
        raw = files[path]
        digest.update(path.encode("utf-8") + b"\0" + str(len(raw)).encode("ascii") + b"\0")
        digest.update(hashlib.sha256(raw).digest())
    return digest.hexdigest()


def apply_path_map(files: Mapping[str, bytes], path_map: Mapping[str, str]) -> dict[str, bytes]:
    mapped: dict[str, bytes] = {}
    ordered = sorted(path_map.items(), key=lambda item: -len(item[0]))
    for path, content in sorted(files.items()):
        for src, dst in ordered:
            target: str | None = None
            if path == src:
                target = dst
            elif path.startswith(src + "/"):
                target = f"{dst}/{path[len(src) + 1 :]}"
            if target is not None:
                _require(valid_path(target), f"mapped path {target!r} is invalid")
                _require(target not in mapped, f"bootstrap path_map collides at {target!r}")
                mapped[target] = content
                break
    return mapped


def compute_delta_sha256(old_tree: Mapping[str, bytes], new_tree: Mapping[str, bytes]) -> str:
    entries: list[dict[str, str | None]] = []
    for path in sorted(set(old_tree) | set(new_tree)):
        old_bytes = old_tree.get(path)
        new_bytes = new_tree.get(path)
        if old_bytes == new_bytes:
            continue
        entries.append(
            {
                "path": path,
                "old": None if old_bytes is None else hashlib.sha256(old_bytes).hexdigest(),
                "new": None if new_bytes is None else hashlib.sha256(new_bytes).hexdigest(),
            }
        )
    raw = json.dumps(entries, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _text_lines(raw: bytes | None) -> list[str] | None:
    if raw is None:
        return []
    if b"\0" in raw:
        return None
    try:
        return raw.decode("utf-8").splitlines(keepends=True)
    except UnicodeDecodeError:
        return None


def compute_upstream_delta(
    old_tree: Mapping[str, bytes], new_tree: Mapping[str, bytes]
) -> tuple[tuple[ChangedFile, ...], str, str]:
    from .classifier import ChangedFile

    delta_digest = compute_delta_sha256(old_tree, new_tree)
    changed: list[ChangedFile] = []
    chunks: list[str] = []
    for path in sorted(set(old_tree) | set(new_tree)):
        old_bytes = old_tree.get(path)
        new_bytes = new_tree.get(path)
        if old_bytes == new_bytes:
            continue
        status = "added" if old_bytes is None else ("removed" if new_bytes is None else "modified")
        staged_path = (
            f"{STAGED_PROMOTION_DIR}/old/{path}" if status == "removed" else f"{STAGED_PROMOTION_DIR}/new/{path}"
        )
        old_lines = _text_lines(old_bytes)
        new_lines = _text_lines(new_bytes)
        if old_lines is None or new_lines is None:
            chunks.append(f"diff --git a/{path} b/{path}\nBinary files a/{path} and b/{path} differ\n")
            changed.append(ChangedFile(path=staged_path, previous_path=None, additions=0, deletions=0, status=status))
            continue
        diff_lines = list(
            difflib.unified_diff(
                old_lines,
                new_lines,
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
            )
        )
        added = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
        deleted = sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))
        chunks.append(f"diff --git a/{path} b/{path}\n" + "".join(diff_lines))
        changed.append(
            ChangedFile(path=staged_path, previous_path=None, additions=added, deletions=deleted, status=status)
        )
    return tuple(changed), "".join(chunks), delta_digest


def extract_tar_tree(archive_bytes: bytes, *, strip_single_root: bool = False) -> dict[str, bytes]:
    raw_entries: list[tuple[str, bytes]] = []
    total_bytes = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:*") as bundle:
            for member in bundle.getmembers():
                if member.isdir() or member.name == "pax_global_header":
                    continue
                _require(member.isfile(), f"upstream archive contains non-regular entry {member.name!r}")
                _require(member.size >= 0, f"upstream archive entry {member.name!r} has negative size")
                _require(len(raw_entries) < MAX_TREE_FILES, "upstream archive exceeds maximum file count")
                cleaned = member.name.lstrip("./")
                _require(valid_path(cleaned), f"upstream archive entry {member.name!r} has invalid path")
                extracted = bundle.extractfile(member)
                _require(extracted is not None, f"cannot read upstream archive entry {member.name!r}")
                data = extracted.read(MAX_TREE_BYTES - total_bytes + 1)
                total_bytes += len(data)
                _require(total_bytes <= MAX_TREE_BYTES, "upstream archive exceeds maximum uncompressed size")
                raw_entries.append((cleaned, data))
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise PromotionError(f"cannot read upstream source archive: {exc}") from exc
    if strip_single_root and raw_entries:
        roots = {name.split("/", 1)[0] for name, _ in raw_entries if "/" in name}
        _require(
            len(roots) == 1 and all("/" in name for name, _ in raw_entries),
            "upstream tarball does not have a single root directory",
        )
        stripped = [(name.split("/", 1)[1], data) for name, data in raw_entries]
    else:
        stripped = raw_entries
    files: dict[str, bytes] = {}
    for path, data in stripped:
        if path.startswith(".git/") or path == ".git":
            continue
        _require(valid_path(path), f"invalid upstream source path {path!r}")
        _require(path not in files, f"duplicate upstream source path {path!r}")
        files[path] = data
    return files


def load_tree_from_dir(directory: Path) -> dict[str, bytes]:
    _require(directory.is_dir() and not directory.is_symlink(), f"source directory {directory} is not a directory")
    root = directory.resolve()
    files: dict[str, bytes] = {}
    total_bytes = 0
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(name for name in dirnames if name != ".git")
        current_path = Path(current)
        for dirname in dirnames:
            _require(not (current_path / dirname).is_symlink(), f"symlink directory {dirname!r} is not allowed")
        for filename in sorted(filenames):
            target = current_path / filename
            _require(not target.is_symlink() and target.is_file(), f"non-regular file {filename!r} is not allowed")
            rel = target.relative_to(root).as_posix()
            _require(valid_path(rel), f"invalid relative path {rel!r}")
            data = target.read_bytes()
            total_bytes += len(data)
            _require(total_bytes <= MAX_TREE_BYTES, "source directory exceeds maximum size")
            _require(len(files) < MAX_TREE_FILES, "source directory exceeds maximum file count")
            files[rel] = data
    return files


def _git_archive_tree(git_root: Path, commit_sha: str) -> dict[str, bytes]:
    result = subprocess.run(
        ["git", "-C", str(git_root), "archive", "--format=tar", commit_sha],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()[:300]
        raise PromotionError(f"git archive {commit_sha[:12]} failed: {detail}")
    return extract_tar_tree(result.stdout, strip_single_root=False)


def default_source_loader(
    *, local_git_root: Path | None = None, local_consumer_repo: str | None = None
) -> SourceLoader:
    configured = os.environ.get(SOURCES_ENV, "").strip()

    def load(repo_slug: str, commit_sha: str) -> dict[str, bytes]:
        if configured:
            base = Path(configured)
            folder = base / commit_sha
            tar_file = base / f"{commit_sha}.tar"
            tgz_file = base / f"{commit_sha}.tar.gz"
            if folder.is_dir():
                return load_tree_from_dir(folder)
            if tar_file.is_file():
                return extract_tar_tree(tar_file.read_bytes(), strip_single_root=False)
            if tgz_file.is_file():
                return extract_tar_tree(tgz_file.read_bytes(), strip_single_root=True)
            if (base / ".git").exists():
                return _git_archive_tree(base, commit_sha)
            raise PromotionError(f"promotion source {commit_sha} was not found under {SOURCES_ENV}")
        if local_git_root is not None and local_consumer_repo == repo_slug:
            return _git_archive_tree(local_git_root, commit_sha)
        try:
            archive = github._gh_bytes(f"repos/{repo_slug}/tarball/{commit_sha}")
        except github.GitHubError as exc:
            raise PromotionError(f"cannot fetch {repo_slug}@{commit_sha[:12]}: {exc}") from exc
        return extract_tar_tree(archive, strip_single_root=True)

    return load


def ci_ancestor_check(repo: str) -> AncestorCheck:
    def check(ancestor: str, descendant: str) -> bool:
        if ancestor == descendant:
            return True
        try:
            comparison = github.get_json(f"repos/{repo}/compare/{ancestor}...{descendant}")
        except (github.GitHubError, ValueError):
            return False
        return comparison.get("status") in ("ahead", "identical")

    return check


def local_ancestor_check(git_root: Path) -> AncestorCheck:
    def check(ancestor: str, descendant: str) -> bool:
        result = subprocess.run(
            ["git", "-C", str(git_root), "merge-base", "--is-ancestor", ancestor, descendant],
            capture_output=True,
            check=False,
        )
        return result.returncode == 0

    return check


def has_planted_promotion_path(paths: Iterable[str]) -> bool:
    return any(path == STAGED_PROMOTION_DIR or path.startswith(f"{STAGED_PROMOTION_DIR}/") for path in paths)


def _validate_bootstrap_against_policy(
    bootstrap: BootstrapRecord | None,
    head_lock: TheseusLock,
    *,
    repo: str,
    base_sha: str | None,
    is_ancestor: AncestorCheck | None,
    bootstrap_baseline_commit: str | None,
    bootstrap_path_map: Mapping[str, str] | None,
    bootstrap_reviewers: Sequence[str],
) -> list[str]:
    if bootstrap is None:
        return ["first theseus promotion requires a bootstrap baseline record"]
    if bootstrap_baseline_commit is None or not bootstrap_path_map:
        return [
            "first theseus promotion requires bootstrap_baseline_commit and bootstrap_path_map in the review policy"
        ]
    reasons: list[str] = []
    if bootstrap.baseline_repo != repo:
        reasons.append("bootstrap baseline_repo does not match this repository")
    if bootstrap.approved_head != head_lock.source_commit:
        reasons.append("bootstrap approved_head does not match the promoted source_commit")
    if bootstrap.baseline_commit != bootstrap_baseline_commit:
        reasons.append("bootstrap baseline_commit does not match the configured baseline commit")
    if dict(bootstrap.path_map) != dict(bootstrap_path_map):
        reasons.append("bootstrap path_map does not match the configured bootstrap_path_map")
    if bootstrap.reviewer not in bootstrap_reviewers:
        reasons.append("bootstrap reviewer is not in the configured bootstrap_reviewers")
    if is_ancestor is not None and base_sha is not None and not is_ancestor(bootstrap.baseline_commit, base_sha):
        reasons.append("bootstrap baseline_commit is not an ancestor of the base branch")
    return reasons


def prepare_promotion(
    *,
    repo: str,
    base_sha: str,
    head_sha: str,
    base_lock_text: str | None,
    head_lock_text: str | None,
    source_loader: SourceLoader,
    is_ancestor: AncestorCheck,
    pr_paths: Iterable[str] = (),
    expected_package: str = "theseus-reviewer",
    expected_repository: str = "BenchBox-dev/theseus",
    bootstrap_baseline_commit: str | None = None,
    bootstrap_path_map: Mapping[str, str] | None = None,
    bootstrap_reviewers: Sequence[str] = ("joeharris76",),
) -> PromotionBundle | None:
    _require(not has_planted_promotion_path(pr_paths), f"pull request cannot touch {STAGED_PROMOTION_DIR}")
    if head_lock_text is None:
        if base_lock_text is not None:
            raise PromotionError("theseus lock cannot be removed")
        return None
    head_lock = parse_lock(head_lock_text)
    _require(head_lock.package == expected_package, "theseus lock package does not match consumer policy")
    _require(head_lock.repository == expected_repository, "theseus lock repository does not match consumer policy")
    if base_lock_text is not None:
        base_lock = parse_lock(base_lock_text)
        _require(
            head_lock.bootstrap is None,
            "bootstrap baseline is only valid when no prior theseus lock exists on the base branch",
        )
        _require(
            (head_lock.package, head_lock.repository) == (base_lock.package, base_lock.repository),
            "theseus lock package/repository cannot change across promotions",
        )
        if lock_digest(base_lock) == lock_digest(head_lock):
            return None
        old_tree = source_loader(base_lock.repository, base_lock.source_commit)
        _require(
            tree_sha256(old_tree) == base_lock.source_sha256,
            "prior locked theseus source tree does not match base lock source_sha256",
        )
        old_source_sha256 = base_lock.source_sha256
        old_artifact_sha256 = base_lock.artifact_sha256
        is_bootstrap = False
    else:
        bootstrap_errors = _validate_bootstrap_against_policy(
            head_lock.bootstrap,
            head_lock,
            repo=repo,
            base_sha=base_sha,
            is_ancestor=is_ancestor,
            bootstrap_baseline_commit=bootstrap_baseline_commit,
            bootstrap_path_map=bootstrap_path_map,
            bootstrap_reviewers=bootstrap_reviewers,
        )
        _require(not bootstrap_errors, "; ".join(bootstrap_errors))
        bootstrap = head_lock.bootstrap
        assert bootstrap is not None
        raw_baseline = source_loader(bootstrap.baseline_repo, bootstrap.baseline_commit)
        old_tree = apply_path_map(raw_baseline, bootstrap.path_map)
        _require(
            tree_sha256(old_tree) == bootstrap.baseline_source_sha256,
            "mapped bootstrap baseline tree does not match baseline_source_sha256",
        )
        old_source_sha256 = bootstrap.baseline_source_sha256
        old_artifact_sha256 = BOOTSTRAP_ARTIFACT_SHA256
        is_bootstrap = True
    new_tree = source_loader(head_lock.repository, head_lock.source_commit)
    _require(
        tree_sha256(new_tree) == head_lock.source_sha256,
        "promoted theseus source tree does not match head lock source_sha256",
    )
    changed_files, diff_text, actual_delta_sha256 = compute_upstream_delta(old_tree, new_tree)
    _require(
        actual_delta_sha256 == head_lock.delta_sha256,
        "theseus lock delta_sha256 does not match the materialized upstream delta",
    )
    record = PromotionRecord(
        head_sha=head_sha,
        old_source_sha256=old_source_sha256,
        new_source_sha256=head_lock.source_sha256,
        old_artifact_sha256=old_artifact_sha256,
        new_artifact_sha256=head_lock.artifact_sha256,
        delta_sha256=actual_delta_sha256,
        bootstrap=is_bootstrap,
    )
    return PromotionBundle(
        record=record,
        changed_files=changed_files,
        diff_text=diff_text,
        old_tree=old_tree,
        new_tree=new_tree,
    )


def _write_tree_secure(target: Path, tree: Mapping[str, bytes]) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for rel, content in sorted(tree.items()):
        _require(valid_path(rel), f"invalid staged path {rel!r}")
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(dest, flags, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)


def write_materialized_bundle(bundle: PromotionBundle, plan_out_dir: Path) -> Path:
    root = plan_out_dir / PROMOTION_PLAN_DIR
    if root.exists():
        shutil.rmtree(root)
    _write_tree_secure(root / "old", bundle.old_tree)
    _write_tree_secure(root / "new", bundle.new_tree)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(root / UPSTREAM_DIFF_NAME, flags, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
        handle.write(bundle.diff_text)
    return root


def _workspace_has_tracked_promotion_dir(workspace: Path) -> bool:
    head_obj = subprocess.run(
        ["git", "-C", str(workspace), "cat-file", "-e", f"HEAD:{STAGED_PROMOTION_DIR}"],
        capture_output=True,
        check=False,
    )
    if head_obj.returncode == 0:
        return True
    tracked = subprocess.run(
        ["git", "-C", str(workspace), "ls-files", "--", STAGED_PROMOTION_DIR],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return tracked.returncode != 0 or bool(tracked.stdout.strip())


def stage_promotion_workspace(plan_dir: Path, workspace: Path) -> Path | None:
    source = plan_dir / PROMOTION_PLAN_DIR
    target = workspace / STAGED_PROMOTION_DIR
    if not source.is_dir() or target.is_symlink() or _workspace_has_tracked_promotion_dir(workspace):
        return None
    try:
        expected_digest = tree_sha256(load_tree_from_dir(source))
    except PromotionError:
        return None
    if os.path.lexists(target):
        return target if staged_tree_digest(workspace) == expected_digest else None
    result = subprocess.run(
        ["git", "-C", str(workspace), "rev-parse", "--git-path", "info/exclude"],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    exclude = result.stdout.strip() if result.returncode == 0 else ""
    if not exclude:
        return None
    exclude_path = workspace / exclude
    try:
        exclude_path.parent.mkdir(parents=True, exist_ok=True)
        existing = exclude_path.read_text(encoding="utf-8") if exclude_path.is_file() else ""
        rule = f"/{STAGED_PROMOTION_DIR}\n"
        if rule not in existing:
            with exclude_path.open("a", encoding="utf-8") as handle:
                handle.write(rule)
        old_tree = load_tree_from_dir(source / "old") if (source / "old").is_dir() else {}
        new_tree = load_tree_from_dir(source / "new") if (source / "new").is_dir() else {}
        diff_bytes = (source / UPSTREAM_DIFF_NAME).read_bytes() if (source / UPSTREAM_DIFF_NAME).is_file() else b""
        _write_tree_secure(target / "old", old_tree)
        _write_tree_secure(target / "new", new_tree)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(target / UPSTREAM_DIFF_NAME, flags, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(diff_bytes)
    except (OSError, PromotionError):
        return None
    return target


def staged_tree_digest(workspace: Path) -> str | None:
    target = workspace / STAGED_PROMOTION_DIR
    if not os.path.lexists(target):
        return None
    if target.is_symlink() or not target.is_dir():
        return "invalid"
    try:
        files = load_tree_from_dir(target)
    except PromotionError:
        return "invalid"
    return tree_sha256(files)


def _parse_review_time(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _head_app_records(
    reviews: Iterable[Mapping[str, Any]],
    *,
    head_sha: str,
    oracle_login: str,
    base_time: datetime | None,
) -> tuple[list[dict[str, Any]], str | None]:
    from . import protocol
    from .base_change import survives

    own: list[Mapping[str, Any]] = []
    for review in reviews:
        login = str(review.get("login") or "").removesuffix("[bot]")
        if login != oracle_login or review.get("user_type") != "Bot":
            continue
        if review.get("state") in {"PENDING", "DISMISSED"}:
            continue
        submitted = _parse_review_time(review.get("submitted_at"))
        if submitted is None or not survives(submitted, base_time):
            continue
        if review.get("commit_id") != head_sha:
            continue
        if "last_edited_at" not in review or "includes_created_edit" not in review:
            return [], f"an oracle review on {head_sha[:12]} has no readable edit state"
        if review.get("last_edited_at") is not None or review.get("includes_created_edit"):
            return [], f"an oracle review on {head_sha[:12]} was edited after submission"
        own.append(review)
    ordered = sorted(
        own, key=lambda item: (_parse_review_time(item.get("submitted_at")) or datetime.min, item.get("id") or 0)
    )
    decoded: list[dict[str, Any]] = []
    for candidate in ordered:
        try:
            record = protocol.decode_marker(str(candidate.get("body") or ""))
        except protocol.MarkerError as exc:
            return [], f"an oracle review on {head_sha[:12]} has an invalid protocol marker: {exc}"
        if record is not None:
            decoded.append(record)
    return decoded, None


def _baseline_expectations(
    base_lock: TheseusLock | None,
    head_lock: TheseusLock,
    *,
    repo: str,
    expected_package: str,
    expected_repository: str,
    bootstrap_baseline_commit: str | None,
    bootstrap_path_map: Mapping[str, str] | None,
    bootstrap_reviewers: Sequence[str],
    is_ancestor: AncestorCheck | None,
    base_sha: str | None,
) -> tuple[tuple[str, str, bool] | None, list[str]]:
    reasons: list[str] = []
    if head_lock.package != expected_package:
        reasons.append("theseus lock package does not match consumer policy")
    if head_lock.repository != expected_repository:
        reasons.append("theseus lock repository does not match consumer policy")
    if base_lock is not None:
        if head_lock.bootstrap is not None:
            reasons.append("bootstrap baseline is only allowed on the first theseus promotion")
        if (head_lock.package, head_lock.repository) != (base_lock.package, base_lock.repository):
            reasons.append("theseus lock package/repository cannot change across promotions")
        return (base_lock.source_sha256, base_lock.artifact_sha256, False), reasons
    bootstrap_reasons = _validate_bootstrap_against_policy(
        head_lock.bootstrap,
        head_lock,
        repo=repo,
        base_sha=base_sha,
        is_ancestor=is_ancestor,
        bootstrap_baseline_commit=bootstrap_baseline_commit,
        bootstrap_path_map=bootstrap_path_map,
        bootstrap_reviewers=bootstrap_reviewers,
    )
    reasons.extend(bootstrap_reasons)
    if head_lock.bootstrap is None:
        return None, reasons
    return (head_lock.bootstrap.baseline_source_sha256, BOOTSTRAP_ARTIFACT_SHA256, True), reasons


def _record_refusals(
    decoded_records: Sequence[Mapping[str, Any]],
    *,
    head_lock: TheseusLock,
    head_sha: str,
    expected_old_source: str,
    expected_old_artifact: str,
    expected_bootstrap: bool,
) -> list[str]:
    from . import protocol

    if not decoded_records:
        return [
            f"theseus promotion to {head_lock.version} has no unedited oracle promotion review for head {head_sha[:12]}"
        ]
    latest = decoded_records[-1]
    if latest.get("open_defects") or latest.get("decision") != protocol.SHIP:
        count = len(latest.get("open_defects") or ())
        return [
            f"theseus promotion review for head {head_sha[:12]} decided {latest.get('decision')} with {count} open defect(s)"
        ]
    if latest.get("carried") or latest.get("kind") == protocol.CARRY:
        return [f"theseus promotion review for head {head_sha[:12]} cannot be carried across heads"]
    promo = latest.get("promotion")
    if not valid_promotion_record(promo):
        return [
            f"theseus promotion to {head_lock.version} has no oracle promotion evidence record for head {head_sha[:12]}"
        ]
    assert isinstance(promo, Mapping)
    reasons: list[str] = []
    if promo["head_sha"] != head_sha or latest.get("head_sha") != head_sha:
        reasons.append(f"promotion evidence record head does not match {head_sha[:12]}")
    if promo["old_source_sha256"] != expected_old_source or promo["new_source_sha256"] != head_lock.source_sha256:
        reasons.append("promotion evidence record source hashes do not match the base/head lock")
    if (
        promo["old_artifact_sha256"] != expected_old_artifact
        or promo["new_artifact_sha256"] != head_lock.artifact_sha256
    ):
        reasons.append("promotion evidence record artifact hashes do not match the base/head lock")
    if promo["delta_sha256"] != head_lock.delta_sha256:
        reasons.append("promotion evidence record delta digest does not match the lock delta_sha256")
    if promo["bootstrap"] != expected_bootstrap:
        reasons.append("promotion evidence record bootstrap flag does not match the base lock state")
    return reasons


def refusals(
    files: Sequence[str],
    base_text: str | None,
    head_text: str | None,
    reviews: Sequence[Mapping[str, Any]],
    head_sha: str,
    *,
    repo: str,
    complete: bool = True,
    base_time: datetime | None = None,
    oracle_login: str = "benchbox-oracle",
    expected_package: str = "theseus-reviewer",
    expected_repository: str = "BenchBox-dev/theseus",
    bootstrap_baseline_commit: str | None = None,
    bootstrap_path_map: Mapping[str, str] | None = None,
    bootstrap_reviewers: Sequence[str] = ("joeharris76",),
    is_ancestor: AncestorCheck | None = None,
    base_sha: str | None = None,
    matcher_sha256_at_head: str | None = None,
    expected_run_uuid: str | None = None,
) -> tuple[bool, list[str]]:
    if has_planted_promotion_path(files):
        return True, [f"pull request cannot touch {STAGED_PROMOTION_DIR}"]
    if LOCK_PATH not in files and complete and base_text == head_text:
        return False, []
    if head_text is None:
        return (True, ["theseus lock cannot be removed"]) if base_text is not None else (False, [])
    try:
        head_lock = parse_lock(head_text)
        base_lock = parse_lock(base_text) if base_text is not None else None
    except PromotionError as exc:
        return True, [str(exc)]
    if base_lock is not None and head_lock.bootstrap is None and lock_digest(base_lock) == lock_digest(head_lock):
        return False, []
    reasons = [] if complete else ["the pull request's file list is incomplete, so promotion scope cannot be verified"]
    expected, baseline_reasons = _baseline_expectations(
        base_lock,
        head_lock,
        repo=repo,
        expected_package=expected_package,
        expected_repository=expected_repository,
        bootstrap_baseline_commit=bootstrap_baseline_commit,
        bootstrap_path_map=bootstrap_path_map,
        bootstrap_reviewers=bootstrap_reviewers,
        is_ancestor=is_ancestor,
        base_sha=base_sha,
    )
    reasons.extend(baseline_reasons)
    if expected is None:
        return True, reasons
    if head_lock.matcher is not None and matcher_sha256_at_head != head_lock.matcher.sha256:
        reasons.append(f"vendored matcher {head_lock.matcher.path} does not match the sha256 in {LOCK_PATH}")
    decoded, history_error = _head_app_records(
        reviews, head_sha=head_sha, oracle_login=oracle_login, base_time=base_time
    )
    if history_error is not None:
        reasons.append(history_error)
        return True, reasons
    old_src, old_art, expected_bootstrap = expected
    reasons.extend(
        _record_refusals(
            decoded,
            head_lock=head_lock,
            head_sha=head_sha,
            expected_old_source=old_src,
            expected_old_artifact=old_art,
            expected_bootstrap=expected_bootstrap,
        )
    )
    if not reasons and expected_run_uuid is not None and decoded and decoded[-1].get("run_uuid") != expected_run_uuid:
        reasons.append("promotion review run_uuid does not match the latest oracle-verdict status")
    return True, reasons
