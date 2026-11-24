# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark™ DI (TPC-DI) - Copyright © Transaction Processing Performance Council
# This implementation is based on the TPC-DI specification.

# Licensed under the MIT License. See LICENSE file in the project root for details.

import csv
import logging
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class CustomerAction(Enum):
    NEW = "NEW"
    ADDACCT = "ADDACCT"
    UPDCUST = "UPDCUST"
    UPDP = "UPDP"
    UPDACCT = "UPDACCT"
    CLOSEACCT = "CLOSEACCT"
    INACT = "INACT"


class AccountStatus(Enum):
    ACTIVE = "Active"
    INACTIVE = "Inactive"
    CLOSED = "Closed"
    SUSPENDED = "Suspended"


@dataclass
class CustomerDemographic:
    customer_id: int
    tax_id: str
    status: str
    last_name: str
    first_name: str
    middle_initial: Optional[str] = None
    gender: Optional[str] = None
    tier: Optional[int] = None
    dob: Optional[date] = None
    address_line1: Optional[str] = None
    address_line2: Optional[str] = None
    postal_code: Optional[str] = None
    city: Optional[str] = None
    state_province: Optional[str] = None
    state_prov: Optional[str] = None
    country: Optional[str] = None
    phone1: Optional[str] = None
    phone2: Optional[str] = None
    phone3: Optional[str] = None
    email1: Optional[str] = None
    email2: Optional[str] = None
    lcl_tx_id: Optional[str] = None
    nat_tx_id: Optional[str] = None

    credit_rating: Optional[int] = None
    net_worth: Optional[Decimal] = None
    income: Optional[Decimal] = None

    num_children: Optional[int] = None
    num_credit_cards: Optional[int] = None
    num_dependents: Optional[int] = None

    age_bracket: Optional[str] = None
    marital_status: Optional[str] = None
    buy_potential: Optional[str] = None
    vehicle_count: Optional[int] = None


@dataclass
class AccountManagement:
    account_id: int
    customer_id: int
    account_desc: str
    tax_status: int
    broker_id: int
    status: AccountStatus

    action: CustomerAction
    action_ts: datetime

    open_date: Optional[date] = None
    close_date: Optional[date] = None

    ca_id: Optional[int] = None
    ca_b_id: Optional[int] = None
    ca_name: Optional[str] = None


@dataclass
class CustomerRelationship:
    primary_customer_id: int
    related_customer_id: int
    relationship_type: str
    relationship_desc: Optional[str] = None
    effective_date: Optional[date] = None
    end_date: Optional[date] = None
    is_active: bool = True


@dataclass
class ProspectRecord:
    agency_id: str
    last_name: str
    first_name: str
    middle_initial: Optional[str] = None
    gender: Optional[str] = None
    address_line1: Optional[str] = None
    address_line2: Optional[str] = None
    postal_code: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    phone: Optional[str] = None
    income: Optional[int] = None
    num_children: Optional[int] = None
    num_credit_cards: Optional[int] = None
    own_or_rent_flag: Optional[str] = None
    employer: Optional[str] = None
    num_cars: Optional[int] = None
    age: Optional[int] = None
    credit_rating: Optional[int] = None
    net_worth: Optional[int] = None
    marketing_nameplate: Optional[str] = None


