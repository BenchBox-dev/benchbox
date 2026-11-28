from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from benchbox.core.tpcdi.etl.historical import TransformStep
from benchbox.core.tpcdi.etl.staging import ETL_DIR, SOURCE_DIALECT, STEPS_BY_NAME, IngestStep, StepResult, run_step

INCREMENTAL_DIR = ETL_DIR / "incremental"
INCREMENTAL_INGEST_DIR = INCREMENTAL_DIR / "ingest"

INCREMENTAL_INGEST_STEPS: tuple[IngestStep, ...] = (
    STEPS_BY_NAME["batch_date"],
    STEPS_BY_NAME["prospect"],
    IngestStep("customer", "CustomerRaw", "customer_path", "Customer.txt", False, directory=INCREMENTAL_INGEST_DIR),
    IngestStep("account", "AccountRaw", "account_path", "Account.txt", False, directory=INCREMENTAL_INGEST_DIR),
    IngestStep("trade", "TradeRaw", "trade_path", "Trade.txt", False, directory=INCREMENTAL_INGEST_DIR),
    IngestStep(
        "cash_transaction",
        "CashTransactionRaw",
        "cash_transaction_path",
        "CashTransaction.txt",
        False,
        directory=INCREMENTAL_INGEST_DIR,
    ),
    IngestStep(
        "holding_history",
        "HoldingHistoryRaw",
        "holding_history_path",
        "HoldingHistory.txt",
        False,
        directory=INCREMENTAL_INGEST_DIR,
    ),
    IngestStep(
        "watch_history",
        "WatchHistoryRaw",
        "watch_history_path",
        "WatchHistory.txt",
        False,
        directory=INCREMENTAL_INGEST_DIR,
    ),
    IngestStep(
        "daily_market",
        "DailyMarketRaw",
        "daily_market_path",
        "DailyMarket.txt",
        False,
        directory=INCREMENTAL_INGEST_DIR,
    ),
)

INCREMENTAL_STEPS: tuple[TransformStep, ...] = (
    TransformStep("dim_customer", "DimCustomer", INCREMENTAL_DIR, atomic=True),
    TransformStep("dim_account", "DimAccount", INCREMENTAL_DIR, atomic=True),
    TransformStep("prospect", "Prospect", INCREMENTAL_DIR, atomic=True),
    TransformStep("dim_trade", "DimTrade", INCREMENTAL_DIR, atomic=True),
    TransformStep("fact_cash_balances", "FactCashBalances", INCREMENTAL_DIR),
    TransformStep("fact_holdings", "FactHoldings", INCREMENTAL_DIR),
    TransformStep("fact_watches", "FactWatches", INCREMENTAL_DIR, atomic=True),
    TransformStep("fact_market_history", "FactMarketHistory", INCREMENTAL_DIR),
)


def load_incremental(
    connection: Any,
    batch_id: int,
    *,
    platform: str = SOURCE_DIALECT,
    steps: Sequence[TransformStep] = INCREMENTAL_STEPS,
) -> list[StepResult]:
    return [run_step(connection, step, platform, {"batch_id": batch_id}, table_exists=True) for step in steps]
