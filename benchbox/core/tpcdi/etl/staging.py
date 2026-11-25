from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from benchbox.utils.clock import elapsed_seconds, mono_time
from benchbox.utils.dialect_utils import split_sql_statements, translate_sql_query
from benchbox.utils.duckdb_extensions import THIRD_PARTY_EXTENSION_OPTION, install_webbed

if TYPE_CHECKING:
    from benchbox.core.tpcdi.etl.historical import TransformStep

ETL_DIR = Path(__file__).resolve().parent
INGEST_DIR = ETL_DIR / "ingest"
SOURCE_DIALECT = "duckdb"
BATCH_ID = 1

XML_REFUSAL_MESSAGE = (
    'TPC-DI on DuckDB reads CustomerMgmt.xml with the community extension "webbed". '
    "Allow third-party extensions ({setting}) to continue."
)


class ThirdPartyExtensionRequired(RuntimeError):
    pass


@dataclass(frozen=True)
class IngestStep:
    name: str
    table: str
    parameter: str
    source: str
    loads_into_existing_table: bool
    requires_xml: bool = False

    @property
    def path(self) -> Path:
        return INGEST_DIR / f"{self.name}.sql"


@dataclass(frozen=True)
class StepResult:
    step: IngestStep | TransformStep
    rows: int
    seconds: float


INGEST_STEPS: tuple[IngestStep, ...] = (
    IngestStep("dim_date", "DimDate", "date_path", "Date.txt", True),
    IngestStep("dim_time", "DimTime", "time_path", "Time.txt", True),
    IngestStep("industry", "Industry", "industry_path", "Industry.txt", True),
    IngestStep("status_type", "StatusType", "status_type_path", "StatusType.txt", True),
    IngestStep("tax_rate", "TaxRate", "tax_rate_path", "TaxRate.txt", True),
    IngestStep("trade_type", "TradeType", "trade_type_path", "TradeType.txt", True),
    IngestStep("batch_date", "BatchDate", "batch_date_path", "BatchDate.txt", True),
    IngestStep("dim_broker", "DimBroker", "hr_path", "HR.csv", True),
    IngestStep("finwire", "FinWire", "finwire_path", "FINWIRE[0-9][0-9][0-9][0-9]Q[1-4]", True),
    IngestStep("customer_mgmt", "CustomerMgmtRaw", "customer_mgmt_path", "CustomerMgmt.xml", False, requires_xml=True),
    IngestStep("prospect", "ProspectRaw", "prospect_path", "Prospect.csv", False),
    IngestStep("trade", "TradeRaw", "trade_path", "Trade.txt", False),
    IngestStep("trade_history", "TradeHistoryRaw", "trade_history_path", "TradeHistory.txt", False),
    IngestStep("holding_history", "HoldingHistoryRaw", "holding_history_path", "HoldingHistory.txt", False),
    IngestStep("watch_history", "WatchHistoryRaw", "watch_history_path", "WatchHistory.txt", False),
    IngestStep("cash_transaction", "CashTransactionRaw", "cash_transaction_path", "CashTransaction.txt", False),
    IngestStep("daily_market", "DailyMarketRaw", "daily_market_path", "DailyMarket.txt", False),
)

STEPS_BY_NAME = {step.name: step for step in INGEST_STEPS}
STEPS_BY_TABLE = {step.table: step for step in INGEST_STEPS}


def xml_refusal_message() -> str:
    return XML_REFUSAL_MESSAGE.format(setting=THIRD_PARTY_EXTENSION_OPTION)


def batch_directory(source_dir: str | Path) -> Path:
    return Path(source_dir) / f"Batch{BATCH_ID}"


def step_parameters(source_dir: str | Path, step: IngestStep) -> dict[str, str]:
    return {step.parameter: str(batch_directory(source_dir).resolve() / step.source)}


def render_step(step: IngestStep | TransformStep, platform: str = SOURCE_DIALECT) -> list[str]:
    sql = translate_sql_query(
        step.path.read_text(encoding="utf-8"),
        platform,
        source_dialect=SOURCE_DIALECT,
        identify=False,
        strict=True,
    )
    return split_sql_statements(sql, platform)


def _count(connection: Any, table: str) -> int:
    return int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])


def run_step(
    connection: Any,
    step: IngestStep | TransformStep,
    platform: str,
    parameters: dict[str, str],
    *,
    table_exists: bool,
) -> StepResult:
    before = _count(connection, step.table) if table_exists else 0
    statements = render_step(step, platform)
    started = mono_time()
    for statement in statements:
        connection.execute(statement, parameters)
    seconds = elapsed_seconds(started)
    return StepResult(step, _count(connection, step.table) - before, seconds)


def _run_ingest_step(connection: Any, source_dir: str | Path, step: IngestStep, platform: str) -> StepResult:
    parameters = step_parameters(source_dir, step)
    return run_step(connection, step, platform, parameters, table_exists=step.loads_into_existing_table)


def load_batch1(
    connection: Any,
    source_dir: str | Path,
    *,
    allow_third_party_extensions: bool,
    platform: str = SOURCE_DIALECT,
    steps: Sequence[IngestStep] = INGEST_STEPS,
) -> list[StepResult]:
    if any(step.requires_xml for step in steps):
        if not allow_third_party_extensions:
            raise ThirdPartyExtensionRequired(xml_refusal_message())
        install_webbed(connection)
    return [_run_ingest_step(connection, source_dir, step, platform) for step in steps]
