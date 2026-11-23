from __future__ import annotations

import csv
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from benchbox.utils.run_deadline import current_run_deadline

BATCH_IDS = (1, 2, 3)
BATCH_DATE_FILE = "BatchDate.txt"
HISTORICAL_SOURCE = "CustomerMgmt.xml"
INCREMENTAL_SOURCE = "Customer.txt"
HISTORICAL_AUDIT = "CustomerMgmt_audit.csv"
INCREMENTAL_AUDIT = "Customer_audit.csv"
DATA_SET = "DimCustomer"
TIER_ATTRIBUTE = "C_TIER_INV"
DOB_TOO_OLD_ATTRIBUTE = "C_DOB_TO"
DOB_TOO_YOUNG_ATTRIBUTE = "C_DOB_TY"
ATTRIBUTES = (TIER_ATTRIBUTE, DOB_TOO_OLD_ATTRIBUTE, DOB_TOO_YOUNG_ATTRIBUTE)
VALID_TIERS = frozenset({"1", "2", "3"})
CLAUSES = {
    1: {TIER_ATTRIBUTE: "4.5.4.4", DOB_TOO_OLD_ATTRIBUTE: "4.5.4.5", DOB_TOO_YOUNG_ATTRIBUTE: "4.5.4.5"},
    2: {TIER_ATTRIBUTE: "4.6.4.4", DOB_TOO_OLD_ATTRIBUTE: "4.6.4.5", DOB_TOO_YOUNG_ATTRIBUTE: "4.6.4.5"},
    3: {TIER_ATTRIBUTE: "4.6.4.4", DOB_TOO_OLD_ATTRIBUTE: "4.6.4.5", DOB_TOO_YOUNG_ATTRIBUTE: "4.6.4.5"},
}
INCREMENTAL_TIER_COLUMN = 9
INCREMENTAL_DOB_COLUMN = 10
INCREMENTAL_COLUMNS = 11
HISTORICAL_LIFETIME_YEARS = 100
CHECK_EVERY_RECORDS = 4096
_ACTION_TAG = "{http://www.tpc.org/tpc-di}Action"
_NEW = "NEW"
_UPDATE = "UPDCUST"


class RecountError(RuntimeError):
    pass


@dataclass(frozen=True)
class Difference:
    data_set: str
    batch: int
    attribute: str
    digen_value: int
    recount_value: int
    clause: str

    def as_record(self) -> dict[str, Any]:
        return {
            "data_set": self.data_set,
            "batch": self.batch,
            "attribute": self.attribute,
            "digen_value": self.digen_value,
            "recount_value": self.recount_value,
            "clause": self.clause,
        }


def _check_deadline() -> None:
    deadline = current_run_deadline()
    if deadline is not None:
        deadline.check()


def _batch_dir(staging_dir: Path, batch: int) -> Path:
    path = staging_dir / f"Batch{batch}"
    if not path.is_dir():
        raise RecountError(f"{staging_dir} has no Batch{batch} directory")
    return path


def read_batch_date(staging_dir: Path | str, batch: int) -> date:
    path = _batch_dir(Path(staging_dir), batch) / BATCH_DATE_FILE
    try:
        return date.fromisoformat(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError) as error:
        raise RecountError(f"{path} does not hold a YYYY-MM-DD date: {error}") from None


