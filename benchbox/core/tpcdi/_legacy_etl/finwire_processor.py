# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark™ DI (TPC-DI) - Copyright © Transaction Processing Performance Council
# This implementation is based on the TPC-DI specification.

# Licensed under the MIT License. See LICENSE file in the project root for details.

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class FinWireRecord:
    record_type: str
    company_id: Optional[str] = None
    symbol: Optional[str] = None
    record_date: Optional[date] = None
    raw_data: str = ""

    def __post_init__(self):
        if not self.record_type:
            raise ValueError("Record type is required")


@dataclass
class CompanyFundamentalRecord(FinWireRecord):
    company_name: Optional[str] = None
    industry: Optional[str] = None
    sp_rating: Optional[str] = None
    founding_date: Optional[date] = None
    address_line1: Optional[str] = None
    address_line2: Optional[str] = None
    postal_code: Optional[str] = None
    city: Optional[str] = None
    state_province: Optional[str] = None
    country: Optional[str] = None
    ceo: Optional[str] = None
    description: Optional[str] = None

    market_cap: Optional[Decimal] = None
    revenue: Optional[Decimal] = None
    net_income: Optional[Decimal] = None
    eps: Optional[Decimal] = None
    pe_ratio: Optional[Decimal] = None
    dividend_yield: Optional[Decimal] = None


@dataclass
class SecurityMasterRecord(FinWireRecord):
    security_name: Optional[str] = None
    exchange: Optional[str] = None
    is_active: bool = True
    status: Optional[str] = None
    issue_type: Optional[str] = None
    shares_outstanding: Optional[int] = None
    first_trade_date: Optional[date] = None
    first_trade_exchange: Optional[str] = None
    dividend: Optional[Decimal] = None
    co_name_or_cik: Optional[str] = None


@dataclass
class DailyMarketRecord(FinWireRecord):
    open_price: Optional[Decimal] = None
    high_price: Optional[Decimal] = None
    low_price: Optional[Decimal] = None
    close_price: Optional[Decimal] = None
    volume: Optional[int] = None
    adj_close_price: Optional[Decimal] = None

    fifty_two_week_high: Optional[Decimal] = None
    fifty_two_week_low: Optional[Decimal] = None
    pe_ratio: Optional[Decimal] = None
    yield_pct: Optional[Decimal] = None


@dataclass
class NewsRecord(FinWireRecord):
    headline: Optional[str] = None
    summary: Optional[str] = None
    source: Optional[str] = None
    author: Optional[str] = None
    relevance_score: Optional[Decimal] = None


