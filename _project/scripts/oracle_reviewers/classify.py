from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .jev import JevSession
from .policy import SIZE_BUCKETS

CONFIDENCE_BAR = 0.60
MAX_SCORED_FILES = 40
GATE_BUCKET_FLOOR = "large"

SIZE_QUESTION = {
    "type": "choice",
    "instructions": (
        "Classify the size of this pull request. File count alone never pushes a change up a bucket: judge by "
        "component spread and behavioral novelty. When a change sits between two buckets, choose the smaller one; "
        "over-classification wastes expensive frontier models."
    ),
    "criteria": {
        "trivial": (
            "Tiny changes of little importance, e.g. spelling correction. Also: dependency/lockfile bumps, "
            "generated-file refreshes, doc-only or comment-only edits, and changes with no behavior change even "
            "across several files."
        ),
        "small": (
            "Simple changes with slightly more surface area, e.g. adding a new method param and calling it. "
            "Bounded tweaks, new tests for existing behavior, tiny single-purpose scripts."
        ),
        "medium": (
            "Adding or removing code in a single module, e.g. new feature no refactoring. Confined to one "
            "component or area: one workflow, one tool, one adapter, one doc tree. Multiple files within one "
            "area are still medium."
        ),
        "large": (
            "Adding or removing code over multiple modules or a large feature. Spans two or more "
            "components/areas, a single change with broad blast radius, or deletion of live machinery."
        ),
        "huge": (
            "Deep refactoring or multi-surface feature addition. Restructures existing code across areas, or "
            "adds a subsystem spanning surfaces such as backend plus UI or CLI."
        ),
        "max": "New product surfaces, architecture changes, and deep feature redesigns.",
    },
}
CAPABILITY_QUESTION = {
    "type": "choice",
    "instructions": "Which review capability does this change need? Answer routine for bounded, familiar, or mechanical changes a standard reviewer clears. Answer deep for novel logic, cross-cutting changes, vendored or generated code, reviewer-selection logic, or wide multi-area features.",
    "criteria": {
        "routine": "Small or familiar change: few areas, no new algorithms, no vendored/generated code, no review-policy logic.",
        "deep": "Novel or risky change: new logic paths, vendored/generated artifacts, cross-cutting edits across many areas, or changes to review/selection machinery itself.",
    },
}
TEMPLATE_QUESTION = {
    "type": "choice",
    "instructions": "Choose the review template. standard: bounded familiar change, single pass. deep: novel or cross-cutting change needing multi-pass scrutiny (blast radius, deletions, enforcement). gate: the change touches review/merge machinery itself and needs bypass analysis with zero benefit of doubt.",
    "criteria": {
        "standard": "Bounded, familiar, single-area change; routine scrutiny suffices.",
        "deep": "Novel logic, cross-cutting edits, large features, or deletions of live machinery; needs blast-radius and deletion passes.",
        "gate": "Touches review/merge machinery (reviewers, verdicts, gates, selection, scoring); needs bypass analysis.",
    },
}
FILE_IMPACT_QUESTION = {
    "type": "choice",
    "instructions": "How impactful is this single file change within its pull request? Answer low for generated, vendored, lockfile, docs-only, test-only, or tiny mechanical edits. Answer high for product logic, workflows, schemas, configs, review/gate machinery, new components, or large deletions of live code.",
    "criteria": {
        "low": "Generated/vendored/lockfile/docs/test-only content, or trivially small mechanical edits with no behavior change.",
        "high": "Behavior-changing code, workflows, schemas, configs, review or gate machinery, new components, or removal of live machinery.",
    },
}
FILE_SCORE_QUESTION = {
    "type": "score",
    "instructions": "How much reviewer scrutiny does this file change deserve?",
    "criteria": [
        "Mechanical change needing no scrutiny.",
        "Worth a skim for obvious mistakes.",
        "Standard careful read.",
        "High-risk change needing adversarial scrutiny.",
        "Critical path: the review must not miss a defect here.",
    ],
}


