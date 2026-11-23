from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from benchbox.core.cost.models import published_total_cost
from benchbox.core.labels import disambiguate_platform_labels
from benchbox.core.results.loader import find_latest_result
from benchbox.core.results.models import BenchmarkResults
from benchbox.core.results.normalizer import normalize_result_dict
from benchbox.core.runtime_paths import resolve_results_dir_from_env
from benchbox.core.visualization.exceptions import VisualizationError
from benchbox.core.visualization.suggestions import recommend_charts
from benchbox.core.visualization.utils import natural_query_sort_key

logger = logging.getLogger(__name__)


@dataclass
class NormalizedQuery:
    query_id: str
    execution_time_ms: float | None
    status: str = "UNKNOWN"


@dataclass
class NormalizedResult:
    benchmark: str
    platform: str
    scale_factor: str | float | int
    execution_id: str | None
    timestamp: datetime | None
    total_time_ms: float | None
    avg_time_ms: float | None
    success_rate: float | None
    cost_total: float | None
    queries: list[NormalizedQuery] = field(default_factory=list)
    source_path: Path | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    execution_mode: str | None = None
    power_at_size: float | None = None


class _NormalizedResultAdapter:
    __slots__ = ("_result",)

    def __init__(self, result: NormalizedResult) -> None:
        self._result = result

    @property
    def platform(self) -> str:
        return self._result.platform

    @property
    def platform_id(self) -> str:
        return self._result.platform

    @property
    def driver_version(self) -> str | None:
        raw = self._result.raw or {}
        block = raw.get("platform") or raw.get("platform_info") or {}
        if isinstance(block, dict):
            return block.get("version")
        return None

    @property
    def execution_mode(self) -> str | None:
        return self._result.execution_mode

    @property
    def scale_factor(self) -> float:
        sf = self._result.scale_factor
        try:
            return float(sf)
        except (TypeError, ValueError):
            return 0.0

    @property
    def run_date(self) -> str:
        ts = self._result.timestamp
        return ts.strftime("%Y-%m-%d") if ts else ""


