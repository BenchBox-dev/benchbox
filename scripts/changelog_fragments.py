#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
FRAGMENT_DIR = "changelog.d"
DEFAULT_MANIFEST = REPO_ROOT / ".github" / "changelog-paths.txt"
SCHEMA_VERSION = 1
MAX_ENTRIES = 5
MAX_DETAILS = 8
TEXT_BOUNDS = (20, 800)
INTERNAL_BOUNDS = (10, 300)
LEAD_MAX = 120
DETAIL_MAX = 400

CATEGORIES = ("before-you-upgrade", "added", "changed", "fixed", "documentation")
HEADINGS = {
    "before-you-upgrade": "Before you upgrade",
    "added": "Added",
    "changed": "Changed",
    "fixed": "Fixed",
    "documentation": "Documentation",
}
ENTRY_KEYS = frozenset({"category", "breaking", "lead", "text", "details", "platforms", "benchmarks"})
TOP_KEYS = frozenset({"schema", "entries", "internal"})

FILE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,99}\.json$")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
PR_SUFFIX_RE = re.compile(r"\(#\d+\)\s*$")
SQUASH_PR_RE = re.compile(r"\(#(\d+)\)\s*$")
UNRELEASED_HEADER = "## [Unreleased]"
TITLE_RE = re.compile(r"^(?P<type>[a-z]+)(?:\((?P<scope>[^()\r\n]*)\))?(?P<bang>!)?: \S.*")
FRAGMENT_PATH_RE = re.compile(r"^changelog\.d/[^/]+\.json$")

EXEMPT_TYPES = frozenset({"build", "chore", "ci", "docs", "refactor", "style", "test"})
REQUIRED_TYPES = frozenset({"feat", "fix", "perf"})
MANIFEST_KINDS = frozenset({"file", "prefix", "except"})

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2


@dataclass(frozen=True)
class Entry:
    category: str
    text: str
    breaking: bool = False
    lead: str | None = None
    details: tuple[str, ...] = ()
    platforms: tuple[str, ...] = ()
    benchmarks: tuple[str, ...] = ()


@dataclass(frozen=True)
class Fragment:
    name: str
    entries: tuple[Entry, ...] = ()
    internal: str | None = None


@dataclass(frozen=True)
class Decision:
    required: bool
    satisfied: bool
    reason: str
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return self.satisfied or not self.required


def _bounded_text(value: Any, label: str, bounds: tuple[int, int]) -> list[str]:
    if not isinstance(value, str):
        return [f"{label}: must be a string"]
    low, high = bounds
    errors = []
    if not low <= len(value) <= high:
        errors.append(f"{label}: length {len(value)} outside {low}-{high} characters")
    if "\n" in value or "\r" in value:
        errors.append(f"{label}: must be a single line")
    return errors


def _id_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list):
        return [f"{label}: must be a list of ids"]
    errors = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not ID_RE.fullmatch(item):
            errors.append(f"{label}[{index}]: {item!r} is not a lowercase id")
    return errors


def _entry_errors(raw: Any, label: str) -> list[str]:
    if not isinstance(raw, dict):
        return [f"{label}: must be an object"]
    errors = [f"{label}: unknown key {key!r}" for key in sorted(set(raw) - ENTRY_KEYS, key=str)]
    category = raw.get("category")
    if category not in CATEGORIES:
        errors.append(f"{label}.category: {category!r} is not one of {', '.join(CATEGORIES)}")
    breaking = raw.get("breaking", False)
    if not isinstance(breaking, bool):
        errors.append(f"{label}.breaking: must be true or false")
    elif breaking and category != "before-you-upgrade":
        errors.append(f"{label}.breaking: a breaking entry must use category 'before-you-upgrade'")
    lead = raw.get("lead")
    if lead is not None:
        errors.extend(_bounded_text(lead, f"{label}.lead", (1, LEAD_MAX)))
        if isinstance(lead, str) and lead.strip() != lead:
            errors.append(f"{label}.lead: must not have surrounding whitespace")
    if breaking is True and not lead:
        errors.append(f"{label}.lead: a breaking entry requires a lead")
    if "text" not in raw:
        errors.append(f"{label}.text: required")
    else:
        text = raw["text"]
        errors.extend(_bounded_text(text, f"{label}.text", TEXT_BOUNDS))
        if isinstance(text, str):
            if text.startswith("- "):
                errors.append(f"{label}.text: must not start with '- '")
            if PR_SUFFIX_RE.search(text):
                errors.append(f"{label}.text: must not end with a (#N) pull request reference")
    details = raw.get("details", [])
    if not isinstance(details, list):
        errors.append(f"{label}.details: must be a list of strings")
    else:
        if len(details) > MAX_DETAILS:
            errors.append(f"{label}.details: {len(details)} items exceeds the maximum of {MAX_DETAILS}")
        for index, item in enumerate(details):
            errors.extend(_bounded_text(item, f"{label}.details[{index}]", (1, DETAIL_MAX)))
    for key in ("platforms", "benchmarks"):
        if key in raw:
            errors.extend(_id_list(raw[key], f"{label}.{key}"))
    return errors


