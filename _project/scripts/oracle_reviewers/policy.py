from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

TIERS = ("low-medium", "medium-high", "very-high")
SEVERITIES = ("Critical", "High", "Medium", "Low")
HARNESSES = ("claude", "codex", "muse", "agy")
READ_ONLY_KINDS = ("hard", "soft")
DELIVERIES = ("comment", "review")


class PolicyError(ValueError):
    pass


@dataclass(frozen=True)
class Reviewer:
    name: str
    harness: str
    family: str
    pool: str
    model: str
    effort: str
    role: str
    read_only: str
    reads_files: bool
    timeout_minutes: int
    turn_cap: int | None
    allowed_tiers: tuple[str, ...]
    enabled: bool
    disabled_reason: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "harness": self.harness,
            "family": self.family,
            "pool": self.pool,
            "model": self.model,
            "role": self.role,
            "effort": self.effort,
            "read_only": self.read_only,
            "reads_files": self.reads_files,
            "timeout_minutes": self.timeout_minutes,
            "turn_cap": self.turn_cap,
            "allowed_tiers": list(self.allowed_tiers),
            "enabled": self.enabled,
            "disabled_reason": self.disabled_reason,
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> Reviewer:
        return cls(
            name=data["name"],
            harness=data["harness"],
            family=data["family"],
            pool=data["pool"],
            model=data["model"],
            effort=data["effort"],
            role=data["role"],
            read_only=data["read_only"],
            reads_files=bool(data["reads_files"]),
            timeout_minutes=int(data["timeout_minutes"]),
            turn_cap=data["turn_cap"],
            allowed_tiers=tuple(data["allowed_tiers"]),
            enabled=bool(data["enabled"]),
            disabled_reason=data.get("disabled_reason", ""),
        )


@dataclass(frozen=True)
class Tier:
    name: str
    order: tuple[str, ...]
    diversity_exempt: tuple[str, ...]


@dataclass(frozen=True)
class ClassifierRules:
    area_depth: int
    small_max_lines: int
    large_min_lines: int
    large_min_areas: int
    gate_paths: tuple[str, ...]
    logic_paths: tuple[str, ...]
    data_paths: tuple[str, ...]


@dataclass(frozen=True)
class ProtocolRules:
    max_defects: int
    max_do_not_ship: int


@dataclass(frozen=True)
class RetryRules:
    daily_budget: int
    backoff_start_minutes: int
    sweep_max_dispatches: int


@dataclass(frozen=True)
class Policy:
    mode: str
    status_context: str
    findings_delivery: str
    bot_login: str
    brief_max_bytes: int
    max_attempts: int
    author_label_prefix: str
    default_author_family: str
    tier_labels: Mapping[str, str]
    retry: RetryRules
    protocol: ProtocolRules
    pools: tuple[str, ...]
    reviewers: Mapping[str, Reviewer]
    tiers: Mapping[str, Tier]
    classifier: ClassifierRules
    families: tuple[str, ...] = field(default=())

    def chain(self, tier: str) -> list[Reviewer]:
        return [self.reviewers[name] for name in self.tiers[tier].order]

    def longest_enabled_chain(self) -> int:
        return max(sum(1 for reviewer in self.chain(tier) if reviewer.enabled) for tier in self.tiers)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PolicyError(message)


def _str_tuple(value: Any, label: str) -> tuple[str, ...]:
    _require(isinstance(value, list) and all(isinstance(item, str) for item in value), f"{label} must be a string list")
    return tuple(value)


def _reviewer(name: str, data: Mapping[str, Any], pools: tuple[str, ...]) -> Reviewer:
    _require(isinstance(data, Mapping), f"reviewer {name} must be a mapping")
    reviewer = Reviewer(
        name=name,
        harness=data.get("harness", ""),
        family=data.get("family", ""),
        pool=data.get("pool", ""),
        model=data.get("model", ""),
        effort=data.get("effort", ""),
        role=data.get("role", ""),
        read_only=data.get("read_only", ""),
        reads_files=data.get("reads_files"),
        timeout_minutes=data.get("timeout_minutes", 0),
        turn_cap=data.get("turn_cap"),
        allowed_tiers=_str_tuple(data.get("allowed_tiers"), f"reviewer {name} allowed_tiers"),
        enabled=data.get("enabled"),
        disabled_reason=data.get("disabled_reason", ""),
    )
    _require(reviewer.harness in HARNESSES, f"reviewer {name} has unknown harness {reviewer.harness!r}")
    _require(reviewer.role == "review", f"reviewer {name} has unknown role {reviewer.role!r}")
    _require(reviewer.pool in pools, f"reviewer {name} has unknown pool {reviewer.pool!r}")
    _require(reviewer.family in HARNESSES, f"reviewer {name} has unknown family {reviewer.family!r}")
    _require(bool(reviewer.model) and bool(reviewer.effort), f"reviewer {name} needs a model and an effort")
    _require(reviewer.read_only in READ_ONLY_KINDS, f"reviewer {name} has unknown read_only {reviewer.read_only!r}")
    _require(isinstance(reviewer.reads_files, bool), f"reviewer {name} reads_files must be a boolean")
    _require(
        isinstance(reviewer.timeout_minutes, int) and 0 < reviewer.timeout_minutes <= 60,
        f"reviewer {name} timeout_minutes must be between 1 and 60",
    )
    _require(
        reviewer.turn_cap is None or (isinstance(reviewer.turn_cap, int) and reviewer.turn_cap > 0),
        f"reviewer {name} turn_cap must be a positive integer or null",
    )
    _require(isinstance(reviewer.enabled, bool), f"reviewer {name} enabled must be a boolean")
    _require(reviewer.enabled or bool(reviewer.disabled_reason), f"disabled reviewer {name} needs a disabled_reason")
    _require(set(reviewer.allowed_tiers) <= set(TIERS), f"reviewer {name} names an unknown tier")
    return reviewer


