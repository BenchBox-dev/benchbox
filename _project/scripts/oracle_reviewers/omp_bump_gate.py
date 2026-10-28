from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

import yaml

from . import omp_pin

POLICY_PATH = ".github/oracle-reviewers.yml"
FIXTURE_PREFIX = "tests/fixtures/oracle_reviewers/omp_candidate/"
FIXTURE_SUFFIX = ".jsonl"
ATTESTERS = frozenset({"joeharris76"})
EVIDENCE_PLATFORM = "darwin-arm64"
EVIDENCE_LINE = re.compile(
    r"(?m)^Omp candidate check: version=(?P<version>\S+) platform=(?P<platform>\S+) "
    r"sha256=(?P<sha256>[0-9a-f]{64}) pin=(?P<pin>[0-9a-f]{64}) head=(?P<head>[0-9a-f]{40}) result=releasable$"
)


class BumpEvidenceError(Exception):
    pass


def pin_section(text: str) -> Mapping[str, Any]:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise BumpEvidenceError("the reviewer policy is not valid YAML") from exc
    section = data.get("omp") if isinstance(data, dict) else None
    return section if isinstance(section, dict) else {}


def without_pin(text: str) -> object:
    data = yaml.safe_load(text)
    return {key: value for key, value in data.items() if key != "omp"} if isinstance(data, dict) else data


def owner_evidence(comments: Sequence[Mapping[str, Any]], version: str, sha256: str, pin: str, head: str) -> bool:
    for comment in comments:
        if comment.get("login") not in ATTESTERS or comment.get("user_type") != "User":
            continue
        if comment.get("updated_at") not in (None, comment.get("created_at")):
            continue
        for match in EVIDENCE_LINE.finditer(str(comment.get("body") or "").replace("\r\n", "\n")):
            if (match["version"], match["platform"], match["sha256"], match["pin"], match["head"]) == (
                version,
                EVIDENCE_PLATFORM,
                sha256,
                pin,
                head,
            ):
                return True
    return False


def refusals(
    files: Sequence[str],
    base_text: str,
    head_text: str,
    comments: Sequence[Mapping[str, Any]],
    head_sha: str,
    complete: bool = True,
) -> list[str]:
    if POLICY_PATH not in files:
        return []
    base_pin, head_pin = pin_section(base_text), pin_section(head_text)
    if base_pin == head_pin:
        return []
    reasons = []
    if not complete:
        reasons.append("the pull request's file list is incomplete, so the other changed files cannot be checked")
    extra = [
        name
        for name in files
        if name != POLICY_PATH and not (name.startswith(FIXTURE_PREFIX) and name.endswith(FIXTURE_SUFFIX))
    ]
    if extra or without_pin(base_text) != without_pin(head_text):
        reasons.append(
            "an omp pin bump changes only the omp section of the reviewer policy and the captured fixtures; "
            f"this PR also changes {', '.join(extra) if extra else 'other reviewer policy settings'}"
        )
    version = head_pin.get("version")
    build = (head_pin.get("builds") or {}).get(EVIDENCE_PLATFORM)
    digest = build.get("sha256") if isinstance(build, dict) else None
    if not isinstance(version, str) or not isinstance(digest, str):
        reasons.append(f"the bumped pin names no {EVIDENCE_PLATFORM} build, so the owner's evidence cannot match it")
        return reasons
    try:
        pin_digest = omp_pin.pin_digest(omp_pin.parse_pin({"omp": head_pin}))
    except omp_pin.PinError as exc:
        reasons.append(f"the bumped omp pin is not a valid pin: {exc}")
        return reasons
    if not owner_evidence(comments, version, digest, pin_digest, head_sha):
        reasons.append(
            f"omp pin bump to {version} has no owner evidence: run `make omp-candidate-check VERSION={version}` on "
            f"the owner's {EVIDENCE_PLATFORM} Mac and post its `Omp candidate check:` line as an unedited comment "
            f"from the owner naming version {version}, the pinned {EVIDENCE_PLATFORM} sha256, the digest of the whole omp pin, and the pull request's current head {head_sha[:9]}"
        )
    return reasons