class CustomerManagementXMLParser:
    def __init__(self):
        self.current_action = None
        self.current_timestamp = None

    def parse_file(self, file_path: Path) -> Iterator[tuple[CustomerAction, datetime, Any]]:
        logger.info(f"Parsing CustomerMgmt.xml file: {file_path}")

        try:
            tree = ET.parse(file_path)
            root = tree.getroot()

            action_elements = root.findall(".//Action")
            if not action_elements:
                action_elements = root.findall(".//{http://www.tpc.org/tpc-di}Action")
            if not action_elements:
                action_elements = [elem for elem in root.iter() if elem.get("ActionType")]

            for action_elem in action_elements:
                action_type = action_elem.get("ActionType")
                action_ts = action_elem.get("ActionTS")

                if not action_type or not action_ts:
                    logger.warning("Skipping action with missing type or timestamp")
                    continue

                try:
                    timestamp = datetime.fromisoformat(action_ts.replace("T", " "))
                except ValueError:
                    logger.warning(f"Invalid timestamp format: {action_ts}")
                    continue

                try:
                    action_enum = CustomerAction(action_type)
                except ValueError:
                    logger.warning(f"Unknown action type: {action_type}")
                    continue

                if action_type in ["NEW", "UPDCUST", "UPDP"]:
                    customer_data = self._parse_customer_element(action_elem)
                    yield (action_enum, timestamp, customer_data)
                elif action_type in ["ADDACCT", "UPDACCT", "CLOSEACCT"]:
                    account_data = self._parse_account_element(action_elem)
                    yield (action_enum, timestamp, account_data)
                elif action_type == "INACT":
                    inact_data = self._parse_inact_element(action_elem)
                    yield (action_enum, timestamp, inact_data)

        except ET.ParseError as e:
            error_msg = f"XML parsing error in file {file_path}: {str(e)}"
            logger.error(error_msg)
            raise ValueError(error_msg) from e
        except Exception as e:
            error_msg = f"Error parsing CustomerMgmt.xml file {file_path}: {str(e)}"
            logger.error(error_msg)
            raise

    def _parse_customer_element(self, action_elem: ET.Element) -> CustomerDemographic:
        customer_elem = action_elem.find("Customer")
        if customer_elem is None:
            raise ValueError("Missing Customer element in action")

        customer_id = int(customer_elem.get("C_ID"))
        tax_id = customer_elem.get("C_TAX_ID", "")
        status = customer_elem.get("C_ST_ID", "")
        last_name = customer_elem.get("C_L_NAME", "")
        first_name = customer_elem.get("C_F_NAME", "")
        middle_initial = customer_elem.get("C_M_NAME")
        gender = customer_elem.get("C_GNDR")
        tier = self._safe_int(customer_elem.get("C_TIER"))

        dob = None
        dob_str = customer_elem.get("C_DOB")
        if dob_str:
            try:
                dob = datetime.strptime(dob_str, "%Y-%m-%d").date()
            except ValueError:
                logger.warning(f"Invalid date of birth: {dob_str}")

        address_line1 = customer_elem.get("C_ADLINE1")
        address_line2 = customer_elem.get("C_ADLINE2")
        postal_code = customer_elem.get("C_ZIPCODE")
        city = customer_elem.get("C_CITY")
        state_province = customer_elem.get("C_STATE_PROV")
        country = customer_elem.get("C_CTRY")

        phone1 = customer_elem.get("C_PRIM_EMAIL")
        phone2 = customer_elem.get("C_ALT_EMAIL")
        email1 = customer_elem.get("C_PHONE_1")
        email2 = customer_elem.get("C_PHONE_2")

        credit_rating = self._safe_int(customer_elem.get("C_CRDT_RTG"))
        net_worth = self._safe_decimal(customer_elem.get("C_NET_WORTH"))
        income = self._safe_decimal(customer_elem.get("C_INCOME"))

        return CustomerDemographic(
            customer_id=customer_id,
            tax_id=tax_id,
            status=status,
            last_name=last_name,
            first_name=first_name,
            middle_initial=middle_initial,
            gender=gender,
            tier=tier,
            dob=dob,
            address_line1=address_line1,
            address_line2=address_line2,
            postal_code=postal_code,
            city=city,
            state_province=state_province,
            country=country,
            phone1=phone1,
            phone2=phone2,
            email1=email1,
            email2=email2,
            credit_rating=credit_rating,
            net_worth=net_worth,
            income=income,
        )

    def _parse_account_element(self, action_elem: ET.Element) -> AccountManagement:
        account_elem = action_elem.find("Account")
        if account_elem is None:
            raise ValueError("Missing Account element in action")

        account_id = int(account_elem.get("CA_ID"))
        customer_id = int(account_elem.get("CA_C_ID"))
        account_desc = account_elem.get("CA_NAME", "")
        tax_status = int(account_elem.get("CA_TAX_ST", "0"))
        broker_id = int(account_elem.get("CA_B_ID"))

        status_str = account_elem.get("CA_ST_ID", "Active")
        try:
            status = AccountStatus(status_str)
        except ValueError:
            logger.warning(f"Unknown account status: {status_str}, defaulting to Active")
            status = AccountStatus.ACTIVE

        action_type = action_elem.get("ActionType")
        action_ts_str = action_elem.get("ActionTS")
        if action_ts_str is None:
            raise ValueError("Missing ActionTS attribute in action element")
        action_ts = datetime.fromisoformat(action_ts_str.replace("T", " "))

        return AccountManagement(
            account_id=account_id,
            customer_id=customer_id,
            account_desc=account_desc,
            tax_status=tax_status,
            broker_id=broker_id,
            status=status,
            action=CustomerAction(action_type),
            action_ts=action_ts,
        )

    def _parse_inact_element(self, action_elem: ET.Element) -> dict[str, Any]:
        inact_elem = action_elem.find("Inactivate")
        if inact_elem is None:
            raise ValueError("Missing Inactivate element in INACT action")

        action_ts_str = action_elem.get("ActionTS")
        if action_ts_str is None:
            raise ValueError("Missing ActionTS attribute in action element")

        return {
            "customer_id": int(inact_elem.get("CA_C_ID")),
            "account_id": int(inact_elem.get("CA_ID")),
            "action_ts": datetime.fromisoformat(action_ts_str.replace("T", " ")),
        }

    def _safe_int(self, value: Optional[str]) -> Optional[int]:
        if not value or not value.strip():
            return None
        try:
            return int(value)
        except ValueError:
            return None

    def _safe_decimal(self, value: Optional[str]) -> Optional[Decimal]:
        if not value or not value.strip():
            return None
        try:
            return Decimal(value)
        except (ValueError, InvalidOperation):
            return None


