from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TuningVerificationSummary:
    status: str
    verdict_counts: tuple[tuple[str, int], ...]
    top_reasons: tuple[tuple[str, int], ...]
    dropped: tuple[tuple[str, str], ...]


def summarize_tuning_verification(ledger: Any) -> TuningVerificationSummary | None:
    if not isinstance(ledger, dict):
        return None
    statements = ledger.get("statements")
    dropped_raw = ledger.get("dropped")
    receipt = ledger.get("receipt")
    if not statements and not dropped_raw and not receipt:
        return None
    status = ledger.get("status")
    entries = receipt.get("entries") if isinstance(receipt, dict) else None
    verdict_counts = _count_verdicts(entries, statements)
    reasons: Counter[str] = Counter()
    if isinstance(entries, list):
        for entry in entries:
            if isinstance(entry, dict):
                reason = entry.get("reason")
                if isinstance(reason, str) and reason.strip():
                    reasons[reason] += 1
    top_reasons = tuple(sorted(reasons.items(), key=lambda item: (-item[1], item[0]))[:3])
    dropped: list[tuple[str, str]] = []
    if isinstance(dropped_raw, list):
        for item in dropped_raw:
            if isinstance(item, dict) and isinstance(item.get("intent"), str):
                reason = item.get("reason")
                dropped.append((item["intent"], reason if isinstance(reason, str) else ""))
    return TuningVerificationSummary(
        status=str(status) if status is not None else "unknown",
        verdict_counts=verdict_counts,
        top_reasons=top_reasons,
        dropped=tuple(dropped),
    )


def _count_verdicts(entries: Any, statements: Any) -> tuple[tuple[str, int], ...]:
    counts: Counter[str] = Counter()
    if isinstance(entries, list) and any(isinstance(entry, dict) for entry in entries):
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            verdict = entry.get("verdict")
            counts[str(verdict) if verdict is not None else "unknown"] += 1
    elif isinstance(statements, list):
        for statement in statements:
            if not isinstance(statement, dict):
                continue
            state = statement.get("status")
            counts[str(state) if state is not None else "unknown"] += 1
    return tuple(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def format_tuning_verification(summary: TuningVerificationSummary) -> list[str]:
    lines = [f"Tuning verification: {summary.status}"]
    if summary.verdict_counts:
        counts = ", ".join(f"{verdict}={count}" for verdict, count in summary.verdict_counts)
        lines.append(f"  Verdicts: {counts}")
    else:
        lines.append("  Verdicts: none recorded")
    if summary.top_reasons:
        lines.append("  Top reasons:")
        for reason, count in summary.top_reasons:
            lines.append(f"    - {reason} (n={count})")
    else:
        lines.append("  Top reasons: none recorded")
    if summary.dropped:
        lines.append(f"  Dropped intents ({len(summary.dropped)}):")
        for intent, reason in summary.dropped:
            lines.append(f"    - {intent} — {reason}" if reason else f"    - {intent}")
    else:
        lines.append("  Dropped intents (0)")
    return lines