@dataclass(frozen=True)
class FileRow:
    path: str
    impact: str
    impact_probability: float
    score: int

    def to_json(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "impact": self.impact,
            "impact_probability": self.impact_probability,
            "score": self.score,
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> FileRow:
        return cls(
            path=data["path"],
            impact=data["impact"],
            impact_probability=float(data["impact_probability"]),
            score=int(data["score"]),
        )


@dataclass(frozen=True)
class Classification:
    size_bucket: str
    capability: str
    template: str
    confidence: float
    gate_flags: tuple[str, ...]
    files: tuple[FileRow, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "size_bucket": self.size_bucket,
            "capability": self.capability,
            "template": self.template,
            "confidence": self.confidence,
            "gate_flags": list(self.gate_flags),
            "files": [item.to_json() for item in self.files],
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> Classification:
        return cls(
            size_bucket=data["size_bucket"],
            capability=data["capability"],
            template=data["template"],
            confidence=float(data["confidence"]),
            gate_flags=tuple(data.get("gate_flags", [])),
            files=tuple(FileRow.from_json(item) for item in data.get("files", [])),
        )


def pr_state(
    repo: str,
    pr: int | None,
    base_sha: str,
    head_sha: str,
    stats: Sequence[tuple[str, int, int, str]],
    gate_flags: Sequence[str],
) -> dict[str, Any]:
    subject = f"PR #{pr}" if pr is not None else "Unopened local change"
    top = sorted(stats, key=lambda item: -(item[1] + item[2]))[:12]
    lines = "; ".join(f"{path} +{added}/-{deleted}" for path, added, deleted, _ in top)
    gate = " Gate signals: touches review/merge machinery." if gate_flags else " Gate signals: none."
    total_added = sum(added for _, added, _, _ in stats)
    total_deleted = sum(deleted for _, _, deleted, _ in stats)
    return {
        "text": (
            f"{subject} in {repo}: {len(stats)} files, +{total_added}/-{total_deleted}, base {base_sha} head "
            f"{head_sha}.{gate} Largest changes: {lines}."
        )
    }


def file_state(
    path: str,
    added: int,
    deleted: int,
    status: str,
    siblings: Sequence[tuple[str, int, int]],
) -> dict[str, Any]:
    areas: dict[str, list[int]] = {}
    for sibling, a, d in siblings:
        top = sibling.split("/")[0] if "/" in sibling else "(root)"
        cell = areas.setdefault(top, [0, 0])
        cell[0] += a
        cell[1] += d
    ranked = sorted(areas.items(), key=lambda item: -(item[1][0] + item[1][1]))[:5]
    context = ", ".join(f"{name} +{cell[0]}/-{cell[1]}" for name, cell in ranked)
    return {"text": (f"File: {path} (+{added}/-{deleted}, {status}). Sibling areas by churn: {context}.")}


def _choice(answer: Mapping[str, Any]) -> tuple[str, dict[str, float], float]:
    probs = {key: float(value) for key, value in answer.get("probabilities", {}).items()}
    return str(answer.get("choice", "")), probs, float(answer.get("confidence", 0.0))


def _score_to_int(answer: Mapping[str, Any]) -> int:
    try:
        return min(5, max(1, round(float(answer.get("score", 1)))))
    except (TypeError, ValueError):
        return 1


def apply_rules(
    size_answer: Mapping[str, Any],
    capability_answer: Mapping[str, Any],
    template_answer: Mapping[str, Any],
    gate_flags: Sequence[str],
) -> tuple[str, str, str, float]:
    size, size_probs, size_confidence = _choice(size_answer)
    if size not in SIZE_BUCKETS:
        raise ValueError(f"unknown size bucket {size!r}")
    bucket = size
    if size_probs.get(size, 0.0) < CONFIDENCE_BAR:
        stepped = SIZE_BUCKETS.index(size) - 1
        bucket = SIZE_BUCKETS[max(stepped, SIZE_BUCKETS.index("small"))]
    if bucket == "trivial" and size_probs.get("trivial", 0.0) < CONFIDENCE_BAR:
        bucket = "small"
    capability, capability_probs, _ = _choice(capability_answer)
    if capability not in ("routine", "deep"):
        raise ValueError(f"unknown capability {capability!r}")
    if capability_probs.get(capability, 0.0) < CONFIDENCE_BAR:
        capability = "routine"
    template, template_probs, _ = _choice(template_answer)
    if template not in ("standard", "deep", "gate"):
        raise ValueError(f"unknown template {template!r}")
    if gate_flags:
        template = "gate"
        bucket = SIZE_BUCKETS[max(SIZE_BUCKETS.index(bucket), SIZE_BUCKETS.index(GATE_BUCKET_FLOOR))]
    elif template_probs.get(template, 0.0) < CONFIDENCE_BAR:
        template = "standard"
    return bucket, capability, template, size_confidence


def classify_diff(
    session: JevSession,
    *,
    repo: str,
    pr: int | None,
    base_sha: str,
    head_sha: str,
    stats: Sequence[tuple[str, int, int, str]],
    gate_flags: Sequence[str],
) -> Classification:
    state = pr_state(repo, pr, base_sha, head_sha, stats, gate_flags)
    answers = session.ask(
        state, {"size": SIZE_QUESTION, "capability": CAPABILITY_QUESTION, "template": TEMPLATE_QUESTION}
    )
    bucket, capability, template, confidence = apply_rules(
        answers["size"], answers["capability"], answers["template"], gate_flags
    )
    rows = []
    ranked = sorted(stats, key=lambda item: -(item[1] + item[2]))[:MAX_SCORED_FILES]
    for path, added, deleted, status in ranked:
        siblings = [(other, a, d) for other, a, d, _ in stats if other != path]
        file_answers = session.ask(
            file_state(path, added, deleted, status, siblings),
            {"impact": FILE_IMPACT_QUESTION, "importance": FILE_SCORE_QUESTION},
        )
        impact, impact_probs, _ = _choice(file_answers["impact"])
        rows.append(
            FileRow(
                path=path,
                impact=impact if impact in ("low", "high") else "low",
                impact_probability=impact_probs.get(impact, 0.0),
                score=_score_to_int(file_answers["importance"]),
            )
        )
    return Classification(
        size_bucket=bucket,
        capability=capability,
        template=template,
        confidence=confidence,
        gate_flags=tuple(gate_flags),
        files=tuple(rows),
    )
