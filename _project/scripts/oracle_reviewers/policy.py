from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SIZE_BUCKETS = ("trivial", "small", "medium", "large", "huge", "max")
SEVERITIES = ("Critical", "High", "Medium", "Low")
HARNESSES = ("omp",)
OMP_THINKING_LEVELS = ("off", "minimal", "low", "medium", "high", "xhigh", "max", "auto")
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
            effort=data.get("effort", ""),
            role=data["role"],
            read_only=data["read_only"],
            reads_files=bool(data["reads_files"]),
            timeout_minutes=int(data["timeout_minutes"]),
            enabled=bool(data["enabled"]),
            disabled_reason=data.get("disabled_reason", ""),
        )


@dataclass(frozen=True)
class ClassifierRules:
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
    retry: RetryRules
    protocol: ProtocolRules
    pools: tuple[str, ...]
    reviewers: Mapping[str, Reviewer]
    buckets: Mapping[str, str]
    classifier: ClassifierRules

    def chain(self, bucket: str) -> list[Reviewer]:
        return [self.reviewers[self.buckets[bucket]]]

    def longest_enabled_chain(self) -> int:
        return max(1 if reviewer.enabled else 0 for reviewer in self.reviewers.values())


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
        enabled=data.get("enabled"),
        disabled_reason=data.get("disabled_reason", ""),
    )
    _require(reviewer.harness in HARNESSES, f"reviewer {name} has unknown harness {reviewer.harness!r}")
    _require(reviewer.role == "review", f"reviewer {name} has unknown role {reviewer.role!r}")
    _require(reviewer.pool in pools, f"reviewer {name} has unknown pool {reviewer.pool!r}")
    _require(bool(reviewer.family), f"reviewer {name} needs a family label")
    _require(bool(reviewer.model), f"reviewer {name} needs a model")
    _require(
        not reviewer.effort or reviewer.effort in OMP_THINKING_LEVELS,
        f"reviewer {name} has unknown effort {reviewer.effort!r}",
    )
    _require(reviewer.read_only in READ_ONLY_KINDS, f"reviewer {name} has unknown read_only {reviewer.read_only!r}")
    _require(isinstance(reviewer.reads_files, bool), f"reviewer {name} reads_files must be a boolean")
    _require(
        isinstance(reviewer.timeout_minutes, int) and 0 < reviewer.timeout_minutes <= 60,
        f"reviewer {name} timeout_minutes must be between 1 and 60",
    )
    _require(isinstance(reviewer.enabled, bool), f"reviewer {name} enabled must be a boolean")
    _require(reviewer.enabled or bool(reviewer.disabled_reason), f"disabled reviewer {name} needs a disabled_reason")
    return reviewer


def parse_policy(data: Mapping[str, Any]) -> Policy:
    _require(isinstance(data, Mapping) and data.get("version") == 2, "policy version must be 2")
    pools = _str_tuple(data.get("pools"), "pools")
    reviewers_data = data.get("reviewers")
    _require(isinstance(reviewers_data, Mapping) and bool(reviewers_data), "reviewers must be a mapping")
    reviewers = {name: _reviewer(name, value, pools) for name, value in reviewers_data.items()}
    buckets_data = data.get("buckets")
    _require(
        isinstance(buckets_data, Mapping) and set(buckets_data) == set(SIZE_BUCKETS),
        f"buckets must be exactly {SIZE_BUCKETS}",
    )
    for bucket in SIZE_BUCKETS:
        _require(buckets_data[bucket] in reviewers, f"bucket {bucket} names unknown reviewer {buckets_data[bucket]}")
    classifier = data.get("classifier") or {}
    retry = data.get("retry") or {}
    protocol = data.get("protocol") or {}
    policy = Policy(
        mode=data.get("mode", ""),
        status_context=data.get("status_context", ""),
        findings_delivery=data.get("findings_delivery", ""),
        bot_login=data.get("bot_login", ""),
        brief_max_bytes=int(data.get("brief_max_bytes", 0)),
        max_attempts=int(data.get("max_attempts", 0)),
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
        buckets=dict(buckets_data),
        classifier=ClassifierRules(
            gate_paths=_str_tuple(classifier.get("gate_paths"), "classifier gate_paths"),
            logic_paths=_str_tuple(classifier.get("logic_paths"), "classifier logic_paths"),
            data_paths=_str_tuple(classifier.get("data_paths"), "classifier data_paths"),
        ),
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
    _require(
        policy.protocol.max_defects > 0 and policy.protocol.max_do_not_ship > 0, "protocol limits must be positive"
    )
    _require(policy.retry.daily_budget > 0 and policy.retry.backoff_start_minutes > 0, "retry rules must be positive")
    return policy


def load_policy(path: Path) -> Policy:
    import yaml

    return parse_policy(yaml.safe_load(path.read_text(encoding="utf-8")))