def _document_errors(data: Any) -> list[str]:
    if not isinstance(data, dict):
        return ["top level must be an object"]
    errors = [f"unknown key {key!r}" for key in sorted(set(data) - TOP_KEYS, key=str)]
    schema = data.get("schema")
    if isinstance(schema, bool) or schema != SCHEMA_VERSION:
        errors.append(f"schema: must be {SCHEMA_VERSION}, got {schema!r}")
    has_entries = "entries" in data
    has_internal = "internal" in data
    if has_entries == has_internal:
        errors.append("exactly one of 'entries' or 'internal' is required")
    elif has_internal:
        errors.extend(_bounded_text(data["internal"], "internal", INTERNAL_BOUNDS))
    else:
        entries = data["entries"]
        if not isinstance(entries, list):
            errors.append("entries: must be a list")
        else:
            if not 1 <= len(entries) <= MAX_ENTRIES:
                errors.append(f"entries: {len(entries)} items outside 1-{MAX_ENTRIES}")
            for index, raw in enumerate(entries):
                errors.extend(_entry_errors(raw, f"entries[{index}]"))
    return errors


def _build_fragment(name: str, data: dict[str, Any]) -> Fragment:
    if "internal" in data:
        return Fragment(name=name, internal=data["internal"])
    entries = tuple(
        Entry(
            category=raw["category"],
            text=raw["text"],
            breaking=raw.get("breaking", False),
            lead=raw.get("lead"),
            details=tuple(raw.get("details", ())),
            platforms=tuple(raw.get("platforms", ())),
            benchmarks=tuple(raw.get("benchmarks", ())),
        )
        for raw in data["entries"]
    )
    return Fragment(name=name, entries=entries)


def parse_fragment(path: Path, source: str | None = None) -> tuple[Fragment | None, list[str]]:
    label = path.as_posix()
    name = path.name
    errors: list[str] = []
    if not FILE_NAME_RE.fullmatch(name):
        errors.append(f"{label}: file name must match {FILE_NAME_RE.pattern}")
    try:
        text = path.read_text(encoding="utf-8") if source is None else source
    except (OSError, UnicodeDecodeError) as exc:
        return None, [*errors, f"{label}: cannot read fragment: {exc}"]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, [*errors, f"{label}: invalid JSON: {exc}"]
    errors.extend(f"{label}: {message}" for message in _document_errors(data))
    if errors:
        return None, errors
    return _build_fragment(name, data), []


def collect_fragments(paths: Iterable[Path]) -> tuple[list[Fragment], list[str]]:
    fragments: list[Fragment] = []
    errors: list[str] = []
    for path in sorted(paths):
        fragment, problems = parse_fragment(path)
        errors.extend(problems)
        if fragment is not None:
            fragments.append(fragment)
    return fragments, errors


def fragment_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.iterdir() if path.is_file() and not path.name.startswith("."))


def _sort_key(entry: Entry) -> tuple[str, str, str]:
    return (",".join(sorted(entry.platforms)), ",".join(sorted(entry.benchmarks)), entry.text)


