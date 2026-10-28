from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .classify import CONFIDENCE_BAR
from .jev import JevError, JevSession
from .verdict import shape_fingerprint

RETRY = "retry"
STOP = "stop"
ESCALATE = "escalate"
CHOICES = (RETRY, STOP, ESCALATE)
JEV = "jev"
FALLBACK = "fallback"
UNAVAILABLE = "unavailable"
UNKEYED = "unkeyed"
OUT_OF_LABEL = "out-of-label"
LOW_CONFIDENCE = "low-confidence"
FALLBACK_REASONS = (UNAVAILABLE, UNKEYED, OUT_OF_LABEL, LOW_CONFIDENCE)
MAX_MESSAGE = 300
MAX_EARLIER = 8
UNKNOWN_SHAPE = "unavailable"
_SHAPE_MARKER = re.compile(r"shape=(\{.*\})")
_SHAPE_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,39}")
_SHAPE_META = frozenset({"<unsafe keys>", "<omitted keys>", "object_keys", "list_items"})
_SHAPE_TYPES = frozenset({"str", "int", "float", "bool", "NoneType"})

RECOVERY_QUESTION = {
    "type": "choice",
    "instructions": (
        "A reviewer model's reply was rejected by the review loop. Decide what the loop does next. Judge whether "
        "the failure belongs to this model's own output or to the review process. A process failure repeats for "
        "every reviewer whatever its tier; a model failure is specific to the model that produced the reply. Two "
        "kinds of message tell them apart. A message about the JSON itself (prose instead of JSON, malformed or "
        "truncated JSON, a missing, extra or misnamed key, a wrong value type) describes the model's own output, "
        "which a stronger model avoids. A message about the review contract (a disputed finding, a concedes "
        "boolean, a restated rebuttal, evidence the process requires) or about the harness (an empty brief, a "
        "changed workspace) is rejected for any reviewer, so a stronger model fails the same way. Earlier invalid "
        "messages in this run that repeat across reviewers or tiers confirm a process failure."
    ),
    "criteria": {
        "retry": (
            "Run this same reviewer again. This is the reviewer's first failed attempt and the message is a "
            "one-off slip of the model's own output, such as a stray key, prose around the JSON or a truncated "
            "reply."
        ),
        "stop": (
            "End the run: the review process is blocked. The message is about the review contract or the "
            "harness, or the same or a closely similar message repeats across reviewers or tiers. A larger model "
            "would fail the same way."
        ),
        "escalate": (
            "Move up to the next, stronger reviewer tier. The message is about this model's own JSON output "
            "shape, this reviewer has already failed once, and nothing in the earlier messages suggests the "
            "process is at fault."
        ),
    },
}


@dataclass(frozen=True)
class Recovery:
    choice: str
    source: str
    answer: str = ""
    probability: float | None = None
    confidence: float | None = None
    reason: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "choice": self.choice,
            "source": self.source,
            "answer": self.answer,
            "probability": self.probability,
            "confidence": self.confidence,
            "reason": self.reason,
        }

    @classmethod
    def from_json(cls, data: Any) -> Recovery | None:
        keys = {"choice", "source", "answer", "probability", "confidence", "reason"}
        if not isinstance(data, dict) or set(data) != keys:
            return None
        choice, source, answer, reason = data["choice"], data["source"], data["answer"], data["reason"]
        if not all(isinstance(value, str) for value in (choice, source, answer, reason)) or choice not in CHOICES:
            return None
        numbers = (data["probability"], data["confidence"])
        if not all(value is None or _number(value) for value in numbers):
            return None
        recovery = cls(choice, source, answer, data["probability"], data["confidence"], reason)
        return recovery if recovery.coherent() else None

    def coherent(self) -> bool:
        if self.source == JEV:
            return (
                self.answer == self.choice
                and self.reason == ""
                and self.probability is not None
                and self.confidence is not None
                and self.confidence >= CONFIDENCE_BAR
            )
        return (
            self.source == FALLBACK
            and self.choice in (RETRY, STOP)
            and self.reason in FALLBACK_REASONS
            and (self.reason != LOW_CONFIDENCE or (self.confidence is not None and self.confidence < CONFIDENCE_BAR))
        )


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and 0.0 <= float(value) <= 1.0


