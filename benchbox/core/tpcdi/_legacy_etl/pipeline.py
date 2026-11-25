from __future__ import annotations

import logging
import random
import re
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from .results import ETLBatchResult, ETLPhaseResult
from .sql_backend import bulk_insert_dataframe

_INSERT_TARGET = re.compile(r"INSERT\s+INTO\s+(\w+)\s*\(([^)]*)\)", re.IGNORECASE)

logger = logging.getLogger(__name__)


class TPCDIETLPipeline:
    def __init__(self, connection: Any, benchmark: Any, dialect: str = "duckdb"):
        self.connection = connection
        self.benchmark = benchmark
        self.dialect = dialect

    def _bulk_insert(self, insert_sql: str, records: list[tuple[Any, ...]]) -> None:
        match = _INSERT_TARGET.search(insert_sql)
        if match is None:
            raise ValueError("Unsupported INSERT statement for bulk load")
        columns = [column.strip() for column in match.group(2).split(",")]
        frame = pd.DataFrame(records, columns=columns)
        if not bulk_insert_dataframe(self.connection, match.group(1), columns, frame):
            self.connection.executemany(insert_sql, records)

    def run_historical_load(self, scale_factor: float = 1.0) -> ETLPhaseResult:
        logger.info(f"Starting TPC-DI historical load (scale factor: {scale_factor})")

        phase_result = ETLPhaseResult(phase_name="Historical Load", start_time=datetime.now())

        try:
            batch_result = ETLBatchResult(
                batch_id=1,
                batch_date=date(2023, 1, 1),
                start_time=datetime.now(),
                end_time=datetime.now(),
                execution_time=0.0,
            )

            logger.info("Loading dimension tables...")
            dim_records = self._load_dimension_tables(batch_result, scale_factor)

            logger.info("Loading fact tables...")
            fact_records = self._load_fact_tables(batch_result, scale_factor)

            logger.info("Creating indexes...")
            self._create_performance_indexes()

            batch_result.end_time = datetime.now()
            batch_result.execution_time = (batch_result.end_time - batch_result.start_time).total_seconds()
            batch_result.records_processed = dim_records + fact_records
            batch_result.records_inserted = batch_result.records_processed
            batch_result.success = True

            phase_result.add_batch_result(batch_result)
            phase_result.end_time = datetime.now()
            phase_result.total_execution_time = (phase_result.end_time - phase_result.start_time).total_seconds()
            phase_result.success = True

            logger.info(
                f"Historical load completed: {batch_result.records_processed:,} records in {batch_result.execution_time:.2f}s"
            )

        except Exception as e:
            logger.error(f"Historical load failed: {e}")
            phase_result.success = False
            if phase_result.batches:
                phase_result.batches[-1].success = False
                phase_result.batches[-1].error_message = str(e)

        return phase_result

    def run_incremental_load(self, batch_id: int, scale_factor: float = 1.0) -> ETLPhaseResult:
        logger.info(f"Starting TPC-DI incremental load batch {batch_id}")

        phase_result = ETLPhaseResult(phase_name=f"Incremental Load {batch_id}", start_time=datetime.now())

        try:
            base_date = date(2023, 1, 1)
            batch_date = base_date + timedelta(days=batch_id)

            batch_result = ETLBatchResult(
                batch_id=batch_id,
                batch_date=batch_date,
                start_time=datetime.now(),
                end_time=datetime.now(),
                execution_time=0.0,
            )

            logger.info("Processing dimension changes...")
            dim_changes = self._process_dimension_changes(batch_result, batch_date, scale_factor)

            logger.info("Processing fact increments...")
            fact_changes = self._process_fact_increments(batch_result, batch_date, scale_factor)

            batch_result.end_time = datetime.now()
            batch_result.execution_time = (batch_result.end_time - batch_result.start_time).total_seconds()
            batch_result.records_processed = dim_changes + fact_changes
            batch_result.success = True

            phase_result.add_batch_result(batch_result)
            phase_result.end_time = datetime.now()
            phase_result.total_execution_time = (phase_result.end_time - phase_result.start_time).total_seconds()
            phase_result.success = True

            logger.info(f"Incremental load batch {batch_id} completed: {batch_result.records_processed:,} records")

        except Exception as e:
            logger.error(f"Incremental load batch {batch_id} failed: {e}")
            phase_result.success = False
            if phase_result.batches:
                phase_result.batches[-1].success = False
                phase_result.batches[-1].error_message = str(e)

        return phase_result

    def run_scd_processing(self, connection: Any, table_name: str, batch_id: int) -> int:
        logger.info(f"Processing SCD Type 2 for {table_name}")

        try:
            update_sql = f"""
                UPDATE {table_name}
                SET EndDate = CURRENT_DATE - 1,
                    IsCurrent = FALSE
                WHERE IsCurrent = TRUE
                AND RANDOM() < 0.1
            """

            if hasattr(connection, "execute"):
                connection.execute(update_sql)
            else:
                connection.query(update_sql)

            count_sql = f"SELECT COUNT(*) FROM {table_name} WHERE BatchID = {batch_id}"
            count_result = connection.execute(count_sql).fetchone()
            return count_result[0] if count_result else 0

        except Exception as e:
            logger.error(f"SCD processing failed for {table_name}: {e}")
            return 0

    def _load_dimension_tables(self, batch_result: ETLBatchResult, scale_factor: float) -> int:
        total_records = 0

        try:
            total_records = self._generate_synthetic_dimension_data(scale_factor)

        except Exception as e:
            logger.error(f"Dimension table loading failed: {e}")
            batch_result.error_message = str(e)

        return total_records

    def _load_fact_tables(self, batch_result: ETLBatchResult, scale_factor: float) -> int:
        total_records = 0

        try:
            total_records = self._generate_synthetic_fact_data(scale_factor)

        except Exception as e:
            logger.error(f"Fact table loading failed: {e}")
            batch_result.error_message = str(e)

        return total_records

    def _process_dimension_changes(self, batch_result: ETLBatchResult, batch_date: date, scale_factor: float) -> int:
        total_changes = 0

        try:
            dimension_tables = [
                "DimCustomer",
                "DimAccount",
                "DimSecurity",
                "DimCompany",
            ]

            for table_name in dimension_tables:
                changes = self.run_scd_processing(self.connection, table_name, batch_result.batch_id)
                total_changes += changes

                if changes > 0:
                    batch_result.records_updated += changes
                    logger.info(f"Processed {changes} SCD changes in {table_name}")

        except Exception as e:
            logger.error(f"Dimension change processing failed: {e}")
            batch_result.error_message = str(e)

        return total_changes

    def _process_fact_increments(self, batch_result: ETLBatchResult, batch_date: date, scale_factor: float) -> int:
        total_increments = 0

        try:
            increment_factor = scale_factor * 0.1

            num_trades = int(1000 * increment_factor)

            if num_trades > 0:
                insert_sql = f"""
                    INSERT INTO FactTrade (
                        TradeID, SK_BrokerID, SK_CreateDateID, SK_CreateTimeID,
                        SK_CloseDateID, SK_CloseTimeID, Status, Type, CashFlag,
                        SK_SecurityID, SK_CompanyID, Quantity, BidPrice, SK_CustomerID,
                        SK_AccountID, ExecutedBy, TradePrice, Fee, Commission, Tax, BatchID
                    )
                    SELECT
                        ROW_NUMBER() OVER () + (SELECT COALESCE(MAX(TradeID), 0) FROM FactTrade) as TradeID,
                        1 as SK_BrokerID,
                        d.SK_DateID as SK_CreateDateID,
                        t.SK_TimeID as SK_CreateTimeID,
                        d2.SK_DateID as SK_CloseDateID,
                        t2.SK_TimeID as SK_CloseTimeID,
                        'COMPLETED' as Status,
                        'MARKET' as Type,
                        FALSE as CashFlag,
                        s.SK_SecurityID,
                        co.SK_CompanyID,
                        ABS(RANDOM()) % 10000 + 100 as Quantity,
                        (RANDOM() * 190 + 10)::DECIMAL(8,2) as BidPrice,
                        c.SK_CustomerID,
                        a.SK_AccountID,
                        'System' as ExecutedBy,
                        (RANDOM() * 190 + 10)::DECIMAL(8,2) as TradePrice,
                        5.00 as Fee,
                        10.00 as Commission,
                        0.00 as Tax,
                        {batch_result.batch_id} as BatchID
                    FROM
                        (SELECT ROW_NUMBER() OVER () as rn FROM DimCustomer LIMIT {num_trades}) gen
                        CROSS JOIN (SELECT SK_CustomerID FROM DimCustomer WHERE IsCurrent = TRUE ORDER BY RANDOM() LIMIT 1) c
                        CROSS JOIN (SELECT SK_AccountID FROM DimAccount WHERE IsCurrent = TRUE ORDER BY RANDOM() LIMIT 1) a
                        CROSS JOIN (SELECT SK_SecurityID FROM DimSecurity WHERE IsCurrent = TRUE ORDER BY RANDOM() LIMIT 1) s
                        CROSS JOIN (SELECT SK_CompanyID FROM DimCompany WHERE IsCurrent = TRUE ORDER BY RANDOM() LIMIT 1) co
                        CROSS JOIN (SELECT SK_DateID FROM DimDate ORDER BY RANDOM() LIMIT 1) d
                        CROSS JOIN (SELECT SK_TimeID FROM DimTime ORDER BY RANDOM() LIMIT 1) t
                        CROSS JOIN (SELECT SK_DateID FROM DimDate ORDER BY RANDOM() LIMIT 1) d2
                        CROSS JOIN (SELECT SK_TimeID FROM DimTime ORDER BY RANDOM() LIMIT 1) t2
                    LIMIT {num_trades}
                """

                self.connection.execute(insert_sql)
                total_increments = num_trades
                batch_result.records_inserted += num_trades

                logger.info(f"Inserted {num_trades:,} incremental trades with valid foreign keys")

        except Exception as e:
            logger.error(f"Fact increment processing failed: {e}")
            batch_result.error_message = str(e)

        return total_increments

    def _create_performance_indexes(self) -> None:
        try:
            index_sqls = [
                "CREATE INDEX IF NOT EXISTS idx_customer_id ON DimCustomer(CustomerID)",
                "CREATE INDEX IF NOT EXISTS idx_account_customer ON DimAccount(SK_CustomerID)",
                "CREATE INDEX IF NOT EXISTS idx_trade_customer ON FactTrade(SK_CustomerID)",
                "CREATE INDEX IF NOT EXISTS idx_trade_account ON FactTrade(SK_AccountID)",
                "CREATE INDEX IF NOT EXISTS idx_trade_security ON FactTrade(SK_SecurityID)",
                "CREATE INDEX IF NOT EXISTS idx_trade_date ON FactTrade(SK_CreateDateID)",
            ]

            for sql in index_sqls:
                try:
                    self.connection.execute(sql)
                except Exception as e:
                    logger.warning(f"Index creation failed: {sql} - {e}")

        except Exception as e:
            logger.error(f"Index creation failed: {e}")

    def _generate_synthetic_dimension_data(self, scale_factor: float) -> int:
        total_records = 0

        try:
            date_records = self._generate_date_dimension()
            total_records += date_records

            time_records = self._generate_time_dimension()
            total_records += time_records

            customer_records = self._generate_customer_dimension(max(1, int(100 * scale_factor)))
            total_records += customer_records

            company_records = self._generate_company_dimension(max(1, int(20 * scale_factor)))
            total_records += company_records

            security_records = self._generate_security_dimension(max(1, int(50 * scale_factor)))
            total_records += security_records

            account_records = self._generate_account_dimension(max(1, int(150 * scale_factor)))
            total_records += account_records

            logger.info(f"Generated synthetic dimension data: {total_records:,} records")

        except Exception as e:
            logger.error(f"Synthetic dimension data generation failed: {e}")

        return total_records

    def _generate_synthetic_fact_data(self, scale_factor: float) -> int:
        try:
            trade_records = self._generate_trade_fact(max(1, int(1000 * scale_factor)))
            logger.info(f"Generated synthetic fact data: {trade_records:,} records")
            return trade_records
        except Exception as e:
            logger.error(f"Synthetic fact data generation failed: {e}")
            return 0

    def _generate_date_dimension(self) -> int:
        try:
            insert_sql = """
                INSERT INTO DimDate (
                    SK_DateID, DateValue, DateDesc, CalendarYearID, CalendarYearDesc,
                    CalendarQtrID, CalendarQtrDesc, CalendarMonthID, CalendarMonthDesc,
                    CalendarWeekID, CalendarWeekDesc, DayOfWeekNum, DayOfWeekDesc,
                    FiscalYearID, FiscalYearDesc, FiscalQtrID, FiscalQtrDesc, HolidayFlag
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """

            records = []
            for day in range(1, 31):
                records.append(
                    (
                        day,
                        f"2023-01-{day:02d}",
                        f"Day {day}",
                        2023,
                        "2023",
                        1,
                        "Q1 2023",
                        1,
                        "Jan 2023",
                        1,
                        "Week 1",
                        1,
                        "Monday",
                        2023,
                        "FY2023",
                        1,
                        "FQ1 2023",
                        False,
                    )
                )

            self._bulk_insert(insert_sql, records)
            return len(records)
        except Exception as e:
            logger.error(f"Date dimension generation failed: {e}")
            return 0

    def _generate_time_dimension(self) -> int:
        try:
            insert_sql = """
                INSERT INTO DimTime (
                    SK_TimeID, TimeValue, HourID, HourDesc, MinuteID, MinuteDesc,
                    SecondID, SecondDesc, MarketHoursFlag, OfficeHoursFlag
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """

            records = []
            for hour in range(24):
                for minute in [0, 30]:
                    time_id = hour * 100 + minute
                    records.append(
                        (
                            time_id,
                            f"{hour:02d}:{minute:02d}:00",
                            hour,
                            f"Hour {hour}",
                            minute,
                            f"Minute {minute}",
                            0,
                            "Second 0",
                            9 <= hour <= 16,
                            8 <= hour <= 17,
                        )
                    )

            self._bulk_insert(insert_sql, records)
            return len(records)
        except Exception as e:
            logger.error(f"Time dimension generation failed: {e}")
            return 0

    def _generate_customer_dimension(self, count: int) -> int:
        try:
            insert_sql = """
                INSERT INTO DimCustomer (
                    SK_CustomerID, CustomerID, TaxID, Status, LastName, FirstName,
                    MiddleInitial, Gender, Tier, DOB, AddressLine1, AddressLine2,
                    PostalCode, City, StateProv, Country, Phone1, Phone2, Phone3,
                    Email1, Email2, NationalTaxRateDesc, NationalTaxRate,
                    LocalTaxRateDesc, LocalTaxRate, AgencyID, CreditRating,
                    NetWorth, MarketingNameplate, IsCurrent, BatchID,
                    EffectiveDate, EndDate
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """

            records = []
            for i in range(1, count + 1):
                records.append(
                    (
                        i,
                        i,
                        f"TAX{i:06d}",
                        "ACTIVE",
                        f"Customer{i}",
                        f"First{i}",
                        "M",
                        random.choice(["M", "F"]),
                        random.choice([1, 2, 3]),
                        "1980-01-01",
                        f"{i} Main St",
                        None,
                        "12345",
                        "City",
                        "State",
                        "Country",
                        f"555-{i:04d}",
                        None,
                        None,
                        f"customer{i}@example.com",
                        None,
                        "Standard Rate",
                        0.25,
                        "Local Rate",
                        0.05,
                        "AGY001",
                        random.randint(1, 10),
                        random.randint(10000, 1000000),
                        "Standard",
                        True,
                        1,
                        "2023-01-01",
                        "9999-12-31",
                    )
                )

            self._bulk_insert(insert_sql, records)
            return len(records)
        except Exception as e:
            logger.error(f"Customer dimension generation failed: {e}")
            return 0

    def _generate_company_dimension(self, count: int) -> int:
        try:
            insert_sql = """
                INSERT INTO DimCompany (
                    SK_CompanyID, CompanyID, Status, Name, Industry, SPrating, isLowGrade,
                    CEO, AddressLine1, AddressLine2, PostalCode, City, StateProv, Country,
                    Description, FoundingDate, IsCurrent, BatchID, EffectiveDate, EndDate
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """

            records = []
            industries = [
                "Technology",
                "Healthcare",
                "Finance",
                "Manufacturing",
                "Retail",
            ]
            for i in range(1, count + 1):
                records.append(
                    (
                        i,
                        i,
                        "ACTIVE",
                        f"Company {i}",
                        random.choice(industries),
                        "AAA",
                        False,
                        f"CEO {i}",
                        f"{i} Corporate Blvd",
                        None,
                        "54321",
                        "Corp City",
                        "Corp State",
                        "USA",
                        f"Description for Company {i}",
                        "2000-01-01",
                        True,
                        1,
                        "2023-01-01",
                        "9999-12-31",
                    )
                )

            self._bulk_insert(insert_sql, records)
            return len(records)
        except Exception as e:
            logger.error(f"Company dimension generation failed: {e}")
            return 0

    def _generate_security_dimension(self, count: int) -> int:
        try:
            insert_sql = """
                INSERT INTO DimSecurity (
                    SK_SecurityID, Symbol, Issue, Status, Name, ExchangeID, SK_CompanyID,
                    SharesOutstanding, FirstTrade, FirstTradeOnExchange, Dividend,
                    IsCurrent, BatchID, EffectiveDate, EndDate
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """

            records = []
            for i in range(1, count + 1):
                records.append(
                    (
                        i,
                        f"SYM{i:03d}",
                        "COM",
                        "ACTIVE",
                        f"Security {i}",
                        "NYSE",
                        ((i - 1) % 20) + 1,
                        1000000,
                        "2020-01-01",
                        "2020-01-01",
                        1.50,
                        True,
                        1,
                        "2023-01-01",
                        "9999-12-31",
                    )
                )

            self._bulk_insert(insert_sql, records)
            return len(records)
        except Exception as e:
            logger.error(f"Security dimension generation failed: {e}")
            return 0

    def _generate_account_dimension(self, count: int) -> int:
        try:
            insert_sql = """
                INSERT INTO DimAccount (
                    SK_AccountID, AccountID, SK_BrokerID, SK_CustomerID, Status,
                    AccountDesc, TaxStatus, IsCurrent, BatchID, EffectiveDate, EndDate
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """

            records = []
            for i in range(1, count + 1):
                records.append(
                    (
                        i,
                        i,
                        1,
                        ((i - 1) % 100) + 1,
                        "ACTIVE",
                        f"Account {i}",
                        1,
                        True,
                        1,
                        "2023-01-01",
                        "9999-12-31",
                    )
                )

            self._bulk_insert(insert_sql, records)
            return len(records)
        except Exception as e:
            logger.error(f"Account dimension generation failed: {e}")
            return 0

    def _generate_trade_fact(self, count: int) -> int:
        try:
            try:
                customer_keys = self.connection.execute(
                    "SELECT SK_CustomerID FROM DimCustomer WHERE IsCurrent = TRUE"
                ).fetchall()
                account_keys = self.connection.execute(
                    "SELECT SK_AccountID FROM DimAccount WHERE IsCurrent = TRUE"
                ).fetchall()
                security_keys = self.connection.execute(
                    "SELECT SK_SecurityID FROM DimSecurity WHERE IsCurrent = TRUE"
                ).fetchall()
                company_keys = self.connection.execute(
                    "SELECT SK_CompanyID FROM DimCompany WHERE IsCurrent = TRUE"
                ).fetchall()
                date_keys = self.connection.execute("SELECT SK_DateID FROM DimDate").fetchall()
                time_keys = self.connection.execute("SELECT SK_TimeID FROM DimTime").fetchall()

                customer_ids = [row[0] for row in customer_keys] if customer_keys else [1]
                account_ids = [row[0] for row in account_keys] if account_keys else [1]
                security_ids = [row[0] for row in security_keys] if security_keys else [1]
                company_ids = [row[0] for row in company_keys] if company_keys else [1]
                date_ids = [row[0] for row in date_keys] if date_keys else [1]
                time_ids = [row[0] for row in time_keys] if time_keys else [1]

            except Exception as e:
                logger.warning(f"Could not retrieve dimension keys, using defaults: {e}")
                customer_ids = list(range(1, 11))
                account_ids = list(range(1, 11))
                security_ids = list(range(1, 6))
                company_ids = list(range(1, 3))
                date_ids = list(range(1, 31))
                time_ids = [
                    100,
                    200,
                    300,
                    400,
                    500,
                    600,
                    700,
                    800,
                    900,
                    1000,
                    1100,
                    1200,
                    1300,
                    1400,
                    1500,
                    1600,
                ]

            insert_sql = """
                INSERT INTO FactTrade (
                    TradeID, SK_BrokerID, SK_CreateDateID, SK_CreateTimeID,
                    SK_CloseDateID, SK_CloseTimeID, Status, Type, CashFlag,
                    SK_SecurityID, SK_CompanyID, Quantity, BidPrice, SK_CustomerID,
                    SK_AccountID, ExecutedBy, TradePrice, Fee, Commission, Tax, BatchID
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """

            records = []
            for i in range(1, count + 1):
                create_date_id = random.choice(date_ids)
                create_time_id = random.choice(time_ids)
                close_date_id = random.choice(date_ids)
                close_time_id = random.choice(time_ids)

                records.append(
                    (
                        i,
                        1,
                        create_date_id,
                        create_time_id,
                        close_date_id,
                        close_time_id,
                        "COMPLETED",
                        "MARKET",
                        False,
                        random.choice(security_ids),
                        random.choice(company_ids),
                        random.randint(100, 10000),
                        round(random.uniform(10, 200), 2),
                        random.choice(customer_ids),
                        random.choice(account_ids),
                        "System",
                        round(random.uniform(10, 200), 2),
                        5.00,
                        10.00,
                        0.00,
                        1,
                    )
                )

            self._bulk_insert(insert_sql, records)
            logger.info(f"Generated {len(records)} trades with valid foreign key references")
            return len(records)
        except Exception as e:
            logger.error(f"Trade fact generation failed: {e}")
            return 0


__all__ = ["TPCDIETLPipeline"]