def _render_entry(entry: Entry) -> list[str]:
    if entry.breaking:
        lines = [f"- **BREAKING: {entry.lead}.** {entry.text}"]
    elif entry.lead:
        lines = [f"- **{entry.lead}.** {entry.text}"]
    else:
        lines = [f"- {entry.text}"]
    lines.extend(f"  - {detail}" for detail in entry.details)
    return lines


def render(fragments: Iterable[Fragment]) -> str:
    by_category: dict[str, list[Entry]] = {category: [] for category in CATEGORIES}
    for fragment in fragments:
        for entry in fragment.entries:
            by_category[entry.category].append(entry)
    blocks: list[str] = []
    for category in CATEGORIES:
        entries = sorted(by_category[category], key=_sort_key)
        if not entries:
            continue
        lines = [f"### {HEADINGS[category]}", ""]
        for entry in entries:
            lines.extend(_render_entry(entry))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n" if blocks else ""


def unreleased_violations(changelog_text: str) -> list[str]:
    lines = changelog_text.splitlines()
    try:
        start = next(index for index, line in enumerate(lines) if line.strip() == UNRELEASED_HEADER)
    except StopIteration:
        return [f"CHANGELOG.md has no '{UNRELEASED_HEADER}' heading"]
    end = next((index for index in range(start + 1, len(lines)) if lines[index].startswith("## [")), len(lines))
    stray = [index for index in range(start + 1, end) if lines[index].strip()]
    if not stray:
        return []
    first = stray[0] + 1
    return [
        f"CHANGELOG.md line {first}: the [Unreleased] section must stay empty ({len(stray)} non-blank lines); "
        f"add a fragment under {FRAGMENT_DIR}/ instead"
    ]


