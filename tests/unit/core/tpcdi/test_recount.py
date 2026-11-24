from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest

from benchbox.core.tpcdi.sources import recount
from benchbox.utils.run_deadline import RunTimeoutError, own_run_deadline

pytestmark = [pytest.mark.unit, pytest.mark.fast]

AUDIT_HEADER = "DataSet, BatchID ,Date , Attribute , Value, DValue\n"
ACTION = '<TPCDI:Action ActionType="{kind}" ActionTS="2007-07-07T16:07:49"><Customer {attributes}><Name/></Customer></TPCDI:Action>'
ACTIONS_OPEN = '<?xml version="1.0" encoding="UTF-8"?>\n<TPCDI:Actions xmlns:TPCDI="http://www.tpc.org/tpc-di">\n'
ACTIONS_CLOSE = "</TPCDI:Actions>\n"
BATCH_DATES = {1: "2017-07-07", 2: "2017-07-08", 3: "2017-07-09"}


def action(kind: str, customer: int, tier: str | None = None, dob: str | None = None) -> str:
    attributes = f'C_ID="{customer}"'
    if tier is not None:
        attributes += f' C_TIER="{tier}"'
    if dob is not None:
        attributes += f' C_DOB="{dob}"'
    return ACTION.format(kind=kind, attributes=attributes)


def customer_row(flag: str, customer: int, tier: str, dob: str) -> str:
    fields = [flag, str(1000 + customer), str(customer), "123-45-6789", "ACTV", "Last", "First", "", "F", tier, dob]
    return "|".join(fields) + "|Street||12345\n"


def write_audit(path: Path, batch: int, tier: int, too_old: int, too_young: int) -> None:
    path.write_text(
        AUDIT_HEADER
        + f"DimCustomer,{batch},,C_NEW,5,\n"
        + f"DimCustomer,{batch},,C_DOB_TO,{too_old},\n"
        + f"DimCustomer,{batch},,C_DOB_TY,{too_young},\n"
        + f"DimCustomer,{batch},,C_TIER_INV,{tier},\n"
        + f"DimAccount,{batch},,C_TIER_INV,999,\n",
        encoding="utf-8",
    )


def write_batch_dates(root: Path, dates: dict[int, str] | None = None) -> None:
    for batch, text in (dates or BATCH_DATES).items():
        (root / f"Batch{batch}").mkdir(parents=True, exist_ok=True)
        (root / f"Batch{batch}" / "BatchDate.txt").write_text(text + "\n", encoding="utf-8")


def build_staging(root: Path, claimed: dict[int, tuple[int, int, int]] | None = None) -> Path:
    write_batch_dates(root)
    historical = [
        action("NEW", 0, "1", "1917-07-07"),
        action("NEW", 1, "2", "1917-07-06"),
        action("NEW", 2, "3", "2017-07-07"),
        action("NEW", 3, "5", "2017-07-08"),
        action("NEW", 4, None, "1980-01-01"),
        action("NEW", 5, "", "1980-01-01"),
        action("UPDCUST", 0, "7"),
        action("UPDCUST", 1, "2", "1800-01-01"),
        action("UPDCUST", 2, ""),
        action("UPDCUST", 3),
        action("ADDACCT", 4),
        action("INACT", 5, "9"),
    ]
    (root / "Batch1" / "CustomerMgmt.xml").write_text(
        ACTIONS_OPEN + "\n".join(historical) + "\n" + ACTIONS_CLOSE, encoding="utf-8"
    )
    (root / "Batch2" / "Customer.txt").write_text(
        customer_row("I", 0, "1", "1917-10-27")
        + customer_row("U", 1, "", "1917-07-08")
        + customer_row("U", 2, "4", "1917-07-07")
        + customer_row("U", 3, "3", "2017-07-09")
        + customer_row("U", 4, "2", "2017-07-08"),
        encoding="utf-8",
    )
    (root / "Batch3" / "Customer.txt").write_text(
        customer_row("I", 4, "2", "1990-05-01") + customer_row("U", 4, "3", "1990-05-01"), encoding="utf-8"
    )
    figures = claimed or {1: (4, 2, 1), 2: (2, 1, 1), 3: (0, 0, 0)}
    write_audit(root / "Batch1" / "CustomerMgmt_audit.csv", 1, *figures[1])
    write_audit(root / "Batch2" / "Customer_audit.csv", 2, *figures[2])
    write_audit(root / "Batch3" / "Customer_audit.csv", 3, *figures[3])
    return root