class FinWireParser:
    CMP_LAYOUT = {
        "pts": (0, 15, str),
        "rec_type": (15, 3, str),
        "company_name": (18, 60, str),
        "cik": (78, 10, str),
        "status": (88, 4, str),
        "industry_id": (92, 2, str),
        "sp_rating": (94, 4, str),
        "founding_date": (98, 8, str),
        "addr_line1": (106, 80, str),
        "addr_line2": (186, 80, str),
        "postal_code": (266, 12, str),
        "city": (278, 25, str),
        "state_province": (303, 20, str),
        "country": (323, 24, str),
        "ceo_name": (347, 46, str),
        "description": (393, 150, str),
    }

    SEC_LAYOUT = {
        "pts": (0, 15, str),
        "rec_type": (15, 3, str),
        "symbol": (18, 15, str),
        "issue_type": (33, 6, str),
        "status": (39, 1, str),
        "name": (40, 70, str),
        "ex_id": (110, 6, str),
        "sh_out": (116, 13, int),
        "first_trade_date": (129, 8, str),
        "first_trade_exchg": (137, 8, str),
        "dividend": (145, 12, Decimal),
        "co_name_or_cik": (157, 60, str),
    }

    FIN_LAYOUT = {
        "pts": (0, 15, str),
        "rec_type": (15, 3, str),
        "year": (18, 4, int),
        "quarter": (22, 1, int),
        "qtrsartdate": (23, 8, str),
        "postdate": (31, 8, str),
        "revenue": (39, 17, Decimal),
        "earnings": (56, 17, Decimal),
        "eps": (73, 12, Decimal),
        "dilutedeps": (85, 12, Decimal),
        "margin": (97, 12, Decimal),
        "inventory": (109, 17, Decimal),
        "assets": (126, 17, Decimal),
        "liabilities": (143, 17, Decimal),
        "sh_out": (160, 13, int),
        "dilutedshout": (173, 13, int),
        "co_name_or_cik": (186, 60, str),
    }

    def __init__(self):
        self.current_line_number = 0
        self.errors: list[str] = []

    def parse_file(self, file_path: Path) -> Iterator[FinWireRecord]:
        logger.info(f"Parsing FinWire file: {file_path}")

        try:
            with open(file_path, encoding="utf-8") as file:
                for line_number, line in enumerate(file, 1):
                    self.current_line_number = line_number
                    line = line.rstrip("\n\r")

                    if len(line) < 18:
                        self.errors.append(f"Line {line_number}: Line too short")
                        continue

                    record_type = line[15:18].strip()

                    try:
                        if record_type == "CMP":
                            yield self._parse_cmp_record(line)
                        elif record_type == "SEC":
                            yield self._parse_sec_record(line)
                        elif record_type == "FIN":
                            yield self._parse_fin_record(line)
                        else:
                            self.errors.append(f"Line {line_number}: Unknown record type: {record_type}")
                            continue

                    except Exception as e:
                        error_msg = f"Line {line_number}: Error parsing {record_type} record: {str(e)}"
                        logger.warning(error_msg)
                        self.errors.append(error_msg)
                        continue

        except Exception as e:
            error_msg = f"Error reading FinWire file {file_path}: {str(e)}"
            logger.error(error_msg)
            self.errors.append(error_msg)
            raise

    def _parse_cmp_record(self, line: str) -> CompanyFundamentalRecord:
        try:
            fields = self._extract_fields(line, self.CMP_LAYOUT)

            founding_date = None
            founding_date_str = fields.get("founding_date", "").strip()
            if founding_date_str:
                try:
                    founding_date = datetime.strptime(founding_date_str, "%Y%m%d").date()
                except ValueError:
                    logger.warning(f"Invalid founding date: {founding_date_str}")

            return CompanyFundamentalRecord(
                record_type="CMP",
                company_id=fields.get("cik", "").strip(),
                company_name=fields.get("company_name", "").strip(),
                industry=fields.get("industry_id", "").strip(),
                sp_rating=fields.get("sp_rating", "").strip(),
                founding_date=founding_date,
                address_line1=fields.get("addr_line1", "").strip(),
                address_line2=fields.get("addr_line2", "").strip(),
                postal_code=fields.get("postal_code", "").strip(),
                city=fields.get("city", "").strip(),
                state_province=fields.get("state_province", "").strip(),
                country=fields.get("country", "").strip(),
                ceo=fields.get("ceo_name", "").strip(),
                description=fields.get("description", "").strip(),
                raw_data=line,
            )
        except Exception as e:
            logger.error(f"Error parsing CMP record: {str(e)}")
            return CompanyFundamentalRecord(record_type="CMP", company_name="UNKNOWN", raw_data=line)

    def _parse_sec_record(self, line: str) -> SecurityMasterRecord:
        fields = self._extract_fields(line, self.SEC_LAYOUT)

        first_trade_date = None
        if fields["first_trade_date"].strip():
            try:
                first_trade_date = datetime.strptime(fields["first_trade_date"], "%Y%m%d").date()
            except ValueError:
                logger.warning(f"Invalid first trade date: {fields['first_trade_date']}")

        dividend = None
        if fields["dividend"] is not None and str(fields["dividend"]).strip():
            try:
                dividend = Decimal(str(fields["dividend"]).strip())
            except (ValueError, InvalidOperation):
                logger.warning(f"Invalid dividend value: {fields['dividend']}")

        return SecurityMasterRecord(
            record_type="SEC",
            symbol=fields["symbol"].strip(),
            security_name=fields["name"].strip(),
            exchange=fields["ex_id"].strip(),
            is_active=(fields["status"].strip().upper() == "A"),
            status=fields["status"].strip(),
            issue_type=fields["issue_type"].strip(),
            shares_outstanding=fields["sh_out"],
            first_trade_date=first_trade_date,
            first_trade_exchange=fields["first_trade_exchg"].strip(),
            dividend=dividend,
            co_name_or_cik=fields["co_name_or_cik"].strip(),
            raw_data=line,
        )

    def _parse_fin_record(self, line: str) -> DailyMarketRecord:
        fields = self._extract_fields(line, self.FIN_LAYOUT)

        record_date = None
        if fields["qtrsartdate"].strip():
            try:
                record_date = datetime.strptime(fields["qtrsartdate"], "%Y%m%d").date()
            except ValueError:
                logger.warning(f"Invalid quarter start date: {fields['qtrsartdate']}")

        return DailyMarketRecord(
            record_type="FIN",
            company_id=fields["co_name_or_cik"].strip(),
            record_date=record_date,
            raw_data=line,
        )

    def _extract_fields(self, line: str, layout: dict[str, tuple[int, int, type]]) -> dict[str, Any]:
        fields = {}

        for field_name, (start_pos, length, field_type) in layout.items():
            end_pos = start_pos + length

            if len(line) < end_pos:
                if field_type in (int, Decimal):
                    fields[field_name] = None
                else:
                    fields[field_name] = ""
                continue

            raw_value = line[start_pos:end_pos]

            if field_type == str:
                fields[field_name] = raw_value
            elif field_type == int:
                try:
                    fields[field_name] = int(raw_value.strip()) if raw_value.strip() else None
                except ValueError:
                    fields[field_name] = None
            elif field_type == Decimal:
                try:
                    fields[field_name] = Decimal(raw_value.strip()) if raw_value.strip() else None
                except (ValueError, InvalidOperation):
                    fields[field_name] = None
            else:
                fields[field_name] = raw_value

        return fields


