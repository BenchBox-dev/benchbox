from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from benchbox.utils.resource_limits import MemorySnapshot
from tests.uat.config import UATConfig
from tests.uat.phases.enumerate import Cell, enumerate_cells

DEFAULT_TABLE_PATH = Path(__file__).resolve().parent / "data" / "disk_budget_table.tsv"


DATABASE_STATUS_MEASURED = "measured"
DATABASE_STATUS_UNMEASURED = "unmeasured"


@dataclass(frozen=True)
class MemoryHeadroomCheck:
    free_gib: float | None
    required_gib: float
    swap_used_percent: float | None

    @property
    def shortfall(self) -> bool:
        return self.free_gib is not None and self.free_gib < self.required_gib


def check_memory_headroom(snapshot: MemorySnapshot, *, min_free_gib: float) -> MemoryHeadroomCheck:
    return MemoryHeadroomCheck(
        free_gib=snapshot.free_gib,
        required_gib=min_free_gib,
        swap_used_percent=snapshot.swap_used_percent,
    )


def format_memory_headroom_failure(check: MemoryHeadroomCheck) -> str:
    swap_note = f"; swap {check.swap_used_percent:.1f}% used" if check.swap_used_percent is not None else ""
    return (
        f"memory headroom gate failed: {check.free_gib:.2f} GiB free < {check.required_gib:.2f} GiB required{swap_note}"
    )


@dataclass(frozen=True)
class DiskBudgetRow:
    platform: str
    benchmark: str
    scale_factor: float
    peak_datagen_gib: float
    peak_database_gib: float
    transient_growth_gib: float
    database_status: str = DATABASE_STATUS_MEASURED

    @property
    def database_measured(self) -> bool:
        return self.database_status == DATABASE_STATUS_MEASURED


@dataclass(frozen=True)
class UnknownDiskCell:
    platform: str
    benchmark: str
    scale: float

    @property
    def key(self) -> str:
        return cell_key(self.platform, self.benchmark, self.scale)


@dataclass(frozen=True)
class DiskBudget:
    cells: int
    est_peak_gib: float
    est_steady_gib: float
    unknown_cells: tuple[UnknownDiskCell, ...]
    database_by_platform_gib: tuple[tuple[str, float], ...] = ()
    concurrent_database_gib: float = 0.0
    chunked_database_gib: float = 0.0
    platforms_total: int = 0
    platforms_with_measured_database: int = 0


@dataclass(frozen=True)
class DiskRootFreeSpace:
    label: str
    path: Path
    free_gib: float


@dataclass(frozen=True)
class DiskHeadroomShortfall:
    label: str
    path: Path
    free_gib: float
    required_gib: float


@dataclass(frozen=True)
class DiskHeadroomCheck:
    required_gib: float
    shortfalls: tuple[DiskHeadroomShortfall, ...]


BudgetTable = dict[tuple[str, str, float], DiskBudgetRow]


def cell_key(platform: str, benchmark: str, scale: float) -> str:
    return f"{platform}|{benchmark}|{scale:g}"


@dataclass(frozen=True)
class CellDiskPrediction:
    platform: str
    benchmark: str
    scale: float
    datagen_gib: float
    transient_growth_gib: float
    database_gib: float | None

    @property
    def database_measured(self) -> bool:
        return self.database_gib is not None

    @property
    def known_growth_gib(self) -> float:
        return self.datagen_gib + self.transient_growth_gib + (self.database_gib or 0.0)

    @property
    def is_lower_bound(self) -> bool:
        return not self.database_measured


def predict_cell_disk_growth(
    platform: str,
    benchmark: str,
    scale: float,
    *,
    table: BudgetTable,
    datagen_already_present: bool = False,
) -> CellDiskPrediction | None:
    row = table.get((platform, benchmark, scale))
    if row is None:
        return None
    return CellDiskPrediction(
        platform=platform,
        benchmark=benchmark,
        scale=scale,
        datagen_gib=0.0 if datagen_already_present else row.peak_datagen_gib,
        transient_growth_gib=row.transient_growth_gib,
        database_gib=row.peak_database_gib if row.database_measured else None,
    )


