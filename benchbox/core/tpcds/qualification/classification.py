from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

DISPLAY_PRECISION = "display_precision"
HALF_BOUNDARY_ROUNDING = "half_boundary_rounding"
FLOAT_DETAIL = "float_detail"
CHAR_PADDING = "char_padding"
TIED_ORDER = "tied_order"
RETURNS_DIFFERENCE = "returns_difference"
MALFORMED_OFFICIAL_ANSWER = "malformed_official_answer"
NULL_ORDER_VARIANT = "null_order_variant"

ROW_WIDTH_ERROR = "Official columns differ from SQL reference width"

REQUIRED_NONEMPTY = frozenset(
    {
        "3",
        "4",
        "8",
        "10",
        "23a",
        "23b",
        "24a",
        "24b",
        "31",
        "32",
        "37",
        "39a",
        "39b",
        "41",
        "54",
        "58",
        "61",
        "64",
        "65",
        "73",
        "82",
        "85",
        "90",
        "91",
        "92",
        "93",
    }
)


@dataclass(frozen=True)
class KnownDifference:
    statement: str
    classes: tuple[str, ...]
    evidence: str
    columns: frozenset[int] = frozenset()
    ratio: tuple[int, int, int, int] | None = None
    tie_key: tuple[int, ...] = ()
    result_digest: str | None = None
    variant_files: tuple[str, ...] = ()
    answer_sha256: str | None = None
    official_columns: int | None = None
    sql_columns: int | None = None
    permanent: bool = False


_DISPLAY_EVIDENCE = (
    "Official files print rounded values; each differing numeric cell rounds, half up or half to even, "
    "to its printed text."
)

_DISPLAY_STATEMENTS = (
    "7",
    "8",
    "9",
    "12",
    "13",
    "20",
    "22",
    "26",
    "27",
    "28",
    "31",
    "36",
    "49",
    "58",
    "59",
    "61",
    "63",
    "70",
    "83",
    "86",
    "90",
    "98",
)

_REASON_COUNT_EVIDENCE = (
    "The TPC-DS v4.0.0 kit's scaling.dst generates a 75-row reason table from SF 1 up, so sr_reason_sk, "
    "cr_reason_sk and wr_reason_sk span 1 to 75. Specification Table 3-2 and the official answers, which were not "
    "regenerated for v4.0.0, use 35 rows at SF 1; clause 3.4.2 says dsdgen prevails. With a 35-row table the "
    "unchanged SQL reproduces the official rows."
)

_HALF_BOUNDARY_COLUMNS = frozenset(range(8, 44))


def _entries() -> dict[str, KnownDifference]:
    known = {
        statement: KnownDifference(statement, (DISPLAY_PRECISION,), _DISPLAY_EVIDENCE)
        for statement in _DISPLAY_STATEMENTS
    }
    known["17"] = KnownDifference(
        "17",
        (MALFORMED_OFFICIAL_ANSWER,),
        "The pinned 17.ans has a merged header and separator field and a wrapped continuation row, so it parses "
        "to 14 columns where the SQL returns 15. The file is not reconstructed and the parser has no special "
        "case; DataFrame-to-SQL parity still checks Q17.",
        answer_sha256="6c2cef17ab183a4c833a4367269d0ca8c1016b6ed2ad81dd6677f7b94335f967",
        official_columns=14,
        sql_columns=15,
        permanent=True,
    )
    for statement in ("39a", "39b"):
        known[statement] = KnownDifference(
            statement,
            (DISPLAY_PRECISION, FLOAT_DETAIL),
            "The coefficient-of-variation columns differ from the printed values by less than 1e-8 relative "
            "(about 1e-12 to 2e-9), beyond the validator tolerance of 1e-10.",
            columns=frozenset({4, 9}),
        )
    known["66"] = KnownDifference(
        "66",
        (DISPLAY_PRECISION, HALF_BOUNDARY_ROUNDING),
        "Monthly totals that are exact decimal halves are computed by DataFrame engines as binary values just "
        "below the half, so display rounding goes down while the printed value goes up. "
        "The NULLS_FIRST file orders NULL first; the reference sorts NULL last.",
        columns=_HALF_BOUNDARY_COLUMNS,
        variant_files=("66_NULLS_FIRST.ans",),
    )
    known["77"] = KnownDifference(
        "77",
        (DISPLAY_PRECISION, HALF_BOUNDARY_ROUNDING, TIED_ORDER),
        "Channel totals that are exact decimal halves are computed by DataFrame engines as binary values just "
        "below the half. Rows tied on (channel, id) between a NULL id and the ROLLUP subtotal have no defined "
        "order. The NULLS_FIRST file orders NULL first; the reference sorts NULL last.",
        columns=frozenset({2, 3, 4}),
        tie_key=(0, 1),
        variant_files=("77_NULLS_FIRST.ans",),
    )
    known["78"] = KnownDifference(
        "78",
        (DISPLAY_PRECISION, HALF_BOUNDARY_ROUNDING),
        "The ratio is an exact decimal half (for example 23 / 40 = 0.575); ROUND on the binary value goes down "
        "where the official value rounds half up.",
        ratio=(3, 4, 7, 2),
    )
    known["84"] = KnownDifference(
        "84",
        (CHAR_PADDING,),
        "The official file concatenates the CHAR(30) last name with its trailing blanks before the comma.",
        columns=frozenset({1}),
    )
    known["85"] = KnownDifference(
        "85",
        (RETURNS_DIFFERENCE,),
        _REASON_COUNT_EVIDENCE,
        result_digest="75b7499e6165af5f3c3c3b2cb2eda83c25b143922628e3500ff16e4353faa3fa",
    )
    known["93"] = KnownDifference(
        "93",
        (RETURNS_DIFFERENCE,),
        _REASON_COUNT_EVIDENCE + " The NULLS_FIRST file orders NULL first; the reference sorts NULL last.",
        result_digest="8510ab97068042c67ca11a7d50c36cc71aabbdd3581139a6741c9e239c5f1582",
        variant_files=("93_NULLS_FIRST.ans",),
    )
    return known


