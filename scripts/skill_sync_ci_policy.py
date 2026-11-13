#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

CLI_DESCRIPTION = (
    "Fail-closed trust policy for BenchBox skill-sync CI routing.\n"
    "\n"
    "The tool pin is a trust anchor owned by BenchBox maintainers. It is\n"
    "intentionally independent of every revision selected by ``skill-sync.conf``:\n"
    "``TOOL_REF`` names the upstream commit the vendored wrapper was copied from\n"
    "and ``TOOL_SHA256`` pins the exact vendored bytes, so a tampered\n"
    "``tools/skill-sync`` fails validation even when the config is untouched.\n"
    "Advance either only with a clean preview/apply/check/verify proof and full CI.\n"
    "\n"
    "The wrapper never fetches: ``skill-sync.conf`` names local source checkouts,\n"
    "so the config carries no origin URLs. URL identity is still checked, but on\n"
    "the committed per-target receipt instead: ``skill-sync`` records each\n"
    "source's ``remote.origin.url`` there at apply time, and validation requires\n"
    "exactly the approved set. Advancing a skill revision only with a reviewed\n"
    "receipt diff is what keeps a forked checkout from riding the narrow lane.\n"
)

TOOL_REPOSITORY = "https://github.com/joeharris76/skill-sync.git"
TOOL_REF = "25e1e47693d8b0b2aba91d3ad3dbc0c14b8d3c4c"
TOOL_SHA256 = "01f2b383318c448e3ed94aed4781432ac69dbd33f1556dae9792e65f9883eb34"
TOOL_PATH = Path("tools/skill-sync")
MANIFEST_PATH = Path("skill-sync.conf")
RECEIPT_PATH = Path(".claude/skills/skill-sync.receipt")
EVIDENCE_PATH = Path("_project/analysis/batch-rollout-evidence.json")
TRACKED_TODO_ROOT = Path(".claude/skills/todo")
PINNED_SKILL = "todo"
_FULL_SHA_RE = re.compile(r"[0-9a-f]{40}")

APPROVED_SOURCE_URLS = frozenset(
    {
        "https://github.com/joeharris76/skill-sync-skills.git",
        TOOL_REPOSITORY,
    }
)

EXPECTED_TARGETS = [".claude/skills", ".agents/skills"]
EXPECTED_GROUPS = [
    {"skills": ["benchbox"]},
    {
        "skills": [
            "blog",
            "code",
            "test",
            "docs",
            "tidy-perms",
            "todo",
            "shared-change-framework",
            "shared-investigation-framework",
            "shared-review-protocol",
        ],
    },
    {"skills": ["shared-agent-execution"]},
    {"skills": ["skill-sync"]},
]
EXPECTED_SKILLS = frozenset(skill for group in EXPECTED_GROUPS for skill in group["skills"])

_REF_LINE_RE = re.compile(r"^(rev *= *)([0-9a-fA-F]+)( *)$")


class PolicyError(ValueError):
    pass


@dataclass(frozen=True)
class ManifestDecision:
    narrow_eligible: bool
    reason: str
    base_ref: str | None = None


def _strip_comment(line: str) -> str:
    return line.split("#", 1)[0].strip()


def parse_conf_groups(text: str) -> tuple[list[str], list[dict[str, object]]]:
    targets: list[str] = []
    groups: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = _strip_comment(raw)
        if not line:
            continue
        if "=" not in line:
            raise PolicyError(f'line {lineno}: expected "key = value"')
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if key == "target":
            if current is not None:
                raise PolicyError(f'line {lineno}: every "target" line must come before the first "source"')
            if not value or value.startswith("/") or ".." in value.split("/"):
                raise PolicyError(f"line {lineno}: bad target path: {value!r}")
            targets.append(value)
        elif key == "source":
            if not value:
                raise PolicyError(f"line {lineno}: empty source")
            current = {"source": value, "rev": "", "dir": "skills", "skills": []}
            groups.append(current)
        elif key in ("rev", "dir", "skill"):
            if current is None:
                raise PolicyError(f'line {lineno}: {key!r} must follow a "source" line')
            if key == "skill":
                if not value:
                    raise PolicyError(f"line {lineno}: empty skill")
                skills = current["skills"]
                assert isinstance(skills, list)
                skills.append(value)
            else:
                if not value:
                    raise PolicyError(f"line {lineno}: empty {key}")
                current[key] = value
        else:
            raise PolicyError(f"line {lineno}: unknown key {key!r}")
    return targets, groups