class ProspectCSVParser:
    def parse_file(self, file_path: Path) -> Iterator[ProspectRecord]:
        logger.info(f"Parsing Prospect.csv file: {file_path}")

        try:
            with open(file_path, encoding="utf-8") as csvfile:
                reader = csv.DictReader(csvfile)

                for row_num, row in enumerate(reader, 1):
                    try:
                        prospect = ProspectRecord(
                            agency_id=row.get("AgencyID", "").strip(),
                            last_name=row.get("LastName", "").strip(),
                            first_name=row.get("FirstName", "").strip(),
                            middle_initial=row.get("MiddleInitial", "").strip() or None,
                            gender=row.get("Gender", "").strip() or None,
                            address_line1=row.get("AddressLine1", "").strip() or None,
                            address_line2=row.get("AddressLine2", "").strip() or None,
                            postal_code=row.get("PostalCode", "").strip() or None,
                            city=row.get("City", "").strip() or None,
                            state=row.get("State", "").strip() or None,
                            country=row.get("Country", "").strip() or None,
                            phone=row.get("Phone", "").strip() or None,
                            income=self._safe_int(row.get("Income")),
                            num_children=self._safe_int(row.get("NumChildren")),
                            num_credit_cards=self._safe_int(row.get("NumCreditCards")),
                            own_or_rent_flag=row.get("OwnOrRentFlag", "").strip() or None,
                            employer=row.get("Employer", "").strip() or None,
                            num_cars=self._safe_int(row.get("NumberCars")),
                            age=self._safe_int(row.get("Age")),
                            credit_rating=self._safe_int(row.get("CreditRating")),
                            net_worth=self._safe_int(row.get("NetWorth")),
                            marketing_nameplate=row.get("MarketingNameplate", "").strip() or None,
                        )
                        yield prospect

                    except Exception as e:
                        logger.warning(f"Error parsing row {row_num}: {str(e)}")
                        continue

        except Exception as e:
            error_msg = f"Error reading Prospect.csv file {file_path}: {str(e)}"
            logger.error(error_msg)
            raise

    def _safe_int(self, value: Optional[str]) -> Optional[int]:
        if not value or not value.strip():
            return None
        try:
            return int(value)
        except ValueError:
            return None