def _parse_birth_date(text: str, where: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise RecountError(f"{where} holds {text!r}, which is not a YYYY-MM-DD date of birth") from None


def _is_too_old(born: date, batch_date: date) -> bool:
    return (born.year, born.month, born.day) < (
        batch_date.year - HISTORICAL_LIFETIME_YEARS,
        batch_date.month,
        batch_date.day,
    )


def _is_too_young(born: date, batch_date: date) -> bool:
    return born > batch_date


def _historical_tier_is_invalid(action_type: str, tier: str | None) -> bool:
    if action_type == _NEW:
        return tier not in VALID_TIERS
    return action_type == _UPDATE and bool(tier) and tier not in VALID_TIERS


def _historical_customers(path: Path) -> Iterator[tuple[str, Mapping[str, str]]]:
    seen = 0
    root = None
    try:
        for event, element in ElementTree.iterparse(path, events=("start", "end")):
            if root is None:
                root = element
            if event != "end" or element.tag != _ACTION_TAG:
                continue
            customer = element.find("Customer")
            yield element.get("ActionType", ""), (customer.attrib if customer is not None else {})
            root.clear()
            seen += 1
            if seen % CHECK_EVERY_RECORDS == 0:
                _check_deadline()
    except ElementTree.ParseError as error:
        raise RecountError(f"{path} is not well-formed XML: {error}") from None


def _incremental_customers(path: Path) -> Iterator[tuple[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        for number, line in enumerate(handle, start=1):
            fields = line.rstrip("\r\n").split("|")
            if len(fields) < INCREMENTAL_COLUMNS:
                raise RecountError(
                    f"{path} line {number} has {len(fields)} fields, expected at least {INCREMENTAL_COLUMNS}"
                )
            yield fields[INCREMENTAL_TIER_COLUMN], fields[INCREMENTAL_DOB_COLUMN]
            if number % CHECK_EVERY_RECORDS == 0:
                _check_deadline()


def count_customer_attributes(staging_dir: Path | str, batch: int) -> dict[str, int]:
    root = Path(staging_dir)
    batch_dir = _batch_dir(root, batch)
    batch_date = read_batch_date(root, batch)
    counts = dict.fromkeys(ATTRIBUTES, 0)
    if batch == 1:
        path = batch_dir / HISTORICAL_SOURCE
        for action_type, customer in _historical_customers(path):
            tier = customer.get("C_TIER")
            if _historical_tier_is_invalid(action_type, tier):
                counts[TIER_ATTRIBUTE] += 1
            birth = customer.get("C_DOB")
            if birth and action_type in (_NEW, _UPDATE):
                born = _parse_birth_date(birth, f"{path} customer {customer.get('C_ID')}")
                counts[DOB_TOO_OLD_ATTRIBUTE] += _is_too_old(born, batch_date)
                counts[DOB_TOO_YOUNG_ATTRIBUTE] += _is_too_young(born, batch_date)
        return counts
    path = batch_dir / INCREMENTAL_SOURCE
    for number, (tier, birth) in enumerate(_incremental_customers(path), start=1):
        if tier not in VALID_TIERS:
            counts[TIER_ATTRIBUTE] += 1
        if birth:
            born = _parse_birth_date(birth, f"{path} line {number}")
            counts[DOB_TOO_OLD_ATTRIBUTE] += _is_too_old(born, batch_date)
            counts[DOB_TOO_YOUNG_ATTRIBUTE] += _is_too_young(born, batch_date)
    return counts


def read_audit_values(staging_dir: Path | str, batch: int) -> dict[tuple[str, str], int]:
    batch_dir = _batch_dir(Path(staging_dir), batch)
    path = batch_dir / (HISTORICAL_AUDIT if batch == 1 else INCREMENTAL_AUDIT)
    if not path.is_file():
        raise RecountError(f"{path} does not exist")
    values: dict[tuple[str, str], int] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = csv.reader(handle)
        next(rows, None)
        for row in rows:
            if not row:
                continue
            if len(row) < 5:
                raise RecountError(f"{path} holds a row with {len(row)} fields: {row}")
            data_set, attribute, value = row[0].strip(), row[3].strip(), row[4].strip()
            try:
                values[(data_set, attribute)] = int(value)
            except ValueError:
                raise RecountError(f"{path} holds a non-integer Value {value!r} for {data_set}/{attribute}") from None
    return values


def recount_answer_key(staging_dir: Path | str) -> list[Difference]:
    root = Path(staging_dir)
    differences: list[Difference] = []
    for batch in BATCH_IDS:
        audit = read_audit_values(root, batch)
        counts = count_customer_attributes(root, batch)
        for attribute in ATTRIBUTES:
            key = (DATA_SET, attribute)
            if key not in audit:
                raise RecountError(f"Batch{batch} audit file has no {DATA_SET}/{attribute} row")
            if counts[attribute] != audit[key]:
                differences.append(
                    Difference(
                        data_set=DATA_SET,
                        batch=batch,
                        attribute=attribute,
                        digen_value=audit[key],
                        recount_value=counts[attribute],
                        clause=CLAUSES[batch][attribute],
                    )
                )
    return differences