class FinWireProcessor:
    def __init__(self, connection: Any = None, dialect: str = "duckdb"):
        self.connection = connection
        self.dialect = dialect
        self.parser = FinWireParser()

    def process_finwire_file(self, file_path: Path, batch_id: int = 1, validate_data: bool = True) -> dict[str, Any]:
        logger.info(f"Processing FinWire file: {file_path}")

        start_time = datetime.now()
        stats = {
            "records_processed": 0,
            "cmp_records": 0,
            "sec_records": 0,
            "fin_records": 0,
            "errors": [],
            "warnings": [],
        }

        try:
            for record in self.parser.parse_file(file_path):
                stats["records_processed"] += 1

                if isinstance(record, CompanyFundamentalRecord):
                    self._process_company_record(record, batch_id)
                    stats["cmp_records"] += 1
                elif isinstance(record, SecurityMasterRecord):
                    self._process_security_record(record, batch_id)
                    stats["sec_records"] += 1
                elif isinstance(record, DailyMarketRecord):
                    self._process_financial_record(record, batch_id)
                    stats["fin_records"] += 1

            stats["errors"].extend(self.parser.errors)

            end_time = datetime.now()
            processing_time = (end_time - start_time).total_seconds()

            stats.update(
                {
                    "start_time": start_time,
                    "end_time": end_time,
                    "processing_time": processing_time,
                    "records_per_second": stats["records_processed"] / max(processing_time, 0.001),
                    "success": len(stats["errors"]) == 0,
                }
            )

            logger.info(f"FinWire processing completed: {stats['records_processed']} records in {processing_time:.2f}s")
            return stats

        except Exception as e:
            error_msg = f"FinWire processing failed: {str(e)}"
            logger.error(error_msg)
            stats["errors"].append(error_msg)
            stats["success"] = False
            return stats

    def _process_financial_record(self, record: DailyMarketRecord, batch_id: int) -> None:
        logger.debug(f"Processing financial record for: {record.company_id}")

    def process_batch(self, file_paths: list[Path]) -> dict[str, Any]:
        logger.info(f"Processing FinWire batch: {len(file_paths)} files")

        start_time = datetime.now()
        batch_stats = {
            "files_processed": 0,
            "total_records": 0,
            "cmp_records": 0,
            "sec_records": 0,
            "fin_records": 0,
            "errors": [],
            "success": True,
        }

        for file_path in file_paths:
            try:
                file_stats = self.process_finwire_file(file_path)
                batch_stats["files_processed"] += 1
                batch_stats["total_records"] += file_stats["records_processed"]
                batch_stats["cmp_records"] += file_stats["cmp_records"]
                batch_stats["sec_records"] += file_stats["sec_records"]
                batch_stats["fin_records"] += file_stats["fin_records"]
                batch_stats["errors"].extend(file_stats["errors"])

                if not file_stats["success"]:
                    batch_stats["success"] = False

            except Exception as e:
                error_msg = f"Failed to process {file_path}: {str(e)}"
                logger.error(error_msg)
                batch_stats["errors"].append(error_msg)
                batch_stats["success"] = False

        end_time = datetime.now()
        processing_time = (end_time - start_time).total_seconds()

        batch_stats.update(
            {
                "start_time": start_time,
                "end_time": end_time,
                "processing_time": processing_time,
                "files_per_second": batch_stats["files_processed"] / max(processing_time, 0.001),
            }
        )

        logger.info(
            f"FinWire batch processing completed: {batch_stats['files_processed']} files, {batch_stats['total_records']} records in {processing_time:.2f}s"
        )
        return batch_stats

    def process_file(self, file_path: Path, batch_id: int = 1) -> dict[str, Any]:
        return self.process_finwire_file(file_path, batch_id)

    def _process_company_record(self, record: CompanyFundamentalRecord, batch_id: int = 1) -> dict[str, Any]:
        logger.debug(f"Processing company record: {record.company_name}")

        return {
            "company_name": record.company_name,
            "industry": record.industry,
            "sp_rating": record.sp_rating,
            "ceo_name": record.ceo,
            "batch_id": batch_id,
            "processed": True,
        }

    def _process_security_record(self, record: SecurityMasterRecord, batch_id: int = 1) -> dict[str, Any]:
        logger.debug(f"Processing security record: {record.symbol}")

        return {
            "symbol": record.symbol,
            "issue": record.security_name,
            "exchange": record.exchange,
            "shares_outstanding": record.shares_outstanding,
            "batch_id": batch_id,
            "processed": True,
        }

    def get_processing_statistics(self) -> dict[str, Any]:
        return {
            "parser_errors": len(self.parser.errors),
            "supported_record_types": ["CMP", "SEC", "FIN"],
            "field_layouts": {
                "CMP": len(self.parser.CMP_LAYOUT),
                "SEC": len(self.parser.SEC_LAYOUT),
                "FIN": len(self.parser.FIN_LAYOUT),
            },
        }
