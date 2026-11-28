from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchbox.core.tpcdi.etl.staging import ETL_DIR, SOURCE_DIALECT, StepResult, run_step

HISTORICAL_DIR = ETL_DIR / "historical"


@dataclass(frozen=True)
class TransformStep:
    name: str
    table: str
    directory: Path
    atomic: bool = False

    @property
    def path(self) -> Path:
        return self.directory / f"{self.name}.sql"


HISTORICAL_STEPS: tuple[TransformStep, ...] = (
    TransformStep("dim_company", "DimCompany", HISTORICAL_DIR),
    TransformStep("dim_security", "DimSecurity", HISTORICAL_DIR),
    TransformStep("financial", "Financial", HISTORICAL_DIR),
    TransformStep("dim_customer", "DimCustomer", HISTORICAL_DIR),
    TransformStep("dim_account", "DimAccount", HISTORICAL_DIR),
    TransformStep("prospect", "Prospect", HISTORICAL_DIR),
    TransformStep("dim_trade", "DimTrade", HISTORICAL_DIR),
    TransformStep("fact_cash_balances", "FactCashBalances", HISTORICAL_DIR),
    TransformStep("fact_holdings", "FactHoldings", HISTORICAL_DIR),
    TransformStep("fact_watches", "FactWatches", HISTORICAL_DIR),
    TransformStep("fact_market_history", "FactMarketHistory", HISTORICAL_DIR),
)


def load_historical(
    connection: Any,
    *,
    platform: str = SOURCE_DIALECT,
    steps: Sequence[TransformStep] = HISTORICAL_STEPS,
) -> list[StepResult]:
    return [run_step(connection, step, platform, {}, table_exists=True) for step in steps]