def snapshot(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def incremental_counts(root: Path, rows: str, batch_date: str) -> dict[str, int]:
    write_batch_dates(root, {2: batch_date})
    (root / "Batch2" / "Customer.txt").write_text(rows, encoding="utf-8")
    return recount.count_customer_attributes(root, 2)


def test_known_counts_are_recounted_in_every_batch(tmp_path: Path) -> None:
    staging = build_staging(tmp_path)
    assert recount.count_customer_attributes(staging, 1) == {"C_TIER_INV": 4, "C_DOB_TO": 2, "C_DOB_TY": 1}
    assert recount.count_customer_attributes(staging, 2) == {"C_TIER_INV": 2, "C_DOB_TO": 1, "C_DOB_TY": 1}
    assert recount.count_customer_attributes(staging, 3) == {"C_TIER_INV": 0, "C_DOB_TO": 0, "C_DOB_TY": 0}


def test_a_valid_tier_is_never_counted_and_an_invalid_one_always_is(tmp_path: Path) -> None:
    for tier, expected in {"1": 0, "2": 0, "3": 0, "0": 1, "4": 1, "9": 1, "": 1}.items():
        counts = incremental_counts(tmp_path, customer_row("I", 0, tier, "1990-01-01"), "2017-07-08")
        assert counts["C_TIER_INV"] == expected, tier


def test_update_actions_count_only_a_supplied_invalid_tier(tmp_path: Path) -> None:
    write_batch_dates(tmp_path)
    cases = {
        "supplied invalid": (action("UPDCUST", 1, "6"), 1),
        "supplied valid": (action("UPDCUST", 1, "3"), 0),
        "retained": (action("UPDCUST", 1), 0),
        "emptied": (action("UPDCUST", 1, ""), 0),
        "new without tier": (action("NEW", 1, None, "1990-01-01"), 1),
        "account action": (action("ADDACCT", 1), 0),
        "inactivation": (action("INACT", 1, "8"), 0),
    }
    for name, (xml, expected) in cases.items():
        (tmp_path / "Batch1" / "CustomerMgmt.xml").write_text(ACTIONS_OPEN + xml + ACTIONS_CLOSE, encoding="utf-8")
        assert recount.count_customer_attributes(tmp_path, 1)["C_TIER_INV"] == expected, name


@pytest.mark.parametrize(
    ("born", "too_old", "too_young"),
    [
        ("1917-07-07", 1, 0),
        ("1917-07-08", 0, 0),
        ("1917-07-09", 0, 0),
        ("1917-10-27", 0, 0),
        ("1917-07-06", 1, 0),
        ("1900-01-01", 1, 0),
        ("2017-07-08", 0, 0),
        ("2017-07-09", 0, 1),
        ("2042-11-12", 0, 1),
    ],
)
def test_birth_dates_are_compared_with_the_batch_date_minus_one_hundred_years(
    tmp_path: Path, born: str, too_old: int, too_young: int
) -> None:
    counts = incremental_counts(tmp_path, customer_row("I", 0, "1", born), "2017-07-08")
    assert (counts["C_DOB_TO"], counts["C_DOB_TY"]) == (too_old, too_young)


@pytest.mark.parametrize(
    ("batch_date", "born", "too_old"),
    [
        ("2016-02-29", "1916-02-28", 1),
        ("2016-02-29", "1916-02-29", 0),
        ("2016-02-29", "1916-03-01", 0),
        ("2000-02-29", "1900-02-28", 1),
        ("2000-02-29", "1900-03-01", 0),
    ],
)
def test_leap_day_batch_dates_keep_the_exclusive_one_hundred_year_bound(
    tmp_path: Path, batch_date: str, born: str, too_old: int
) -> None:
    counts = incremental_counts(tmp_path, customer_row("I", 0, "1", born), batch_date)
    assert (counts["C_DOB_TO"], counts["C_DOB_TY"]) == (too_old, 0)


def test_a_missing_birth_date_is_not_an_alert(tmp_path: Path) -> None:
    counts = incremental_counts(tmp_path, customer_row("U", 0, "1", ""), "2017-07-08")
    assert (counts["C_DOB_TO"], counts["C_DOB_TY"]) == (0, 0)


def test_a_malformed_birth_date_is_refused(tmp_path: Path) -> None:
    with pytest.raises(recount.RecountError, match="'19-07-07'.*not a YYYY-MM-DD date of birth"):
        incremental_counts(tmp_path, customer_row("I", 0, "1", "19-07-07"), "2017-07-08")


def test_differences_report_digen_value_recount_value_and_clause(tmp_path: Path) -> None:
    staging = build_staging(tmp_path, claimed={1: (3, 2, 1), 2: (2, 2, 1), 3: (1, 0, 4)})
    assert [difference.as_record() for difference in recount.recount_answer_key(staging)] == [
        {
            "data_set": "DimCustomer",
            "batch": 1,
            "attribute": "C_TIER_INV",
            "digen_value": 3,
            "recount_value": 4,
            "clause": "4.5.4.4",
        },
        {
            "data_set": "DimCustomer",
            "batch": 2,
            "attribute": "C_DOB_TO",
            "digen_value": 2,
            "recount_value": 1,
            "clause": "4.6.4.5",
        },
        {
            "data_set": "DimCustomer",
            "batch": 3,
            "attribute": "C_TIER_INV",
            "digen_value": 1,
            "recount_value": 0,
            "clause": "4.6.4.4",
        },
        {
            "data_set": "DimCustomer",
            "batch": 3,
            "attribute": "C_DOB_TY",
            "digen_value": 4,
            "recount_value": 0,
            "clause": "4.6.4.5",
        },
    ]


def test_matching_audit_values_report_nothing(tmp_path: Path) -> None:
    assert recount.recount_answer_key(build_staging(tmp_path)) == []


def test_the_recount_writes_nothing_into_staging(tmp_path: Path) -> None:
    staging = build_staging(tmp_path, claimed={1: (0, 0, 0), 2: (0, 0, 0), 3: (0, 0, 0)})
    before = snapshot(staging)
    assert recount.recount_answer_key(staging)
    assert snapshot(staging) == before


def test_missing_audit_row_is_refused(tmp_path: Path) -> None:
    staging = build_staging(tmp_path)
    (staging / "Batch2" / "Customer_audit.csv").write_text(AUDIT_HEADER + "DimCustomer,2,,C_NEW,5,\n", encoding="utf-8")
    with pytest.raises(recount.RecountError, match="Batch2 audit file has no DimCustomer/C_TIER_INV row"):
        recount.recount_answer_key(staging)


def test_missing_batch_directory_is_refused(tmp_path: Path) -> None:
    with pytest.raises(recount.RecountError, match="no Batch1 directory"):
        recount.count_customer_attributes(tmp_path, 1)


def test_missing_batch_date_is_refused(tmp_path: Path) -> None:
    (tmp_path / "Batch1").mkdir()
    with pytest.raises(recount.RecountError, match="does not hold a YYYY-MM-DD date"):
        recount.count_customer_attributes(tmp_path, 1)


def test_non_integer_audit_value_is_refused(tmp_path: Path) -> None:
    staging = build_staging(tmp_path)
    (staging / "Batch1" / "CustomerMgmt_audit.csv").write_text(
        AUDIT_HEADER + "DimCustomer,1,,C_TIER_INV,many,\n", encoding="utf-8"
    )
    with pytest.raises(recount.RecountError, match="non-integer Value 'many'"):
        recount.recount_answer_key(staging)


def test_short_incremental_row_is_refused(tmp_path: Path) -> None:
    with pytest.raises(recount.RecountError, match="line 1 has 3 fields"):
        incremental_counts(tmp_path, "I|1|2\n", "2017-07-08")


def test_malformed_historical_xml_is_refused(tmp_path: Path) -> None:
    write_batch_dates(tmp_path)
    (tmp_path / "Batch1" / "CustomerMgmt.xml").write_text("<TPCDI:Actions", encoding="utf-8")
    with pytest.raises(recount.RecountError, match="not well-formed XML"):
        recount.count_customer_attributes(tmp_path, 1)


@pytest.mark.parametrize("batch", [1, 2])
def test_recount_checks_the_run_deadline_while_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, batch: int
) -> None:
    staging = build_staging(tmp_path)
    monkeypatch.setattr(recount, "CHECK_EVERY_RECORDS", 1)
    with own_run_deadline(1) as deadline:
        deadline._started -= 10
        with pytest.raises(RunTimeoutError):
            recount.count_customer_attributes(staging, batch)


def imported_modules(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            names.add(node.module or "")
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def test_recount_imports_nothing_from_the_etl_packages() -> None:
    imports = imported_modules(Path(recount.__file__))
    assert imports
    assert not [
        name for name in imports if name.startswith(("benchbox.core.tpcdi.etl", "benchbox.core.tpcdi._legacy_etl"))
    ]
    assert {name for name in imports if name.startswith("benchbox")} == {
        "benchbox.utils.run_deadline",
        "benchbox.utils.run_deadline.current_run_deadline",
    }
