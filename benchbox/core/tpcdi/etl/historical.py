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

    @property
    def path(self) -> Path:
        return HISTORICAL_DIR / f"{self.name}.sql"


HISTORICAL_STEPS: tuple[TransformStep, ...] = (
    TransformStep("dim_company", "DimCompany"),
    TransformStep("dim_security", "DimSecurity"),
    TransformStep("financial", "Financial"),
    TransformStep("dim_customer", "DimCustomer"),
    TransformStep("dim_account", "DimAccount"),
    TransformStep("prospect", "Prospect"),
    TransformStep("dim_trade", "DimTrade"),
    TransformStep("fact_cash_balances", "FactCashBalances"),
    TransformStep("fact_holdings", "FactHoldings"),
    TransformStep("fact_watches", "FactWatches"),
    TransformStep("fact_market_history", "FactMarketHistory"),
)


def load_historical(
    connection: Any,
    *,
    platform: str = SOURCE_DIALECT,
    steps: Sequence[TransformStep] = HISTORICAL_STEPS,
) -> list[StepResult]:
    return [run_step(connection, step, platform, {}, table_exists=True) for step in steps]
