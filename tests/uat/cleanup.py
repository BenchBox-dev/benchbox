from __future__ import annotations

import shutil
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from benchbox.core.benchmark_registry import BENCHMARK_METADATA


@dataclass(frozen=True)
class CellKey:
    platform: str
    benchmark: str
    scale: float


@dataclass(frozen=True)
class CleanupDecision:
    safe_to_prune: bool
    reason: str


def remaining_consumers(
    source_benchmark: str,
    completed_cells: list[CellKey],
    pending_cells: list[CellKey],
    *,
    platform: str,
    scale: float,
) -> list[CellKey]:
    consumers = source_reuse_graph().get(source_benchmark, (source_benchmark,))
    out = []
    for cell in pending_cells:
        if cell.platform != platform:
            continue
        if cell.scale != scale:
            continue
        if cell.benchmark in consumers:
            out.append(cell)
    return out


def source_reuse_graph() -> dict[str, tuple[str, ...]]:
    consumers_by_source: defaultdict[str, list[str]] = defaultdict(list)
    for benchmark_id, meta in BENCHMARK_METADATA.items():
        data_source = meta.get("data_source")
        if not isinstance(data_source, str):
            continue
        if data_source not in BENCHMARK_METADATA or data_source == benchmark_id:
            continue
        consumers_by_source[data_source].append(benchmark_id)
    return {source: (source, *tuple(consumers)) for source, consumers in sorted(consumers_by_source.items())}


def can_prune(
    source_benchmark: str,
    *,
    platform: str,
    scale: float,
    pending_cells: list[CellKey],
    completed_cells: list[CellKey],
) -> CleanupDecision:
    remaining = remaining_consumers(
        source_benchmark,
        completed_cells,
        pending_cells,
        platform=platform,
        scale=scale,
    )
    if remaining:
        names = ", ".join(f"{c.platform}/{c.benchmark}@{c.scale}" for c in remaining)
        return CleanupDecision(
            safe_to_prune=False,
            reason=f"{len(remaining)} pending consumer(s): {names}",
        )
    return CleanupDecision(
        safe_to_prune=True,
        reason=f"no pending consumers of {source_benchmark}@{scale}",
    )


def prune_database_dir(
    databases_root: Path,
    *,
    platform: str,
    benchmark: str,
    scale: float,
    dry_run: bool = False,
) -> int:
    target = databases_root / platform / benchmark / str(scale)
    if not target.exists():
        return 0
    bytes_total = sum(p.stat().st_size for p in target.rglob("*") if p.is_file())
    if not dry_run:
        shutil.rmtree(target, ignore_errors=True)
    return bytes_total


def prune_server_database(
    *,
    platform: str,
    benchmark: str,
    scale: float,
    dry_run: bool = False,
) -> bool:
    if platform != "singlestore":
        return False
    if dry_run:
        return True
    try:
        import os

        from benchbox.platforms.singlestore import SingleStoreAdapter
        from benchbox.utils.scale_factor import format_benchmark_name

        benchmark_name = format_benchmark_name(benchmark, scale)
        db_name = f"benchbox_{benchmark_name}".lower().replace("-", "_")
        port = int(os.environ.get("SINGLESTORE_HOST_PORT", os.environ.get("SINGLESTORE_PORT", "13306")))
        password = os.environ.get("SINGLESTORE_PASSWORD", os.environ.get("ROOT_PASSWORD", "benchbox"))
        adapter = SingleStoreAdapter.from_config(
            {
                "host": os.environ.get("SINGLESTORE_HOST", "localhost"),
                "port": port,
                "username": os.environ.get("SINGLESTORE_USER", "root"),
                "password": password,
            }
        )
        adapter.drop_database(database=db_name)
        return True
    except Exception:
        return False
