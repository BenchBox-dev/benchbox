#!/usr/bin/env python3

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})

MAX_CURATED_BULLETS = 45
MAX_CURATED_LINES = 160
_PR_SUFFIX_RE = re.compile(r"\(#\d+\)\s*$")
_BULLET_RE = re.compile(r"^\s*- \S")

_CHANGELOG_VERSION_HEADER_RE = re.compile(r"^## \[(?P<version>\d+\.\d+\.\d+)\] - ", re.MULTILINE)

_PYPROJECT_VERSION_RE = re.compile(r'^version\s*=\s*["\'](?P<version>[^"\']+)["\']', re.MULTILINE)
_STABLE_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_PYPI_RELEASE_URL = "https://pypi.org/pypi/benchbox/json"

_RELEASE_BRANCH_RE = re.compile(r"^v(?P<version>\d+\.\d+\.\d+)(-[A-Za-z0-9.+-]+)?$")

_ANY_SECTION_RE = re.compile(r"^## \[(?P<label>[^\]]+)\]")
_LINK_RE = re.compile(r"^\[(?P<label>[^\]]+)\]: (?P<url>\S+)\s*$")
_HEADING_RE = re.compile(r"^### (?P<name>.+?)\s*$")
_COMPARE_BASE = "https://github.com/BenchBox-dev/BenchBox/compare/"
_TAG_BASE = "https://github.com/BenchBox-dev/BenchBox/releases/tag/"
_FRAGMENT_DIR = "changelog.d"


def _run_git(source: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=source,
        capture_output=True,
        text=True,
        check=check,
    )


def released_versions_in_changelog(changelog_text: str) -> list[str]:
    return [m.group("version") for m in _CHANGELOG_VERSION_HEADER_RE.finditer(changelog_text)]


def section_body(changelog_text: str, version: str) -> str | None:
    header = re.search(rf"^## \[{re.escape(version)}\] - .*$", changelog_text, re.MULTILINE)
    if header is None:
        return None
    rest = changelog_text[header.end() :]
    following = re.search(r"^## \[", rest, re.MULTILINE)
    return rest[: following.start()] if following else rest


def has_changelog_section(source: Path, version: str) -> bool:
    changelog = source / "CHANGELOG.md"
    if not changelog.exists():
        return False
    return section_body(changelog.read_text(encoding="utf-8"), version) is not None


def curated_section_from_file(path: Path, version: str, release_date: str) -> tuple[str | None, str | None]:
    try:
        body = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        return None, f"cannot read curated section file {path}: {exc}"
    if not body:
        return None, f"curated section file {path} is empty"
    if re.search(r"^## ", body, re.MULTILINE):
        return None, f"curated section file {path} must hold the section body only, without a '## ' version header"
    return f"## [{version}] - {release_date}\n\n{body}\n", None


def _insert_section(content: str, new_section: str) -> str:
    first_release = re.search(r"^## \[\d", content, re.MULTILINE)
    if first_release is None:
        return content.rstrip() + "\n\n" + new_section
    return content[: first_release.start()] + new_section + "\n" + content[first_release.start() :]


def _replace_section(content: str, version: str, new_section: str) -> str:
    header = re.search(rf"^## \[{re.escape(version)}\] - .*$", content, re.MULTILINE)
    assert header is not None
    following = re.search(r"^## \[", content[header.end() :], re.MULTILINE)
    end = header.end() + following.start() if following else len(content)
    return content[: header.start()] + new_section + ("\n" if following else "") + content[end:]


def _write_curated_section(changelog: Path, version: str, release_date: str, section_file: Path) -> bool:
    new_section, error = curated_section_from_file(section_file, version, release_date)
    if new_section is None:
        print(f"  Error: {error}")
        return False
    content = changelog.read_text(encoding="utf-8")
    if section_body(content, version) is None:
        content = _insert_section(content, new_section)
        print(f"  Wrote the [{version}] section from {section_file}")
    else:
        content = _replace_section(content, version, new_section)
        print(f"  Replaced the existing [{version}] section with {section_file}")
    changelog.write_text(content, encoding="utf-8")
    return True


