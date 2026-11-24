from __future__ import annotations

import importlib.util
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

OFFICIAL_COMPLIANCE_CLASS = "official"

_rule_path = Path(__file__).with_name("corpus_rule.py")
_rule_spec = importlib.util.spec_from_file_location("_benchbox_subscale_corpus_rule", _rule_path)
if _rule_spec is None or _rule_spec.loader is None:
    raise ImportError(f"Cannot load corpus rule from {_rule_path}")
_corpus_rule = importlib.util.module_from_spec(_rule_spec)
sys.modules[_rule_spec.name] = _corpus_rule
_rule_spec.loader.exec_module(_corpus_rule)
SUBSCALE_COMPLIANCE_CLASS = "unofficial_subscale"
NONSTANDARD_COMPLIANCE_CLASS = "unofficial_nonstandard"
UNOFFICIAL_COMPLIANCE_CLASSES = frozenset({SUBSCALE_COMPLIANCE_CLASS, NONSTANDARD_COMPLIANCE_CLASS})
RANKING_EXCLUDED_COMPLIANCE_CLASSES = frozenset({NONSTANDARD_COMPLIANCE_CLASS})
SUBSCALE_TPC_BENCHMARKS = frozenset({"tpch", "tpcds"})


def normalize_tpc_benchmark_id(benchmark_id: Any) -> str:
    return "".join(character for character in str(benchmark_id or "").lower() if character.isalnum())


def is_subscale_tpc(benchmark_id: Any, scale_factor: Any) -> bool:
    try:
        scale = float(scale_factor)
    except (TypeError, ValueError):
        return False
    return normalize_tpc_benchmark_id(benchmark_id) in SUBSCALE_TPC_BENCHMARKS and 0 < scale < 1.0


def is_nonstandard_scale(benchmark_id: Any, scale_factor: Any) -> bool:
    if isinstance(scale_factor, bool) or scale_factor is None:
        return False
    try:
        scale = float(scale_factor)
    except (TypeError, ValueError):
        return False
    try:
        return _corpus_rule.is_standard_scale(benchmark_id, scale) is False
    except RuntimeError as error:
        try:
            from benchbox.core.benchmark_registry import get_scale_factor_rule
        except ImportError:
            raise error from None
        try:
            rule = get_scale_factor_rule(str(benchmark_id))
        except ValueError:
            return False
        benchmark = _corpus_rule.normalize_benchmark_id(benchmark_id)
        return (
            _corpus_rule.is_standard_scale(benchmark, scale, {"scales": {benchmark: {"standard": rule.standard}}})
            is False
        )


def effective_compliance_class(benchmark_id: Any, scale_factor: Any, recorded: Any) -> str | None:
    if is_nonstandard_scale(benchmark_id, scale_factor):
        return NONSTANDARD_COMPLIANCE_CLASS
    if is_subscale_tpc(benchmark_id, scale_factor):
        return SUBSCALE_COMPLIANCE_CLASS
    if recorded is not None:
        return str(getattr(recorded, "value", recorded))
    return None


def gateway_routed(gateway: Any) -> bool:
    return isinstance(gateway, Mapping) and bool(gateway.get("routed"))


def routed_compliance_class(compliance_class: Any, gateway: Any) -> str | None:
    if gateway_routed(gateway):
        return NONSTANDARD_COMPLIANCE_CLASS
    if compliance_class is None:
        return None
    return str(getattr(compliance_class, "value", compliance_class))


def platform_info_gateway(platform_info: Any) -> Any:
    if not isinstance(platform_info, Mapping):
        return None
    if "gateway" in platform_info:
        return platform_info["gateway"]
    configuration = platform_info.get("configuration")
    return configuration.get("gateway") if isinstance(configuration, Mapping) else None


def result_gateway_routed(result: Any) -> bool:
    return gateway_routed(platform_info_gateway(getattr(result, "platform_info", None)))


def result_compliance_class(result: Any) -> str | None:
    gateway = platform_info_gateway(getattr(result, "platform_info", None))
    return routed_compliance_class(getattr(result, "compliance_class", None), gateway)


def unofficial_metric_marker(compliance_class: str) -> dict[str, Any]:
    return {"unofficial": True, "reason": f"compliance_class={compliance_class}"}