def pr_number_for(repo: Path, path: str, ref: str = "HEAD") -> int | None:
    result = subprocess.run(
        ["git", "log", "--diff-filter=A", "--format=%s", ref, "--", path],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    subjects = [line for line in result.stdout.splitlines() if line.strip()]
    if not subjects:
        return None
    match = SQUASH_PR_RE.search(subjects[0])
    return int(match.group(1)) if match else None


def normalize_path(path: str) -> str:
    return path.strip().replace("\\", "/")


def load_rules(path: Path = DEFAULT_MANIFEST) -> tuple[Any, ...]:
    root = str(REPO_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    from _project.scripts.soundness_paths import _load_rules

    return _load_rules(path, MANIFEST_KINDS)


def matches_manifest(path: str, rules: Sequence[Any]) -> bool:
    normalized = normalize_path(path)
    if not normalized:
        return False
    included = any(
        (rule.kind == "prefix" and normalized.startswith(rule.pattern))
        or (rule.kind == "file" and normalized == rule.pattern)
        for rule in rules
    )
    excluded = any(rule.kind == "except" and normalized == rule.pattern for rule in rules)
    return included and not excluded


def is_fragment_path(path: str) -> bool:
    return FRAGMENT_PATH_RE.fullmatch(normalize_path(path)) is not None


def decide_required(
    title: str,
    changed_paths: Iterable[str],
    rules: Sequence[Any],
    release_identity_changed: bool = False,
    user_entry_fragments: Iterable[str] = (),
) -> Decision:
    paths = [normalize_path(path) for path in changed_paths]
    user_entries = list(user_entry_fragments)
    satisfied = any(is_fragment_path(path) for path in paths)
    match = TITLE_RE.fullmatch(title)
    pr_type = match.group("type") if match else None
    scope = match.group("scope") if match else None
    breaking_title = bool(match and match.group("bang"))

    if title.startswith('Revert "'):
        return Decision(False, satisfied, "revert pull request")
    if release_identity_changed:
        return Decision(False, satisfied, "release identity changed")
    if not any(matches_manifest(path, rules) for path in paths):
        return Decision(False, satisfied, "no changed path is shipped in the package")

    internal_type = pr_type in EXEMPT_TYPES and not breaking_title and not (pr_type == "chore" and scope == "deps")
    if internal_type:
        warnings = ()
        if user_entries:
            warnings = (f"a '{pr_type}' pull request adds a user-facing changelog entry; use a user-facing title type",)
        return Decision(False, satisfied, f"title type '{pr_type}' needs no entry", warnings)
    if match is None:
        reason = "title is not a conventional commit title"
    elif breaking_title:
        reason = "breaking title"
    elif pr_type == "chore":
        reason = "dependency update"
    elif pr_type in REQUIRED_TYPES:
        reason = f"title type '{pr_type}'"
    else:
        reason = f"unrecognised title type '{pr_type}'"
    return Decision(True, satisfied, reason)


def current_branch(repo: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo, capture_output=True, text=True, check=False
    )
    branch = result.stdout.strip()
    if result.returncode != 0 or not branch or branch == "HEAD":
        raise ValueError("cannot derive a fragment name from a detached HEAD; pass --name")
    return branch


def fragment_name(branch: str) -> str:
    name = f"{branch.replace('/', '-').lower()}.json"
    if not FILE_NAME_RE.fullmatch(name):
        raise ValueError(f"branch {branch!r} does not produce a valid fragment name ({name!r}); pass --name")
    return name


def _split_ids(value: str | None) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()] if value else []


def _truthy(value: str | None) -> bool:
    return value is not None and value.strip().lower() in {"1", "true", "yes", "on"}


def _entry_payload(args: argparse.Namespace) -> dict[str, Any]:
    entry: dict[str, Any] = {"category": args.category, "breaking": args.breaking}
    if args.lead:
        entry["lead"] = args.lead
    entry["text"] = args.text
    if args.detail:
        entry["details"] = list(args.detail)
    platforms = _split_ids(args.platforms)
    if platforms:
        entry["platforms"] = platforms
    benchmarks = _split_ids(args.benchmarks)
    if benchmarks:
        entry["benchmarks"] = benchmarks
    return entry


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def command_add(args: argparse.Namespace) -> int:
    root = Path(args.root)
    try:
        name = args.name or fragment_name(current_branch(root))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    path = root / FRAGMENT_DIR / name
    if args.internal:
        if args.text or args.category:
            print("error: --internal cannot be combined with --category or --text", file=sys.stderr)
            return EXIT_USAGE
        data: dict[str, Any] = {"schema": SCHEMA_VERSION, "internal": args.internal}
        if path.exists():
            print(f"error: {path.as_posix()} already exists; remove it to mark the change internal", file=sys.stderr)
            return EXIT_FAILED
    else:
        if not args.category or not args.text:
            print("error: a user entry needs --category and --text (or --internal)", file=sys.stderr)
            return EXIT_USAGE
        entry = _entry_payload(args)
        data = {"schema": SCHEMA_VERSION, "entries": [entry]}
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                print(f"error: {path.as_posix()}: cannot extend unreadable fragment: {exc}", file=sys.stderr)
                return EXIT_FAILED
            if not isinstance(existing, dict) or not isinstance(existing.get("entries"), list):
                print(f"error: {path.as_posix()}: existing fragment has no entries list to extend", file=sys.stderr)
                return EXIT_FAILED
            data = {**existing, "entries": [*existing["entries"], entry]}
    problems = _document_errors(data)
    if problems:
        print("\n".join(f"error: {path.as_posix()}: {message}" for message in problems), file=sys.stderr)
        return EXIT_FAILED
    _write_json(path, data)
    print(f"wrote {path.as_posix()}")
    return EXIT_OK


def _load_all(args: argparse.Namespace) -> tuple[list[Fragment], list[Path], list[str]]:
    paths = [Path(item) for item in args.paths] if args.paths else fragment_files(Path(args.root) / FRAGMENT_DIR)
    fragments, errors = collect_fragments(paths)
    return fragments, paths, errors


def command_check(args: argparse.Namespace) -> int:
    _, paths, errors = _load_all(args)
    if args.require_empty_unreleased:
        changelog = Path(args.changelog)
        try:
            errors.extend(unreleased_violations(changelog.read_text(encoding="utf-8")))
        except OSError as exc:
            errors.append(f"{changelog.as_posix()}: cannot read changelog: {exc}")
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return EXIT_FAILED
    print(f"changelog fragments OK: {len(paths)} checked")
    return EXIT_OK


def command_preview(args: argparse.Namespace) -> int:
    fragments, paths, errors = _load_all(args)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return EXIT_FAILED
    sys.stdout.write(render(fragments))
    worksheet = ["Curation worksheet (pull request derived from the commit that added each fragment):"]
    repo = Path(args.root)
    for path in sorted(paths):
        try:
            relative = path.resolve().relative_to(repo.resolve()).as_posix()
        except ValueError:
            relative = path.as_posix()
        number = pr_number_for(repo, relative, args.ref)
        worksheet.append(f"  {relative}  {'#' + str(number) if number else '(pull request unknown)'}")
    if len(worksheet) > 1:
        print("\n".join(worksheet), file=sys.stderr)
    return EXIT_OK


def _read_lines(source: str) -> list[str]:
    text = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
    return [line for line in text.splitlines() if line.strip()]


def command_required(args: argparse.Namespace) -> int:
    try:
        rules = load_rules(Path(args.manifest))
        changed = list(args.changed_file)
        for source in args.changed_files_from:
            changed.extend(_read_lines(source))
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    root = Path(args.root)
    user_entries = []
    for path in changed:
        if not is_fragment_path(path):
            continue
        candidate = root / normalize_path(path)
        if not candidate.is_file():
            continue
        fragment, _ = parse_fragment(candidate)
        if fragment is not None and fragment.entries:
            user_entries.append(path)
    decision = decide_required(
        args.title,
        changed,
        rules,
        release_identity_changed=args.release_identity_changed,
        user_entry_fragments=user_entries,
    )
    for warning in decision.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    state = "required" if decision.required else "not required"
    print(f"changelog entry {state}: {decision.reason}")
    if decision.ok:
        return EXIT_OK
    print(
        f"error: add a fragment under {FRAGMENT_DIR}/ (make changelog-entry) or mark the change internal",
        file=sys.stderr,
    )
    return EXIT_FAILED


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate, create, render and gate changelog fragments.")
    parser.add_argument("--root", default=str(REPO_ROOT), help="Repository root.")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="Validate fragments.")
    check.add_argument("paths", nargs="*", help="Fragment files (default: every file in changelog.d).")
    check.add_argument("--require-empty-unreleased", action="store_true")
    check.add_argument("--changelog", default=str(REPO_ROOT / "CHANGELOG.md"))
    check.set_defaults(handler=command_check)

    add = sub.add_parser("add", help="Create or extend this branch's fragment.")
    env = os.environ.get
    add.add_argument("--name", default=env("CHANGELOG_FRAGMENT"), help="Fragment file name (default: branch slug).")
    add.add_argument("--category", default=env("CHANGELOG_CATEGORY"), choices=CATEGORIES)
    add.add_argument("--text", default=env("CHANGELOG_TEXT"))
    add.add_argument("--lead", default=env("CHANGELOG_LEAD"))
    add.add_argument("--breaking", action="store_true", default=_truthy(env("CHANGELOG_BREAKING")))
    add.add_argument("--detail", action="append", default=[])
    add.add_argument("--platforms", default=env("CHANGELOG_PLATFORMS"), help="Comma-separated platform ids.")
    add.add_argument("--benchmarks", default=env("CHANGELOG_BENCHMARKS"), help="Comma-separated benchmark ids.")
    add.add_argument("--internal", default=env("CHANGELOG_INTERNAL"), help="Reason the change has no user effect.")
    add.set_defaults(handler=command_add)

    preview = sub.add_parser("preview", help="Render fragments as Markdown.")
    preview.add_argument("paths", nargs="*")
    preview.add_argument("--ref", default="HEAD")
    preview.set_defaults(handler=command_preview)

    required = sub.add_parser("required", help="Decide whether a pull request needs a fragment.")
    required.add_argument("--title", required=True)
    required.add_argument("--changed-file", action="append", default=[])
    required.add_argument("--changed-files-from", action="append", default=[], metavar="FILE", help="'-' for stdin.")
    required.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    required.add_argument("--release-identity-changed", action="store_true")
    required.set_defaults(handler=command_required)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