def validate_conf_text(text: str) -> None:
    targets, groups = parse_conf_groups(text)
    if targets != EXPECTED_TARGETS:
        raise PolicyError(f"targets must be exactly {EXPECTED_TARGETS}; got {targets}")
    if len(groups) != len(EXPECTED_GROUPS):
        raise PolicyError(f"expected {len(EXPECTED_GROUPS)} source groups, got {len(groups)}")
    seen: set[str] = set()
    for index, (group, expected) in enumerate(zip(groups, EXPECTED_GROUPS)):
        rev = group["rev"]
        assert isinstance(rev, str)
        if not _FULL_SHA_RE.fullmatch(rev):
            raise PolicyError(f"source group {index + 1} rev must be a lowercase 40-character commit SHA")
        if group["dir"] != "skills":
            raise PolicyError(f'source group {index + 1} dir must be "skills"')
        skills = group["skills"]
        assert isinstance(skills, list)
        if [str(skill) for skill in skills] != expected["skills"]:
            raise PolicyError(f"source group {index + 1} skills must be exactly {expected['skills']}")
        for skill in skills:
            name = str(skill)
            if name in seen:
                raise PolicyError(f"skill {name!r} is listed more than once")
            seen.add(name)
    if seen != EXPECTED_SKILLS:
        raise PolicyError(f"skill selection must be exactly {sorted(EXPECTED_SKILLS)}")


def validate_receipt_text(text: str) -> None:
    urls: list[str] = []
    revs: list[str] = []
    skills: list[str] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = _strip_comment(raw)
        if not line:
            continue
        if "=" not in line:
            raise PolicyError(f'receipt line {lineno}: expected "key = value"')
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if key == "source":
            urls.append(value)
        elif key == "rev":
            revs.append(value)
        elif key == "skill":
            skills.append(value)
        elif key in ("dir", "file"):
            continue
        else:
            raise PolicyError(f"receipt line {lineno}: unknown key {key!r}")
    if len(urls) != len(EXPECTED_GROUPS):
        raise PolicyError(f"receipt must record {len(EXPECTED_GROUPS)} sources, got {len(urls)}")
    unknown = [url for url in urls if url not in APPROVED_SOURCE_URLS]
    if unknown:
        raise PolicyError(f"receipt source not approved: {unknown!r}")
    for rev in revs:
        if not _FULL_SHA_RE.fullmatch(rev):
            raise PolicyError(f"receipt rev must be a lowercase 40-character commit SHA: {rev!r}")
    if set(skills) != EXPECTED_SKILLS:
        raise PolicyError(f"receipt skills must be exactly {sorted(EXPECTED_SKILLS)}; got {sorted(set(skills))}")


def validate_tool_bytes(data: bytes) -> None:
    digest = hashlib.sha256(data).hexdigest()
    if digest != TOOL_SHA256:
        raise PolicyError(
            "tools/skill-sync does not match the pinned revision "
            f"{TOOL_REF} (sha256 {digest}); update it only by vendoring "
            "bin/skill-sync from the product repository and recording the "
            "new pin here with a full review"
        )


def validate_manifest_text(text: str) -> None:
    validate_conf_text(text)


def validate_evidence_binding(manifest_text: str, evidence_text: str, tracked_root: Path) -> None:
    name = EVIDENCE_PATH.as_posix()
    _, groups = parse_conf_groups(manifest_text)
    pins = [str(group["rev"]) for group in groups if PINNED_SKILL in list(group["skills"])]
    if len(pins) != 1:
        raise PolicyError(f"{name}: expected exactly one conf group listing skill = {PINNED_SKILL}, found {len(pins)}")
    pin = pins[0]
    try:
        evidence = json.loads(evidence_text)
        after = evidence["benchbox_pin"]["after"]
        merge_revision = evidence["catalog"]["merge_revision"]
        tracked = evidence["mirrors"]["tracked_target"]
        receipt_revision = tracked["receipt_revision"]
        files = tracked["files"]
        if not isinstance(files, dict):
            raise TypeError("files is not an object")
    except (ValueError, KeyError, TypeError) as exc:
        raise PolicyError(f"{name}: cannot read the catalog pin fields: {exc!r}") from exc
    for field, value in (
        ("benchbox_pin.after", after),
        ("catalog.merge_revision", merge_revision),
        ("mirrors.tracked_target.receipt_revision", receipt_revision),
    ):
        if value != pin:
            raise PolicyError(
                f"{name}: {field} is {value!r} but the skill-sync.conf group listing skill = {PINNED_SKILL} pins {pin}"
            )
    mirrored = {path.relative_to(tracked_root).as_posix() for path in tracked_root.rglob("*") if path.is_file()}
    if not mirrored:
        raise PolicyError(f"{name}: the tracked mirror {tracked_root.as_posix()} has no files to bind")
    if set(files) != mirrored:
        unlisted = sorted(mirrored - set(files))
        absent = sorted(set(files) - mirrored)
        raise PolicyError(
            f"{name}: mirrors.tracked_target.files must list every file under {tracked_root.as_posix()} and no other; "
            f"not listed: {unlisted}; listed but not in the mirror: {absent}"
        )
    for relative, expected in sorted(files.items()):
        field = f"mirrors.tracked_target.files[{relative!r}]"
        path = tracked_root / relative
        try:
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            raise PolicyError(f"{name}: {field} names {path.as_posix()} which cannot be read: {exc}") from exc
        if actual != expected:
            raise PolicyError(f"{name}: {field} is {expected} but {path.as_posix()} hashes to {actual}")