def check_changelog_curation(source: Path, version: str) -> tuple[bool, list[str]]:
    changelog = source / "CHANGELOG.md"
    if not changelog.exists():
        return False, [f"{changelog} not found"]

    body = section_body(changelog.read_text(encoding="utf-8"), version)
    if body is None:
        return False, [f"no '## [{version}] - <date>' section found in CHANGELOG.md"]

    bullets = [line.strip() for line in body.splitlines() if _BULLET_RE.match(line)]
    problems: list[str] = []
    if not bullets:
        problems.append("section contains no bullets")
    problems.extend(f"duplicate '### {name}' heading" for name in _duplicate_headings(body))
    raw_bullets = [b for b in bullets if _PR_SUFFIX_RE.search(b)]
    if raw_bullets:
        problems.append(
            f"{len(raw_bullets)} bullet(s) are verbatim commit subjects "
            f"(trailing PR number), e.g. {raw_bullets[0][:70]!r}"
        )
    if len(bullets) > MAX_CURATED_BULLETS:
        problems.append(f"{len(bullets)} bullets exceeds the {MAX_CURATED_BULLETS}-bullet curated ceiling")
    body_lines = len(body.splitlines())
    if body_lines > MAX_CURATED_LINES:
        problems.append(f"{body_lines} lines exceeds the {MAX_CURATED_LINES}-line curated ceiling")
    return (not problems), problems


def _duplicate_headings(body: str) -> list[str]:
    seen: set[str] = set()
    duplicated: list[str] = []
    for _index, name in _heading_positions(body.splitlines()):
        if name in seen and name not in duplicated:
            duplicated.append(name)
        seen.add(name)
    return duplicated


def curated_section_body(source: Path, version: str) -> tuple[str | None, list[str]]:
    ok, problems = check_changelog_curation(source, version)
    if not ok:
        return None, problems
    body = section_body((source / "CHANGELOG.md").read_text(encoding="utf-8"), version)
    if body is None or not body.strip():
        return None, ["section body is empty"]
    return body.strip(), []


def existing_tags(source: Path) -> set[str]:
    local = _run_git(source, "tag", "-l", "v*")
    if local.returncode != 0:
        raise RuntimeError(f"could not inspect local release tags: {local.stderr.strip() or 'git tag failed'}")
    tags = {line.strip() for line in local.stdout.splitlines() if line.strip()}

    shallow = _run_git(source, "rev-parse", "--is-shallow-repository")
    if shallow.returncode != 0:
        raise RuntimeError(f"could not inspect checkout depth: {shallow.stderr.strip() or 'git rev-parse failed'}")
    is_shallow = shallow.stdout.strip() == "true"
    if tags and not is_shallow:
        return tags

    remote = _run_git(source, "ls-remote", "--tags", "origin", "refs/tags/v*")
    if remote.returncode != 0:
        reason = remote.stderr.strip() or "git ls-remote failed"
        raise RuntimeError(f"could not discover release tags from origin: {reason}")
    for line in remote.stdout.splitlines():
        ref = line.strip().rsplit("\t", 1)[-1]
        name = ref.rsplit("/", 1)[-1]
        if name and not name.endswith("^{}"):
            tags.add(name)
    if not tags:
        raise RuntimeError("no release tags were discovered locally or on origin")
    return tags


def find_untagged_changelog_versions(
    changelog_text: str,
    tags: set[str],
    *,
    current_branch: str | None = None,
    prepared_version: str | None = None,
) -> list[str]:
    untagged = [
        v for v in released_versions_in_changelog(changelog_text) if f"v{v}" not in tags and v != prepared_version
    ]
    if not untagged:
        return []
    branch_match = _RELEASE_BRANCH_RE.match(current_branch) if current_branch else None
    if branch_match:
        drafted_version = branch_match.group("version")
        untagged = [v for v in untagged if v != drafted_version]
    return untagged