class ResultPlotter:
    def __init__(self, results: Sequence[NormalizedResult], theme: str = "light"):
        if not results:
            raise VisualizationError("No results provided for visualization.")
        self.results = list(results)
        adapters = [_NormalizedResultAdapter(r) for r in self.results]
        labels = disambiguate_platform_labels(adapters)
        for result, label in zip(self.results, labels):
            result.platform = label
        self._sort_results_by_version()
        self.theme = theme

    def _sort_results_by_version(self) -> None:

        def _version_key(result: NormalizedResult) -> tuple:
            m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?(?:-(\w+))?", result.platform)
            if not m:
                return (float("inf"), 0, 0, 0, result.platform)
            major = int(m.group(1))
            minor = int(m.group(2))
            patch = int(m.group(3)) if m.group(3) else 0
            is_pre = 1 if m.group(4) else 0
            return (major, minor, patch, is_pre, result.platform)

        self.results.sort(key=_version_key)

    @classmethod
    def from_sources(
        cls,
        sources: Sequence[str | Path] | None = None,
        theme: str = "light",
    ) -> ResultPlotter:
        normalized: list[NormalizedResult] = []
        resolved_sources = cls._expand_sources(sources)
        for path in resolved_sources:
            try:
                with open(path, encoding="utf-8") as handle:
                    data = json.load(handle)
            except FileNotFoundError:
                logger.warning("Result file not found: %s", path)
                continue
            except json.JSONDecodeError as exc:
                logger.warning("Skipping invalid JSON file %s: %s", path, exc)
                continue

            normalized.append(cls._normalize_dict(data, source_path=path))

        if not normalized:
            raise VisualizationError("No valid result files found for visualization.")
        return cls(normalized, theme=theme)

    @classmethod
    def from_benchmark_results(
        cls,
        results: Sequence[BenchmarkResults],
        theme: str = "light",
    ) -> ResultPlotter:
        normalized = [cls._normalize_benchmark_result(res) for res in results]
        return cls(normalized, theme=theme)

    @staticmethod
    def _expand_sources(sources: Sequence[str | Path] | None) -> list[Path]:
        if sources:
            candidates = []
            for source in sources:
                path = Path(source)
                if path.is_dir():
                    candidates.extend(path.rglob("*.json"))
                elif path.is_file():
                    candidates.append(path)
            return sorted({p.resolve() for p in candidates})

        results_dir = resolve_results_dir_from_env()
        latest = find_latest_result(results_dir)
        if not latest:
            raise VisualizationError(f"No result sources provided and none found in {results_dir}.")
        return [latest]

    @staticmethod
    def _normalize_dict(data: dict[str, Any], source_path: Path | None) -> NormalizedResult:
        normalized = normalize_result_dict(data)

        platform = normalized.platform

        queries = [
            NormalizedQuery(
                query_id=q.query_id,
                execution_time_ms=q.execution_time_ms,
                status=q.status,
            )
            for q in normalized.queries
        ]

        tpc_metrics = (data.get("summary") or {}).get("tpc_metrics") or {}
        power_at_size = tpc_metrics.get("power_at_size")
        if power_at_size is not None:
            power_at_size = float(power_at_size)

        return NormalizedResult(
            benchmark=normalized.benchmark,
            platform=platform,
            scale_factor=normalized.scale_factor,
            execution_id=normalized.execution_id,
            timestamp=normalized.timestamp,
            total_time_ms=normalized.total_time_ms,
            avg_time_ms=normalized.avg_time_ms,
            success_rate=normalized.success_rate,
            cost_total=normalized.cost_total,
            queries=queries,
            source_path=source_path,
            raw=data,
            execution_mode=normalized.execution_mode,
            power_at_size=power_at_size,
        )

    @staticmethod
    def _normalize_benchmark_result(result: BenchmarkResults) -> NormalizedResult:
        timestamp = getattr(result, "timestamp", None)
        if timestamp and not isinstance(timestamp, datetime):
            try:
                timestamp = datetime.fromisoformat(str(timestamp))
            except Exception:
                timestamp = None

        queries: list[NormalizedQuery] = []
        for query in getattr(result, "query_results", []) or []:
            execution_time_ms = query.get("execution_time_ms")
            if execution_time_ms is None and "execution_time_seconds" in query:
                execution_time_ms = float(query["execution_time_seconds"]) * 1000.0
            if execution_time_ms is None and "execution_time" in query:
                execution_time_ms = float(query["execution_time"]) * 1000.0
            queries.append(
                NormalizedQuery(
                    query_id=query.get("query_id") or query.get("id") or "unknown",
                    execution_time_ms=execution_time_ms,
                    status=query.get("status", "UNKNOWN"),
                )
            )

        total_ms = getattr(result, "total_execution_time", None)
        if total_ms is not None:
            total_ms = float(total_ms) * 1000.0

        avg_ms = getattr(result, "average_query_time", None)
        if avg_ms is not None:
            avg_ms = float(avg_ms) * 1000.0

        success_rate = None
        if getattr(result, "total_queries", None):
            success_rate = (result.successful_queries or 0) / float(result.total_queries)

        cost_summary = getattr(result, "cost_summary", None) or {}

        platform = str(getattr(result, "platform", "unknown"))
        platform_info = getattr(result, "platform_info", None) or {}
        execution_mode = platform_info.get("execution_mode")

        return NormalizedResult(
            benchmark=str(getattr(result, "benchmark_name", "unknown")),
            platform=platform,
            scale_factor=getattr(result, "scale_factor", "unknown"),
            execution_id=getattr(result, "execution_id", None),
            timestamp=timestamp,
            total_time_ms=total_ms,
            avg_time_ms=avg_ms,
            success_rate=success_rate,
            cost_total=published_total_cost(cost_summary),
            queries=queries,
            source_path=None,
            raw={},
            execution_mode=execution_mode,
            power_at_size=getattr(result, "power_at_size", None),
        )

    def _suggest_chart_types(self) -> list[str]:
        return [recommendation.chart_type for recommendation in recommend_charts(self.results)]

    def _performance_score(self, result: NormalizedResult) -> float:
        if result.avg_time_ms and result.avg_time_ms > 0:
            return 1000.0 / result.avg_time_ms
        if result.total_time_ms and result.total_time_ms > 0 and result.queries:
            return (len(result.queries) * 1000.0) / result.total_time_ms
        return 0.0

    def _benchmark_label(self) -> str:
        benchmarks = {r.benchmark for r in self.results}
        if len(benchmarks) == 1:
            return next(iter(benchmarks))
        return ", ".join(sorted(benchmarks))

    @staticmethod
    def _natural_sort_key(s: str) -> tuple[float, str]:
        return natural_query_sort_key(s)

    def group_by(self, field: str) -> dict[str, ResultPlotter]:
        if field not in {"platform", "benchmark"}:
            raise VisualizationError("group_by must be 'platform' or 'benchmark'.")

        groups: dict[str, list[NormalizedResult]] = {}
        for result in self.results:
            key = getattr(result, field)
            groups.setdefault(key, []).append(result)

        return {key: ResultPlotter(values, theme=self.theme) for key, values in groups.items()}