def normalize_ref_only_manifest(text: str) -> str:
    validate_conf_text(text)
    normalized: list[str] = []
    replacements = 0
    for line in text.splitlines():
        if _REF_LINE_RE.match(line):
            normalized.append("rev = <immutable-rev>")
            replacements += 1
        else:
            normalized.append(line)
    if replacements != len(EXPECTED_GROUPS):
        raise PolicyError(f"expected {len(EXPECTED_GROUPS)} source revs, normalized {replacements}")
    return "\n".join(normalized) + "\n"


def compare_manifest_texts(base_text: str, head_text: str, *, base_ref: str | None = None) -> ManifestDecision:
    try:
        base_normalized = normalize_ref_only_manifest(base_text)
        head_normalized = normalize_ref_only_manifest(head_text)
    except PolicyError as exc:
        return ManifestDecision(False, f"manifest_policy_error:{exc}", base_ref)
    if base_normalized != head_normalized:
        return ManifestDecision(False, "manifest_structural_change", base_ref)
    return ManifestDecision(True, "approved_ref_only_change", base_ref)


def _git_show(base_ref: str, path: Path) -> str:
    if not _FULL_SHA_RE.fullmatch(base_ref):
        raise PolicyError("CI manifest comparison requires an immutable 40-character base SHA")
    result = subprocess.run(
        ["git", "show", f"{base_ref}:{path.as_posix()}"],
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise PolicyError(f"cannot read base config at {base_ref}: {result.stderr.strip()}")
    return result.stdout


def compare_repository_manifest(base_ref: str, *, manifest: Path = MANIFEST_PATH) -> ManifestDecision:
    try:
        base_text = _git_show(base_ref, manifest)
        head_text = manifest.read_text(encoding="utf-8")
    except (OSError, PolicyError) as exc:
        return ManifestDecision(False, f"manifest_evidence_error:{exc}", base_ref)
    return compare_manifest_texts(base_text, head_text, base_ref=base_ref)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    validate.add_argument("--receipt", type=Path, default=RECEIPT_PATH)
    validate.add_argument("--tool", type=Path, default=TOOL_PATH)
    validate.add_argument("--evidence", type=Path, default=None)
    validate.add_argument("--tracked-todo", type=Path, default=None)
    compare = subparsers.add_parser("compare")
    compare.add_argument("--base-sha", required=True)
    compare.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    args = parser.parse_args(argv)

    if args.command == "validate":
        try:
            manifest_text = args.manifest.read_text(encoding="utf-8")
            validate_conf_text(manifest_text)
            validate_receipt_text(args.receipt.read_text(encoding="utf-8"))
            validate_tool_bytes(args.tool.read_bytes())
            repo_root = args.manifest.parent
            validate_evidence_binding(
                manifest_text,
                (args.evidence or repo_root / EVIDENCE_PATH).read_text(encoding="utf-8"),
                args.tracked_todo or repo_root / TRACKED_TODO_ROOT,
            )
        except (OSError, PolicyError) as exc:
            print(json.dumps({"valid": False, "reason": str(exc)}, sort_keys=True))
            return 1
        print(json.dumps({"valid": True, "tool_ref": TOOL_REF}, sort_keys=True))
        return 0

    decision = compare_repository_manifest(args.base_sha, manifest=args.manifest)
    print(json.dumps(asdict(decision), sort_keys=True))
    return 0 if decision.narrow_eligible else 1


if __name__ == "__main__":
    raise SystemExit(main())