def _current_branch(source: Path) -> str | None:
    result = _run_git(source, "rev-parse", "--abbrev-ref", "HEAD")
    branch = result.stdout.strip()
    if result.returncode == 0 and branch and branch != "HEAD":
        return branch
    env_branch = os.environ.get("GITHUB_HEAD_REF", "").strip()
    return env_branch or None


def check_tag_claims(source: Path) -> tuple[bool, list[str]]:
    changelog_path = source / "CHANGELOG.md"
    text = changelog_path.read_text(encoding="utf-8")
    pyproject = source / "pyproject.toml"
    version_match = _PYPROJECT_VERSION_RE.search(pyproject.read_text(encoding="utf-8")) if pyproject.is_file() else None
    tags = existing_tags(source)
    branch = _current_branch(source)
    untagged = find_untagged_changelog_versions(
        text,
        tags,
        current_branch=branch,
        prepared_version=version_match.group("version") if version_match else None,
    )
    return (not untagged, untagged)


def latest_published_version() -> str:
    try:
        with urllib.request.urlopen(_PYPI_RELEASE_URL, timeout=15) as response:
            payload = json.load(response)
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"could not read published BenchBox version from PyPI: {exc}") from exc

    version = payload.get("info", {}).get("version") if isinstance(payload, dict) else None
    if not isinstance(version, str) or not _STABLE_VERSION_RE.fullmatch(version):
        raise RuntimeError(f"PyPI returned an invalid stable BenchBox version: {version!r}")
    return version


def find_release_accounting_errors(
    changelog_text: str,
    tags: set[str],
    *,
    published_version: str,
    pyproject_version: str | None,
    current_branch: str | None = None,
) -> list[str]:
    if not _STABLE_VERSION_RE.fullmatch(published_version):
        return [f"published version {published_version!r} is not a stable X.Y.Z version"]
    tag = f"v{published_version}"

    errors: list[str] = []
    if tag not in tags:
        errors.append(f"published PyPI version {published_version} has no visible git tag {tag}")

    if published_version not in released_versions_in_changelog(changelog_text):
        errors.append(f"CHANGELOG.md has no dated [{published_version}] section for published version {tag}")

    expected_compare = f"[Unreleased]: https://github.com/BenchBox-dev/BenchBox/compare/{tag}...HEAD"
    if expected_compare not in changelog_text:
        errors.append(f"CHANGELOG.md [Unreleased] comparison does not start at {tag}")

    branch_match = _RELEASE_BRANCH_RE.fullmatch(current_branch) if current_branch else None
    expected_project_version = branch_match.group("version") if branch_match else published_version
    if pyproject_version != expected_project_version:
        expectation = f"release branch v{expected_project_version}" if branch_match else f"published PyPI version {tag}"
        errors.append(f"pyproject.toml version {pyproject_version!r} does not match {expectation}")

    return errors


def check_release_accounting(source: Path, published_version: str) -> tuple[bool, list[str]]:
    changelog_text = (source / "CHANGELOG.md").read_text(encoding="utf-8")
    pyproject_text = (source / "pyproject.toml").read_text(encoding="utf-8")
    version_match = _PYPROJECT_VERSION_RE.search(pyproject_text)
    pyproject_version = version_match.group("version") if version_match else None
    errors = find_release_accounting_errors(
        changelog_text,
        existing_tags(source),
        published_version=published_version,
        pyproject_version=pyproject_version,
        current_branch=_current_branch(source),
    )
    return (not errors, errors)