def load_budget_table(path: Path | None = None) -> BudgetTable:
    table_path = path or DEFAULT_TABLE_PATH
    rows: BudgetTable = {}
    if not table_path.exists():
        return rows
    with table_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        required = {
            "platform",
            "benchmark",
            "scale_factor",
            "peak_datagen_gib",
            "peak_database_gib",
            "transient_growth_gib",
        }
        missing = required.difference(reader.fieldnames or ())
        if missing:
            raise ValueError(f"disk budget table {table_path} missing columns: {sorted(missing)}")
        for row in reader:
            budget_row = DiskBudgetRow(
                platform=row["platform"],
                benchmark=row["benchmark"],
                scale_factor=float(row["scale_factor"]),
                peak_datagen_gib=float(row["peak_datagen_gib"]),
                peak_database_gib=float(row["peak_database_gib"]),
                transient_growth_gib=float(row["transient_growth_gib"]),
                database_status=(row.get("peak_database_gib_status") or DATABASE_STATUS_MEASURED).strip().lower(),
            )
            rows[(budget_row.platform, budget_row.benchmark, budget_row.scale_factor)] = budget_row
    return rows


def estimate_peak_disk(config: UATConfig, *, table_path: Path | None = None) -> DiskBudget:
    return estimate_cells(enumerate_cells(config), table=load_budget_table(table_path))


def estimate_cells(cells: Iterable[Cell], *, table: BudgetTable) -> DiskBudget:
    cells_tuple = tuple(cells)
    datagen_by_source: dict[tuple[str, float], float] = {}
    database_by_platform: dict[str, float] = {}
    transient_gib = 0.0
    unknown: list[UnknownDiskCell] = []

    for cell in cells_tuple:
        row = table.get((cell.platform, cell.benchmark, cell.scale))
        if row is None:
            unknown.append(UnknownDiskCell(cell.platform, cell.benchmark, cell.scale))
            continue
        datagen_key = (cell.benchmark, cell.scale)
        datagen_by_source[datagen_key] = max(datagen_by_source.get(datagen_key, 0.0), row.peak_datagen_gib)
        if row.database_measured:
            database_by_platform[cell.platform] = database_by_platform.get(cell.platform, 0.0) + row.peak_database_gib
        transient_gib += row.transient_growth_gib

    database_gib = sum(database_by_platform.values())
    platform_totals = tuple(sorted(database_by_platform.items()))
    steady_gib = sum(datagen_by_source.values()) + database_gib
    return DiskBudget(
        cells=len(cells_tuple),
        est_peak_gib=steady_gib + transient_gib,
        est_steady_gib=steady_gib,
        unknown_cells=tuple(unknown),
        database_by_platform_gib=platform_totals,
        concurrent_database_gib=database_gib,
        chunked_database_gib=max(database_by_platform.values(), default=0.0),
        platforms_total=len({cell.platform for cell in cells_tuple}),
        platforms_with_measured_database=len(database_by_platform),
    )


def largest_scale_cells(config: UATConfig) -> tuple[Cell, ...]:
    cells_by_scale: dict[float, list[Cell]] = {}
    for cell in enumerate_cells(config):
        cells_by_scale.setdefault(cell.scale, []).append(cell)
    if not cells_by_scale:
        return ()
    return tuple(cells_by_scale[max(cells_by_scale)])


@dataclass(frozen=True)
class DiskBudgetCoverage:
    cells_total: int
    cells_with_rows: int
    cells_with_measured_database: int
    platforms_total: int
    measured_platforms: tuple[str, ...]
    unmeasured_platforms: tuple[str, ...]

    @property
    def is_lower_bound(self) -> bool:
        return self.cells_with_rows < self.cells_total or self.cells_with_measured_database < self.cells_total


def assess_budget_coverage(cells: Iterable[Cell], *, table: BudgetTable) -> DiskBudgetCoverage:
    cells_tuple = tuple(cells)
    platforms = sorted({cell.platform for cell in cells_tuple})
    cells_with_rows = 0
    cells_with_measured_database = 0
    unmeasured: set[str] = set()
    for cell in cells_tuple:
        row = table.get((cell.platform, cell.benchmark, cell.scale))
        if row is None:
            unmeasured.add(cell.platform)
            continue
        cells_with_rows += 1
        if row.database_measured:
            cells_with_measured_database += 1
        else:
            unmeasured.add(cell.platform)
    return DiskBudgetCoverage(
        cells_total=len(cells_tuple),
        cells_with_rows=cells_with_rows,
        cells_with_measured_database=cells_with_measured_database,
        platforms_total=len(platforms),
        measured_platforms=tuple(p for p in platforms if p not in unmeasured),
        unmeasured_platforms=tuple(p for p in platforms if p in unmeasured),
    )