def fallback_choice(number: int, limit: int) -> str:
    return RETRY if number < limit else STOP


def fallback(
    number: int,
    limit: int,
    reason: str,
    answer: str = "",
    probability: float | None = None,
    confidence: float | None = None,
) -> Recovery:
    return Recovery(fallback_choice(number, limit), FALLBACK, answer, probability, confidence, reason)


def shape_of(artifact: Mapping[str, Any]) -> str:
    verdict = artifact.get("verdict")
    if isinstance(verdict, dict):
        return shape_fingerprint(verdict)
    for line in str(artifact.get("diagnostic") or "").splitlines()[:2]:
        match = _SHAPE_MARKER.fullmatch(line.strip())
        if match is not None:
            return _checked_shape(match.group(1))
        if not line.startswith("rewrites="):
            break
    return UNKNOWN_SHAPE


def _checked_shape(text: str) -> str:
    try:
        data = json.loads(text)
    except ValueError:
        return UNKNOWN_SHAPE
    if not _plain_shape(data):
        return UNKNOWN_SHAPE
    return json.dumps(data, ensure_ascii=True, separators=(",", ":"))


def _plain_shape(value: Any) -> bool:
    if isinstance(value, str):
        return value in _SHAPE_TYPES
    if isinstance(value, int) and not isinstance(value, bool):
        return True
    if isinstance(value, dict):
        return all(
            isinstance(key, str)
            and (key in _SHAPE_META or _SHAPE_KEY.fullmatch(key) is not None)
            and _plain_shape(child)
            for key, child in value.items()
        )
    return False


def _compact(text: str) -> str:
    cleaned = " ".join(text.split())
    return cleaned if len(cleaned) <= MAX_MESSAGE else cleaned[: MAX_MESSAGE - 1] + "\u2026"


def request_state(
    *,
    tier: str,
    harness: str,
    role: str,
    model: str,
    effort: str,
    number: int,
    limit: int,
    message: str,
    shape: str,
    earlier: Sequence[tuple[str, int, str]],
) -> dict[str, Any]:
    prior = (
        "; ".join(f"tier {name}, attempt {count}: {_compact(text)}" for name, count, text in earlier[-MAX_EARLIER:])
        or "none"
    )
    effort_text = f", effort {effort}" if effort else ""
    return {
        "text": (
            f"Invalid reviewer attempt. Reviewer tier {tier}, model role {role} ({harness} {model}{effort_text}); "
            f"this is attempt {number} of at most {limit} for this reviewer in this run. "
            f"Validation message: {_compact(message)}. "
            f"Reply shape (types and counts only): {shape}. "
            f"Earlier invalid attempts in this run: {prior}."
        )
    }


def judge(answer: Mapping[str, Any] | None, number: int, limit: int) -> Recovery:
    if not isinstance(answer, Mapping):
        return fallback(number, limit, OUT_OF_LABEL)
    choice = answer.get("choice")
    if not isinstance(choice, str) or choice not in CHOICES:
        return fallback(number, limit, OUT_OF_LABEL)
    try:
        confidence = float(answer.get("confidence"))
        probabilities = answer.get("probabilities")
        probability = float(probabilities[choice])
    except (TypeError, ValueError, KeyError):
        return fallback(number, limit, OUT_OF_LABEL)
    if not (0.0 <= confidence <= 1.0 and 0.0 <= probability <= 1.0):
        return fallback(number, limit, OUT_OF_LABEL)
    if confidence < CONFIDENCE_BAR:
        return fallback(number, limit, LOW_CONFIDENCE, choice, probability, confidence)
    return Recovery(choice, JEV, choice, probability, confidence)


def classify(session: JevSession, state: Mapping[str, Any], number: int, limit: int) -> Recovery:
    if not session.api_key:
        return fallback(number, limit, UNKEYED)
    try:
        with session:
            answers = session.ask(state, {"recovery": RECOVERY_QUESTION})
    except JevError:
        return fallback(number, limit, UNAVAILABLE)
    answer = answers.get("recovery") if isinstance(answers, Mapping) else None
    return judge(answer, number, limit)
