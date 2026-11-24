from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any


@dataclass
class ETLBatchResult:
    batch_id: int
    batch_date: date
    start_time: datetime
    end_time: datetime
    execution_time: float = 0.0
    records_processed: int = 0
    records_inserted: int = 0
    records_updated: int = 0
    records_deleted: int = 0
    success: bool = False
    error_message: str | None = None
    validation_results: dict[str, Any] = field(default_factory=dict)


@dataclass
class ETLPhaseResult:
    phase_name: str
    batches: list[ETLBatchResult] = field(default_factory=list)
    start_time: datetime | None = None
    end_time: datetime | None = None
    total_execution_time: float = 0.0
    total_records_processed: int = 0
    success: bool = False

    def add_batch_result(self, batch: ETLBatchResult) -> None:
        self.batches.append(batch)
        self.total_records_processed += batch.records_processed
        if not batch.success:
            self.success = False


@dataclass
class ETLResult:
    historical_load: ETLPhaseResult | None = None
    incremental_loads: list[ETLPhaseResult] = field(default_factory=list)
    start_time: datetime | None = None
    end_time: datetime | None = None
    total_execution_time: float = 0.0
    total_records_processed: int = 0
    success: bool = False


__all__ = ["ETLBatchResult", "ETLPhaseResult", "ETLResult"]