KNOWN: dict[str, KnownDifference] = _entries()


def rows_digest(rows: list[tuple[Any, ...]]) -> str:
    step = Decimal("0.000001")

    def cell(value: Any) -> str:
        if value is None:
            return "NULL"
        if isinstance(value, (bool, str)):
            return str(value)
        if isinstance(value, (int, float, Decimal)):
            return format(Decimal(str(value)).quantize(step, ROUND_HALF_EVEN), "f")
        return str(value)

    return hashlib.sha256(json.dumps([[cell(value) for value in row] for row in rows]).encode()).hexdigest()


def _side_ok(item: dict[str, Any], side: str, classes: frozenset[str]) -> bool:
    if item[f"{side}_to_printed"]["status"] == "match":
        return True
    return item.get(f"{side}_classification", {}).get("class") in classes


def _malformed(entry: KnownDifference, files: list[dict[str, Any]]) -> bool:
    return bool(files) and all(
        item.get("status") == "error"
        and item.get("detail") == ROW_WIDTH_ERROR
        and item.get("sha256") == entry.answer_sha256
        and item.get("official_columns") == entry.official_columns
        and item.get("sql_columns") == entry.sql_columns
        for item in files
    )


def printed_classification(query: str, status: str, files: list[dict[str, Any]]) -> dict[str, Any]:
    if status == "match" or (
        not any(item.get("status") == "error" for item in files)
        and any(
            all(item.get(f"{side}_to_printed", {}).get("status") == "match" for side in ("sql", "dataframe"))
            for item in files
        )
    ):
        return {"state": "strict"}
    entry = KNOWN.get(query)
    if entry is None:
        return {"state": "unclassified"}
    if MALFORMED_OFFICIAL_ANSWER in entry.classes:
        if _malformed(entry, files):
            return {"state": "classified", "classes": [entry.classes[0]], "permanent": entry.permanent}
        return {"state": "unclassified"}
    allowed = frozenset(entry.classes)
    for item in sorted(files, key=lambda item: item.get("null_order") == "first"):
        if "sql_to_printed" not in item:
            continue
        if all(_side_ok(item, side, allowed) for side in ("sql", "dataframe")):
            used = sorted(
                {
                    item[f"{side}_classification"]["class"]
                    for side in ("sql", "dataframe")
                    if f"{side}_classification" in item and item[f"{side}_to_printed"]["status"] != "match"
                }
            )
            return {"state": "classified", "classes": used, "file": item["file"], "permanent": False}
    return {"state": "unclassified"}


def gate(
    query: str,
    status: str,
    parity: dict[str, Any],
    classification: dict[str, Any],
    nonempty: bool,
) -> dict[str, Any]:
    failures = []
    if parity.get("status") != "match":
        failures.append("dataframe_to_sql_divergence")
    if classification["state"] == "unclassified":
        failures.append("unclassified_printed_error" if status == "error" else "unclassified_printed_mismatch")
    if query in REQUIRED_NONEMPTY and not nonempty:
        failures.append("empty_at_sf1")
    return {"status": "fail" if failures else "pass", "failures": failures}