def update_github_release_notes(source: Path, version: str, body: str) -> None:
    tag = f"v{version}"
    notes_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as notes_file:
            notes_file.write(body.strip() + "\n")
            notes_path = Path(notes_file.name)
        result = subprocess.run(
            ["gh", "release", "edit", tag, "--notes-file", str(notes_path)],
            cwd=source,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            reason = result.stderr.strip() or result.stdout.strip() or "gh release edit failed"
            raise RuntimeError(f"could not update GitHub Release {tag}: {reason}")
    finally:
        if notes_path is not None:
            notes_path.unlink(missing_ok=True)


def _publishable_body_or_report(source: Path, version: str) -> str | None:
    body, problems = curated_section_body(source, version)
    if body is not None:
        return body
    print(f"  Error: CHANGELOG.md section [{version}] is not publishable:", file=sys.stderr)
    for problem in problems:
        print(f"    - {problem}", file=sys.stderr)
    return None


def _run_release_accounting_check(source: Path) -> int:
    try:
        published_version = latest_published_version()
        ok, errors = check_release_accounting(source, published_version)
    except RuntimeError as exc:
        print(f"  Error: {exc}", file=sys.stderr)
        return 1
    if not ok:
        print(f"  Repository accounting does not match published PyPI version v{published_version}:")
        for error in errors:
            print(f"    - {error}")
        return 1
    print(f"  Published-release accounting guard: OK (PyPI v{published_version})")
    return 0


def _run_release_notes_sync(source: Path, version: str) -> int:
    body = _publishable_body_or_report(source, version)
    if body is None:
        return 1
    try:
        update_github_release_notes(source, version, body)
    except RuntimeError as exc:
        print(f"  Error: {exc}", file=sys.stderr)
        return 1
    print(f"  Updated GitHub Release v{version} from curated CHANGELOG.md notes")
    return 0


def _conventional_changelog_subject(subject: str) -> bool:
    if ":" not in subject:
        return False
    prefix = subject.split(":", 1)[0].strip().lower()
    bare_prefix = prefix.split("(", 1)[0].strip()
    return bare_prefix in {"feat", "fix", "perf"}


def _commits_since_ref(source: Path, ref: str) -> list[tuple[str, str]] | None:
    result = _run_git(source, "log", "--format=%H%x00%s%x00", f"{ref}..HEAD")
    if result.returncode != 0:
        print(f"  Error: Failed to get git log: {result.stderr}")
        return None
    parts = [part for part in result.stdout.split("\0") if part]
    return [
        (commit_hash.strip(), subject.strip())
        for commit_hash, subject in zip(parts[0::2], parts[1::2], strict=False)
        if commit_hash.strip()
    ]


def _sibling_module(name: str):
    key = f"_generate_changelog_entry_{name}"
    cached = sys.modules.get(key)
    if cached is not None:
        return cached
    path = Path(__file__).resolve().with_name(f"{name}.py")
    spec = importlib.util.spec_from_file_location(key, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


def _heading_positions(lines: list[str]) -> list[tuple[int, str]]:
    positions: list[tuple[int, str]] = []
    fenced = False
    for index, line in enumerate(lines):
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
            continue
        match = None if fenced else _HEADING_RE.match(line)
        if match:
            positions.append((index, match.group("name")))
    return positions


def _trim(lines: list[str]) -> list[str]:
    start = 0
    end = len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return lines[start:end]


def _insert_entries(core: list[str], name: str, entry_lines: list[str], rank: dict[str, int]) -> list[str]:
    positions = _heading_positions(core)
    for number, (start, existing) in enumerate(positions):
        if existing != name:
            continue
        stop = positions[number + 1][0] if number + 1 < len(positions) else len(core)
        last = max((index for index in range(start + 1, stop) if core[index].strip()), default=None)
        if last is not None:
            return [*core[: last + 1], *entry_lines, *core[last + 1 :]]
        tail = core[start + 1 :]
        gap = [""] if tail and tail[0].strip() else []
        return [*core[: start + 1], "", *entry_lines, *gap, *tail]
    block = [f"### {name}", "", *entry_lines]
    for start, existing in positions:
        if rank.get(existing, -1) > rank[name]:
            return [*core[:start], *block, "", *core[start:]]
    return [*core, "", *block] if core else block


def _merge_entries(core: list[str], rendered: str) -> list[str]:
    fragments = _sibling_module("changelog_fragments")
    rank = {fragments.HEADINGS[category]: index for index, category in enumerate(fragments.CATEGORIES)}
    new = _trim(rendered.splitlines())
    positions = _heading_positions(new)
    merged = list(core)
    for number, (start, name) in enumerate(positions):
        stop = positions[number + 1][0] if number + 1 < len(positions) else len(new)
        merged = _insert_entries(merged, name, _trim(new[start + 1 : stop]), rank)
    return merged


def _split_changelog(text: str) -> tuple[list[str], list[tuple[str, list[str]]], list[str]]:
    lines = text.splitlines()
    end = len(lines)
    while end > 0 and (not lines[end - 1].strip() or _LINK_RE.match(lines[end - 1])):
        end -= 1
    links = [line for line in lines[end:] if _LINK_RE.match(line)]
    content = lines[:end]
    headers = [index for index, line in enumerate(content) if _ANY_SECTION_RE.match(line)]
    preamble = content[: headers[0]] if headers else content
    sections: list[tuple[str, list[str]]] = []
    for number, start in enumerate(headers):
        stop = headers[number + 1] if number + 1 < len(headers) else len(content)
        match = _ANY_SECTION_RE.match(content[start])
        assert match is not None
        sections.append((match.group("label"), content[start:stop]))
    return preamble, sections, links


def _folded_prereleases(labels: list[str], version: str) -> list[str]:
    from packaging.version import InvalidVersion, Version

    target = Version(version)
    found: list[tuple[Version, str]] = []
    for label in labels:
        try:
            candidate = Version(label)
        except InvalidVersion:
            continue
        if candidate.is_prerelease and candidate.base_version == target.base_version and candidate < target:
            found.append((candidate, label))
    return [label for _candidate, label in sorted(found)]


def _compare_links(
    links: list[str], folded: list[str], version: str, previous_final: str | None, final: bool
) -> list[str]:
    urls: dict[str, str] = {}
    for line in links:
        match = _LINK_RE.match(line)
        assert match is not None
        if match.group("label") not in folded:
            urls[match.group("label")] = match.group("url")
    if previous_final is None:
        version_url = f"{_TAG_BASE}v{version}"
    else:
        version_url = f"{_COMPARE_BASE}{previous_final}...v{version}"
    if final:
        unreleased_url: str | None = f"{_COMPARE_BASE}v{version}...HEAD"
    elif "Unreleased" in urls:
        unreleased_url = urls["Unreleased"]
    else:
        unreleased_url = f"{_COMPARE_BASE}{previous_final}...HEAD" if previous_final else None
    ordered: list[tuple[str, str]] = []
    if unreleased_url is not None:
        ordered.append(("Unreleased", unreleased_url))
    ordered.append((version, version_url))
    ordered.extend((label, url) for label, url in urls.items() if label not in ("Unreleased", version))
    return [f"[{label}]: {url}" for label, url in ordered]


def compile_changelog(
    text: str, version: str, release_date: str, rendered: str, previous_final: str | None
) -> tuple[str | None, list[str]]:
    from packaging.version import Version

    fragments = _sibling_module("changelog_fragments")
    violations = fragments.unreleased_violations(text)
    if violations:
        return None, violations
    preamble, sections, links = _split_changelog(text)
    labels = [label for label, _lines in sections]
    existing = labels.index(version) if version in labels else None
    folded = [] if existing is not None else _folded_prereleases(labels, version)
    if existing is not None:
        header, draft = sections[existing][1][0], sections[existing][1][1:]
    elif folded:
        header = f"## [{version}] - {release_date}"
        draft = sections[labels.index(folded[-1])][1][1:]
    else:
        header, draft = f"## [{version}] - {release_date}", []
    core = _trim(draft)
    if rendered.strip():
        core = _merge_entries(core, rendered)
    if not core:
        return None, [f"no user-facing fragments or pre-release section to compile into [{version}]"]
    block = [header, "", *core, ""]
    result: list[str] = []
    for label, lines in sections:
        if label in folded:
            continue
        if existing is not None and label == version:
            result.extend(block)
        elif label == "Unreleased" and existing is None:
            result.extend(lines if not lines[-1].strip() else [*lines, ""])
            result.extend(block)
        else:
            result.extend(lines)
    if result and result[-1].strip():
        result.append("")
    final = not Version(version).is_prerelease
    new_links = _compare_links(links, folded, version, previous_final, final)
    return "\n".join([*preamble, *result, *new_links]) + "\n", []


def pending_fragment_paths(source: Path) -> list[Path]:
    directory = source / _FRAGMENT_DIR
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.glob("*.json") if path.is_file())


def require_prepared(source: Path, version: str) -> list[str]:
    problems: list[str] = []
    changelog = source / "CHANGELOG.md"
    if not changelog.exists():
        problems.append(f"{changelog} not found")
    elif section_body(changelog.read_text(encoding="utf-8"), version) is None:
        problems.append(
            f"CHANGELOG.md has no '## [{version}] - <date>' section; run `make release-prep VERSION={version}`"
        )
    pending = pending_fragment_paths(source)
    if pending:
        names = ", ".join(path.name for path in pending)
        problems.append(
            f"{len(pending)} changelog fragment(s) still pending in {_FRAGMENT_DIR}/: {names}; "
            f"run `make release-prep VERSION={version}`"
        )
    return problems


def _commit_added_fragment(source: Path, commit_hash: str) -> bool:
    fragments = _sibling_module("changelog_fragments")
    result = _run_git(source, "diff-tree", "--no-commit-id", "--name-status", "-r", "--diff-filter=A", commit_hash)
    if result.returncode != 0:
        return False
    for line in result.stdout.splitlines():
        _status, _separator, path = line.partition("\t")
        if fragments.is_fragment_path(path):
            return True
    return False


def _print_worksheet(source: Path, fragments: list) -> None:
    library = _sibling_module("changelog_fragments")
    print("  Worksheet (file -> PR -> category -> tags):")
    for fragment in fragments:
        relative = f"{_FRAGMENT_DIR}/{fragment.name}"
        number = library.pr_number_for(source, relative)
        pr = f"PR #{number}" if number is not None else "PR unknown"
        if fragment.internal is not None:
            print(f"    {relative} -> {pr} -> internal -> -")
            continue
        for entry in fragment.entries:
            tags = [f"platform:{name}" for name in entry.platforms] + [f"benchmark:{name}" for name in entry.benchmarks]
            print(f"    {relative} -> {pr} -> {entry.category} -> {', '.join(tags) or '-'}")


def _previous_release_boundary(source: Path, previous_final: str) -> str | None:
    heading = f"## [{previous_final.removeprefix('v')}] - "
    result = _run_git(source, "log", "--format=%H", f"-S{heading}", "--", "CHANGELOG.md")
    if result.returncode != 0:
        return None
    hashes = result.stdout.split()
    return hashes[-1] if hashes else None


def _print_coverage_report(source: Path, previous_final: str | None) -> None:
    print("  Coverage: feat/fix/perf commits since the previous final release that added no fragment:")
    if previous_final is None:
        print("    skipped: no previous final release tag")
        return
    boundary = _previous_release_boundary(source, previous_final)
    if boundary is None:
        print(f"    skipped: no commit on this branch added the {previous_final} changelog section")
        return
    commits = _commits_since_ref(source, boundary)
    if commits is None:
        print("    skipped: commit history unavailable")
        return
    uncovered = [
        subject
        for commit_hash, subject in commits
        if _conventional_changelog_subject(subject) and not _commit_added_fragment(source, commit_hash)
    ]
    for subject in uncovered:
        print(f"    - {subject}")
    if not uncovered:
        print("    none")


def compile_fragments(source: Path, version: str, release_date: str, dry_run: bool = False) -> bool:
    from packaging.version import InvalidVersion, Version

    library = _sibling_module("changelog_fragments")
    flow = _sibling_module("release_flow")
    print(f"\n  Compiling changelog fragments for v{version}...")
    changelog = source / "CHANGELOG.md"
    if not changelog.exists():
        print(f"  Error: {changelog} not found")
        return False
    try:
        Version(version)
    except InvalidVersion:
        print(f"  Error: {version!r} is not a valid version")
        return False
    paths = pending_fragment_paths(source)
    fragments, errors = library.collect_fragments(paths)
    if errors:
        print("  Error: invalid changelog fragment(s); nothing was written:", file=sys.stderr)
        for error in errors:
            print(f"    - {error}", file=sys.stderr)
        return False
    text = changelog.read_text(encoding="utf-8")
    if section_body(text, version) is not None and not paths:
        print(f"  [{version}] already compiled and no fragments pending; nothing to do")
        return True
    previous = flow.latest_release_tag(source, before_version=version)
    previous_final = previous.removeprefix("refs/tags/") if previous else None
    new_text, problems = compile_changelog(text, version, release_date, library.render(fragments), previous_final)
    if new_text is None:
        print("  Error: cannot compile the changelog; nothing was written:", file=sys.stderr)
        for problem in problems:
            print(f"    - {problem}", file=sys.stderr)
        return False
    if dry_run:
        print(f"  Dry run: {len(paths)} fragment(s) would be compiled into [{version}]; nothing written")
        return True
    _print_worksheet(source, fragments)
    if new_text != text:
        changelog.write_text(new_text, encoding="utf-8")
    for path in paths:
        path.unlink()
    body = section_body(new_text, version) or ""
    bullets = len([line for line in body.splitlines() if _BULLET_RE.match(line)])
    lines = len(body.splitlines())
    print(
        f"  Updated {changelog}: [{version}] has {bullets}/{MAX_CURATED_BULLETS} bullets, {lines}/{MAX_CURATED_LINES} lines"
    )
    _print_coverage_report(source, previous_final)
    return True


def _run_require_prepared(source: Path, version: str) -> int:
    problems = require_prepared(source, version)
    if problems:
        print(f"  Release {version} is not prepared:", file=sys.stderr)
        for problem in problems:
            print(f"    - {problem}", file=sys.stderr)
        return 1
    print(f"  Release {version} is prepared: [{version}] exists and no fragments are pending")
    return 0


def _run_curation_check(source: Path, version: str) -> int:
    ok, problems = check_changelog_curation(source, version)
    if ok:
        print(f"  CHANGELOG.md curation guard: OK ([{version}] section looks curated)")
        return 0
    print(f"  CHANGELOG.md section [{version}] does not look curated:")
    for problem in problems:
        print(f"    - {problem}")
    if has_changelog_section(source, version) and (
        os.environ.get("RELEASE_ALLOW_RAW_CHANGELOG", "").strip().lower() in _TRUE_VALUES
    ):
        print("  RELEASE_ALLOW_RAW_CHANGELOG is set; accepting the section as-is")
        return 0
    print()
    print(f"  Fix the [{version}] section in CHANGELOG.md, then re-run the cut:")
    print(f"    make release-cut VERSION={version}")
    print("  To accept the section deliberately: RELEASE_ALLOW_RAW_CHANGELOG=1 make release-cut ...")
    return 1


def _run_section_file(source: Path, version: str, release_date: str, section_file: Path) -> int:
    changelog = source / "CHANGELOG.md"
    if not changelog.exists():
        print(f"  Error: {changelog} not found")
        return 1
    return 0 if _write_curated_section(changelog, version, release_date, section_file) else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compile CHANGELOG.md release sections from changelog.d fragments and check release changelog state."
    )
    parser.add_argument(
        "--check-tag-claims",
        action="store_true",
        help=(
            "Check CHANGELOG.md for dated released-version sections with no matching "
            "vX.Y.Z git tag, then exit (skips entry generation; --version not required)."
        ),
    )
    parser.add_argument(
        "--check-release-accounting",
        action="store_true",
        help=(
            "Read the current published version from PyPI and verify its git tag, "
            "CHANGELOG.md section, [Unreleased] comparison, and project version."
        ),
    )
    parser.add_argument(
        "--check-curation",
        action="store_true",
        help=(
            "Check that the '## [VERSION]' section is curated (no raw commit subjects, "
            f"no duplicate headings, at most {MAX_CURATED_BULLETS} bullets and {MAX_CURATED_LINES} lines), "
            "then exit. Requires --version. Override with RELEASE_ALLOW_RAW_CHANGELOG=1."
        ),
    )
    parser.add_argument(
        "--compile-fragments",
        action="store_true",
        help=(
            "Build the dated '## [VERSION]' section from changelog.d fragments (folding any pre-release "
            "section of the same base version), update compare links, and delete the consumed fragments. "
            "Requires --version."
        ),
    )
    parser.add_argument(
        "--require-prepared",
        action="store_true",
        help="Require a '## [VERSION]' section and no pending changelog.d fragments, then exit. Requires --version.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --compile-fragments: validate and compute the result without writing anything.",
    )
    parser.add_argument(
        "--print-section",
        action="store_true",
        help="Print the curated CHANGELOG.md body for --version, then exit.",
    )
    parser.add_argument(
        "--sync-github-release-notes",
        action="store_true",
        help="Replace the existing GitHub Release notes for vVERSION with the curated section.",
    )
    parser.add_argument(
        "--version",
        default=None,
        help="Version string for the new entry (without leading 'v'), e.g. 0.3.0",
    )
    parser.add_argument(
        "--release-date",
        default=date.today().isoformat(),
        help="Release date in YYYY-MM-DD format (default: today)",
    )
    parser.add_argument(
        "--section-file",
        type=Path,
        default=None,
        help=(
            "Write the [VERSION] section from this hand-curated body (the ### groups and bullets, "
            "no version header). Replaces an existing [VERSION] section. "
            "--check-curation still checks the result."
        ),
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=Path.cwd(),
        help="Repository root (default: current directory)",
    )
    args = parser.parse_args()

    if not (args.source / ".git").exists():
        print(f"  Error: {args.source} is not a git repository", file=sys.stderr)
        return 1

    if args.check_tag_claims:
        ok, untagged = check_tag_claims(args.source)
        if not ok:
            print("  CHANGELOG.md claims released version(s) with no matching git tag:")
            for version in untagged:
                print(f"    - {version} (expected tag v{version})")
            return 1
        print("  CHANGELOG.md tag-claim guard: OK")
        return 0

    if args.check_release_accounting:
        return _run_release_accounting_check(args.source)

    if not args.version:
        parser.error("--version is required unless a check-only option is set")

    if args.print_section:
        body = _publishable_body_or_report(args.source, args.version)
        if body is None:
            return 1
        print(body)
        return 0

    if args.sync_github_release_notes:
        return _run_release_notes_sync(args.source, args.version)

    if args.require_prepared:
        return _run_require_prepared(args.source, args.version)

    if args.check_curation:
        return _run_curation_check(args.source, args.version)

    if args.compile_fragments:
        return 0 if compile_fragments(args.source, args.version, args.release_date, args.dry_run) else 1

    if args.section_file is not None:
        return _run_section_file(args.source, args.version, args.release_date, args.section_file)

    parser.error("choose --compile-fragments, --require-prepared, --section-file or a check option")
    return 2


if __name__ == "__main__":
    sys.exit(main())