_MAX_LISTED_PLATFORMS = 6


def format_budget_coverage(coverage: DiskBudgetCoverage) -> str:
    if coverage.cells_total == 0:
        return "Disk budget coverage: no cells enumerated for this config; nothing measured and nothing gated"
    if not coverage.is_lower_bound:
        return (
            "Disk budget coverage: COMPLETE -- measured rows cover all "
            f"{coverage.cells_total} largest-scale cell(s) across all "
            f"{coverage.platforms_total} platform(s)"
        )
    listed = coverage.unmeasured_platforms[:_MAX_LISTED_PLATFORMS]
    remainder = len(coverage.unmeasured_platforms) - len(listed)
    names = ", ".join(listed) + (f", +{remainder} more" if remainder > 0 else "")
    return (
        "Disk budget coverage: PARTIAL -- this estimate is a LOWER BOUND, not a certification that the "
        f"sweep fits. Measured rows cover {len(coverage.measured_platforms)} of {coverage.platforms_total} "
        f"platform(s); {coverage.cells_with_rows} of {coverage.cells_total} largest-scale cell(s) have any "
        f"row and {coverage.cells_with_measured_database} of {coverage.cells_total} have a measured "
        f"loaded-database footprint. Unmeasured platform(s): {names}"
    )


def format_budget_verdict(check: DiskHeadroomCheck, coverage: DiskBudgetCoverage) -> str:
    if coverage.cells_total == 0:
        return (
            "Disk budget verdict: no cells enumerated for this config; the "
            f"{check.required_gib:.2f} GiB requirement above reflects the configured "
            "floor only, not a measured or estimated disk footprint"
        )
    if coverage.is_lower_bound:
        return (
            f"Disk budget verdict: no shortfall detected against a lower-bound requirement of "
            f"{check.required_gib:.2f} GiB; real demand may be higher (see coverage above)"
        )
    return f"Disk budget verdict: measured requirement of {check.required_gib:.2f} GiB fits every required root"


def check_disk_headroom(
    budget: DiskBudget,
    roots: Iterable[DiskRootFreeSpace],
    *,
    min_free_gib: float,
    platform_chunking: bool = False,
) -> DiskHeadroomCheck:
    peak_gib = budget.est_peak_gib
    if platform_chunking:
        peak_gib += budget.chunked_database_gib - budget.concurrent_database_gib
    required_gib = max(min_free_gib, peak_gib)
    shortfalls = tuple(
        DiskHeadroomShortfall(root.label, root.path, root.free_gib, required_gib)
        for root in roots
        if root.free_gib < required_gib
    )
    return DiskHeadroomCheck(required_gib=required_gib, shortfalls=shortfalls)


def format_disk_budget(budget: DiskBudget) -> str:
    return (
        "Disk budget estimate: "
        f"{budget.est_peak_gib:.2f} GiB peak "
        f"({budget.est_steady_gib:.2f} GiB steady; "
        f"cells={budget.cells}; unknown={len(budget.unknown_cells)}; "
        f"measured-db platforms={budget.platforms_with_measured_database}/{budget.platforms_total}; "
        f"database concurrent={budget.concurrent_database_gib:.2f} GiB "
        f"chunked_max={budget.chunked_database_gib:.2f} GiB; "
        "basis=measured inventory rows at configured rungs, not a guessed per-platform constant)"
    )


def format_disk_headroom_failure(check: DiskHeadroomCheck, budget: DiskBudget | None = None) -> str:
    details = "; ".join(
        f"{shortfall.label} {shortfall.path}: "
        f"{shortfall.free_gib:.1f} GiB free < {shortfall.required_gib:.1f} GiB required"
        for shortfall in check.shortfalls
    )
    message = f"disk headroom gate failed: {details}"
    if budget is None or not check.shortfalls:
        return message
    worst = min(check.shortfalls, key=lambda shortfall: shortfall.free_gib - shortfall.required_gib)
    shortfall_gib = worst.required_gib - worst.free_gib
    return (
        f"{message}; computed shortfall {shortfall_gib:.1f} GiB "
        f"(measured-db platforms={budget.platforms_with_measured_database}/{budget.platforms_total}, "
        f"database concurrent={budget.concurrent_database_gib:.2f} GiB, "
        f"chunked_max={budget.chunked_database_gib:.2f} GiB; "
        "failing now rather than exhausting disk mid-sweep)"
    )