class CustomerManagementProcessor:
    def __init__(self, connection: Any = None, dialect: str = "duckdb"):
        self.connection = connection
        self.dialect = dialect
        self.xml_parser = CustomerManagementXMLParser()
        self.prospect_parser = ProspectCSVParser()

    def process_customer_management_file(
        self, file_path: Path, batch_id: int = 1, validate_data: bool = True
    ) -> dict[str, Any]:
        logger.info(f"Processing CustomerMgmt file: {file_path}")

        start_time = datetime.now()
        stats: dict[str, Any] = {
            "records_processed": 0,
            "new_customers": 0,
            "updated_customers": 0,
            "new_accounts": 0,
            "updated_accounts": 0,
            "closed_accounts": 0,
            "inactivated_customers": 0,
            "errors": [],
            "warnings": [],
        }

        try:
            for action, timestamp, data in self.xml_parser.parse_file(file_path):
                stats["records_processed"] += 1

                if action == CustomerAction.NEW:
                    self._process_new_customer(data, timestamp, batch_id)
                    stats["new_customers"] += 1
                elif action == CustomerAction.UPDCUST:
                    self._process_customer_update(data, timestamp, batch_id)
                    stats["updated_customers"] += 1
                elif action == CustomerAction.ADDACCT:
                    self._process_new_account(data, timestamp, batch_id)
                    stats["new_accounts"] += 1
                elif action == CustomerAction.UPDACCT:
                    self._process_account_update(data, timestamp, batch_id)
                    stats["updated_accounts"] += 1
                elif action == CustomerAction.CLOSEACCT:
                    self._process_account_closure(data, timestamp, batch_id)
                    stats["closed_accounts"] += 1
                elif action == CustomerAction.INACT:
                    self._process_customer_inactivation(data, timestamp, batch_id)
                    stats["inactivated_customers"] += 1

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

            logger.info(
                f"CustomerMgmt processing completed: {stats['records_processed']} records in {processing_time:.2f}s"
            )
            return stats

        except Exception as e:
            error_msg = f"CustomerMgmt processing failed: {str(e)}"
            logger.error(error_msg)
            stats["errors"].append(error_msg)
            stats["success"] = False
            return stats

    def process_prospect_file(self, file_path: Path, batch_id: int = 1) -> dict[str, Any]:
        logger.info(f"Processing Prospect file: {file_path}")

        start_time = datetime.now()
        stats = {"prospects_processed": 0, "errors": [], "warnings": []}

        try:
            for prospect in self.prospect_parser.parse_file(file_path):
                self._process_prospect_record(prospect, batch_id)
                stats["prospects_processed"] += 1

            end_time = datetime.now()
            processing_time = (end_time - start_time).total_seconds()

            stats.update(
                {
                    "start_time": start_time,
                    "end_time": end_time,
                    "processing_time": processing_time,
                    "records_per_second": stats["prospects_processed"] / max(processing_time, 0.001),
                    "success": len(stats["errors"]) == 0,
                }
            )

            logger.info(
                f"Prospect processing completed: {stats['prospects_processed']} records in {processing_time:.2f}s"
            )
            return stats

        except Exception as e:
            error_msg = f"Prospect processing failed: {str(e)}"
            logger.error(error_msg)
            stats["errors"].append(error_msg)
            stats["success"] = False
            return stats

    def _process_new_customer(self, customer: CustomerDemographic, timestamp: datetime, batch_id: int) -> None:
        logger.debug(f"Processing new customer: {customer.customer_id}")

    def _process_customer_update(self, customer: CustomerDemographic, timestamp: datetime, batch_id: int) -> None:
        logger.debug(f"Processing customer update: {customer.customer_id}")

    def _process_new_account(self, account: AccountManagement, timestamp: datetime, batch_id: int) -> None:
        logger.debug(f"Processing new account: {account.account_id}")

    def _process_account_update(self, account: AccountManagement, timestamp: datetime, batch_id: int) -> None:
        logger.debug(f"Processing account update: {account.account_id}")

    def _process_account_closure(self, account: AccountManagement, timestamp: datetime, batch_id: int) -> None:
        logger.debug(f"Processing account closure: {account.account_id}")

    def _process_customer_inactivation(self, inact_data: dict[str, Any], timestamp: datetime, batch_id: int) -> None:
        logger.debug(f"Processing customer inactivation: {inact_data['customer_id']}")

    def _process_prospect_record(self, prospect: ProspectRecord, batch_id: int) -> None:
        logger.debug(f"Processing prospect: {prospect.agency_id}")

    def get_processing_statistics(self) -> dict[str, Any]:
        return {
            "supported_actions": [action.value for action in CustomerAction],
            "supported_account_statuses": [status.value for status in AccountStatus],
            "xml_parser_available": True,
            "csv_parser_available": True,
            "supported_formats": ["XML", "CSV"],
        }

    def process_xml_file(self, file_path: Path) -> dict[str, Any]:
        logger.info(f"Processing XML file: {file_path}")

        total_actions = 0
        new_customers = 0
        updated_customers = 0
        errors = []

        try:
            for action, _timestamp, _data in self.xml_parser.parse_file(file_path):
                total_actions += 1

                if action == CustomerAction.NEW:
                    new_customers += 1
                elif action in [CustomerAction.UPDCUST, CustomerAction.UPDP]:
                    updated_customers += 1

            return {
                "success": True,
                "total_actions": total_actions,
                "new_customers": new_customers,
                "updated_customers": updated_customers,
                "errors": errors,
            }

        except Exception as e:
            error_msg = f"Error processing XML file {file_path}: {str(e)}"
            logger.error(error_msg)
            return {
                "success": False,
                "total_actions": 0,
                "new_customers": 0,
                "updated_customers": 0,
                "errors": [error_msg],
            }

    def process_csv_file(self, file_path: Path) -> dict[str, Any]:
        logger.info(f"Processing CSV file: {file_path}")

        prospects = []
        errors = []

        try:
            for prospect in self.prospect_parser.parse_file(file_path):
                prospects.append(prospect)

            return {
                "success": True,
                "total_prospects": len(prospects),
                "prospects": prospects,
                "errors": errors,
            }

        except Exception as e:
            error_msg = f"Error processing CSV file {file_path}: {str(e)}"
            logger.error(error_msg)
            return {
                "success": False,
                "total_prospects": 0,
                "prospects": [],
                "errors": [error_msg],
            }

    def _process_customer_action(self, action_data: dict[str, Any]) -> dict[str, Any]:
        try:
            action = action_data.get("action")
            customer_id = action_data.get("customer_id")
            timestamp = action_data.get("timestamp")
            data = action_data.get("data", {})

            if action == CustomerAction.NEW:
                action_type = "NEW"
            elif action == CustomerAction.UPDCUST:
                action_type = "UPDCUST"
            elif action == CustomerAction.ADDACCT:
                action_type = "ADDACCT"
            else:
                action_type = action.value if hasattr(action, "value") else str(action)

            return {
                "action_type": action_type,
                "customer_id": customer_id,
                "processed": True,
                "timestamp": timestamp,
                "data_processed": len(data) > 0,
            }

        except Exception as e:
            logger.error(f"Error processing customer action: {str(e)}")
            return {
                "action_type": "UNKNOWN",
                "customer_id": None,
                "processed": False,
                "error": str(e),
            }

    def _validate_demographic_data(self, demo_data: CustomerDemographic) -> dict[str, Any]:
        errors = []

        if not demo_data.customer_id:
            errors.append("Customer ID is required")

        if not demo_data.last_name or not demo_data.last_name.strip():
            errors.append("Last name is required")

        if not demo_data.first_name or not demo_data.first_name.strip():
            errors.append("First name is required")

        if demo_data.email1 and "@" not in demo_data.email1:
            errors.append("Invalid email format")

        if (
            demo_data.phone1
            and len(demo_data.phone1.replace("-", "").replace(" ", "").replace("(", "").replace(")", "")) < 7
        ):
            errors.append("Invalid phone number format")

        state = demo_data.state_province or demo_data.state_prov
        if state and len(state.strip()) < 2:
            errors.append("Invalid state/province format")

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "validated_fields": [
                "customer_id",
                "last_name",
                "first_name",
                "email1",
                "phone1",
            ],
        }

    def process_batch(self, file_paths: list[Path]) -> dict[str, Any]:
        logger.info(f"Processing batch of {len(file_paths)} files")

        xml_files = 0
        csv_files = 0
        total_processed = 0
        errors = []

        try:
            for file_path in file_paths:
                if file_path.suffix.lower() == ".xml":
                    result = self.process_xml_file(file_path)
                    xml_files += 1
                elif file_path.suffix.lower() == ".csv":
                    result = self.process_csv_file(file_path)
                    csv_files += 1
                else:
                    logger.warning(f"Unsupported file type: {file_path}")
                    continue

                if result.get("success"):
                    total_processed += 1
                else:
                    errors.extend(result.get("errors", []))

            return {
                "success": len(errors) == 0,
                "files_processed": total_processed,
                "xml_files": xml_files,
                "csv_files": csv_files,
                "total_files": len(file_paths),
                "errors": errors,
            }

        except Exception as e:
            error_msg = f"Error processing batch: {str(e)}"
            logger.error(error_msg)
            return {
                "success": False,
                "files_processed": 0,
                "xml_files": 0,
                "csv_files": 0,
                "total_files": len(file_paths),
                "errors": [error_msg],
            }