def _tier(name: str, data: Mapping[str, Any], reviewers: Mapping[str, Reviewer]) -> Tier:
    tier = Tier(
        name=name,
        order=_str_tuple(data.get("order"), f"tier {name} order"),
        diversity_exempt=_str_tuple(data.get("diversity_exempt", []), f"tier {name} diversity_exempt"),
    )
    _require(
        "blocking" not in data, f"tier {name}: severity orders defects and never gates, so blocking is not allowed"
    )
    _require(bool(tier.order), f"tier {name} has no reviewers")
    _require(len(set(tier.order)) == len(tier.order), f"tier {name} repeats a reviewer")
    for reviewer_name in tier.order:
        _require(reviewer_name in reviewers, f"tier {name} names unknown reviewer {reviewer_name}")
        _require(
            name in reviewers[reviewer_name].allowed_tiers,
            f"reviewer {reviewer_name} is not allowed in tier {name}",
        )
    _require(set(tier.diversity_exempt) <= set(tier.order), f"tier {name} exempts a reviewer outside its order")
    return tier


def parse_policy(data: Mapping[str, Any]) -> Policy:
    _require(isinstance(data, Mapping) and data.get("version") == 1, "policy version must be 1")
    pools = _str_tuple(data.get("pools"), "pools")
    reviewers_data = data.get("reviewers")
    _require(isinstance(reviewers_data, Mapping) and bool(reviewers_data), "reviewers must be a mapping")
    reviewers = {name: _reviewer(name, value, pools) for name, value in reviewers_data.items()}
    tiers_data = data.get("tiers")
    _require(isinstance(tiers_data, Mapping) and set(tiers_data) == set(TIERS), f"tiers must be exactly {TIERS}")
    tiers = {name: _tier(name, tiers_data[name], reviewers) for name in TIERS}
    classifier = data.get("classifier") or {}
    retry = data.get("retry") or {}
    protocol = data.get("protocol") or {}
    tier_labels = data.get("tier_labels") or {}
    _require(all(value in TIERS for value in tier_labels.values()), "tier_labels must map to known tiers")
    policy = Policy(
        mode=data.get("mode", ""),
        status_context=data.get("status_context", ""),
        findings_delivery=data.get("findings_delivery", ""),
        bot_login=data.get("bot_login", ""),
        brief_max_bytes=int(data.get("brief_max_bytes", 0)),
        max_attempts=int(data.get("max_attempts", 0)),
        author_label_prefix=data.get("author_label_prefix", ""),
        default_author_family=data.get("default_author_family", ""),
        tier_labels=dict(tier_labels),
        retry=RetryRules(
            daily_budget=int(retry.get("daily_budget", 0)),
            backoff_start_minutes=int(retry.get("backoff_start_minutes", 0)),
            sweep_max_dispatches=int(retry.get("sweep_max_dispatches", 0)),
        ),
        protocol=ProtocolRules(
            max_defects=int(protocol.get("max_defects", 0)),
            max_do_not_ship=int(protocol.get("max_do_not_ship", 0)),
        ),
        pools=pools,
        reviewers=reviewers,
        tiers=tiers,
        classifier=ClassifierRules(
            area_depth=int(classifier.get("area_depth", 0)),
            small_max_lines=int(classifier.get("small_max_lines", 0)),
            large_min_lines=int(classifier.get("large_min_lines", 0)),
            large_min_areas=int(classifier.get("large_min_areas", 0)),
            gate_paths=_str_tuple(classifier.get("gate_paths"), "classifier gate_paths"),
            logic_paths=_str_tuple(classifier.get("logic_paths"), "classifier logic_paths"),
            data_paths=_str_tuple(classifier.get("data_paths"), "classifier data_paths"),
        ),
        families=tuple(sorted({reviewer.family for reviewer in reviewers.values()})),
    )
    _require(policy.mode in ("shadow", "enforce"), "mode must be shadow or enforce")
    _require(bool(policy.status_context), "status_context is required")
    _require(policy.findings_delivery in DELIVERIES, f"findings_delivery must be one of {DELIVERIES}")
    _require(re.fullmatch(r"[a-z0-9][a-z0-9-]*", policy.bot_login) is not None, "bot_login must be an App slug")
    _require(0 < policy.brief_max_bytes <= 120_000, "brief_max_bytes must fit in one command-line argument")
    _require(
        policy.max_attempts >= policy.longest_enabled_chain(),
        f"max_attempts must cover the longest enabled chain ({policy.longest_enabled_chain()})",
    )
    _require(policy.default_author_family in policy.families, "default_author_family must be a reviewer family")
    _require(
        policy.protocol.max_defects > 0 and policy.protocol.max_do_not_ship > 0, "protocol limits must be positive"
    )
    _require(policy.retry.daily_budget > 0 and policy.retry.backoff_start_minutes > 0, "retry rules must be positive")
    _require(
        policy.classifier.area_depth > 0 and policy.classifier.small_max_lines > 0,
        "classifier thresholds must be positive",
    )
    return policy


def load_policy(path: Path) -> Policy:
    import yaml

    return parse_policy(yaml.safe_load(path.read_text(encoding="utf-8")))
