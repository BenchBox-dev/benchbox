from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchbox.core.results.tpcdi_audit import UNAUDITED_VALIDATION_STATUS
from benchbox.core.tpcdi.appendix import appendix_path, load_appendix_sql
from benchbox.core.tpcdi.etl.staging import SOURCE_DIALECT, execute_sql
from benchbox.core.tpcdi.sources.recount import recount_answer_key
from benchbox.utils.run_deadline import enter_run_phase

AUDIT_PHASE = "audit"
AUDIT_FILE_PATTERN = "*_audit.csv"
OK_RESULT = "OK"
FAILED_STATUS = "FAILED"
FINAL_BATCH = 3

_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_UNION = re.compile(r"\bunion\b", re.IGNORECASE)
_TEST_NAME = re.compile(r"^\s*select\s+'([^']+)'", re.IGNORECASE)
_WHOLE_RUN = re.compile(r"^\s*select\s+'[^']+'(?:\s+as\s+\w+)?\s*,\s*NULL\b", re.IGNORECASE)
_BATCH_COUNT = re.compile(
    r"\(\s*select\s+(?:max|count)\(\s*(?:distinct\s+)?BatchID\s*\)[^()]*\)\s*=\s*[34]\b", re.IGNORECASE
)
_SUMMED = re.compile(
    r"sum\(\s*Value\s*\)\s+from\s+Audit\s+where\s+DataSet\s*=\s*'(\w+)'\s+and\s+Attribute\s*=\s*'(\w+)'\s*\)",
    re.IGNORECASE,
)

AUDIT_FILES = """
read_csv(
  $paths,
  header = true,
  columns = {'DataSet': 'VARCHAR', 'BatchID': 'VARCHAR', 'Date': 'VARCHAR', 'Attribute': 'VARCHAR',
             'Value': 'VARCHAR', 'DValue': 'VARCHAR'}
)
"""

AUDIT_SQL = {
    "load_audit": f"""
INSERT INTO Audit
SELECT
  trim(DataSet),
  CAST(nullif(trim(BatchID), '') AS INTEGER),
  CAST(nullif(trim(Date), '') AS DATE),
  trim(Attribute),
  CAST(nullif(trim(Value), '') AS BIGINT),
  TRY_CAST(nullif(trim(DValue), '') AS DECIMAL(15, 5))
FROM {AUDIT_FILES}
""",
    "dvalues_not_stored": f"""
SELECT trim(DataSet), CAST(nullif(trim(BatchID), '') AS INTEGER), trim(Attribute), trim(DValue)
FROM {AUDIT_FILES}
WHERE nullif(trim(DValue), '') IS NOT NULL AND TRY_CAST(trim(DValue) AS DECIMAL(15, 5)) IS NULL
ORDER BY 1, 2, 3
""",
    "correct_value": (
        "UPDATE Audit SET Value = $value WHERE DataSet = $data_set AND BatchID = $batch AND Attribute = $attribute"
    ),
    "attributes_in_later_batches": "SELECT DISTINCT DataSet, Attribute FROM Audit WHERE BatchID > $latest_batch",
}


@dataclass(frozen=True)
class AuditTestSpec:
    name: str
    per_batch: bool
    counts_every_batch: bool
    summed: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class AuditResult:
    test: str
    batch: int | None
    result: str
    description: str
    evaluable: bool


@dataclass(frozen=True)
class AuditOutcome:
    results: tuple[AuditResult, ...]
    corrections: tuple[dict[str, Any], ...]
    dvalues_not_stored: tuple[dict[str, Any], ...]
    appendix_sha256: str
    pre_steps: tuple[dict[str, Any], ...]
    latest_batch: int

    @property
    def failures(self) -> tuple[AuditResult, ...]:
        return tuple(result for result in self.results if result.evaluable and result.result != OK_RESULT)

    @property
    def excluded_tests(self) -> tuple[str, ...]:
        return tuple(sorted({result.test for result in self.results if not result.evaluable and result.batch is None}))

    @property
    def status(self) -> str:
        return FAILED_STATUS if self.failures else UNAUDITED_VALIDATION_STATUS

    def as_block(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "latest_batch": self.latest_batch,
            "appendix_sha256": self.appendix_sha256,
            "pre_steps": list(self.pre_steps),
            "answer_key_corrections": list(self.corrections),
            "dvalues_not_stored": list(self.dvalues_not_stored),
            "excluded_tests": list(self.excluded_tests),
            "tests": [
                {
                    "test": result.test,
                    "batch": result.batch,
                    "result": result.result,
                    "description": result.description,
                    "evaluable": result.evaluable,
                }
                for result in self.results
            ],
        }


def audit_test_specs(text: str | None = None) -> dict[str, AuditTestSpec]:
    source = text if text is not None else appendix_path("audit").read_text(encoding="utf-8")
    specs: dict[str, AuditTestSpec] = {}
    for block in _UNION.split(_COMMENT.sub("", source)):
        named = _TEST_NAME.search(block)
        if named is None:
            continue
        whole_run = _WHOLE_RUN.search(block) is not None
        specs[named.group(1)] = AuditTestSpec(
            name=named.group(1),
            per_batch=not whole_run,
            counts_every_batch=whole_run and _BATCH_COUNT.search(block) is not None,
            summed=tuple(_SUMMED.findall(block)) if whole_run else (),
        )
    return specs


def is_evaluable(spec: AuditTestSpec | None, batch: int | None, latest_batch: int, later: set[tuple[str, str]]) -> bool:
    if spec is None:
        return True
    if spec.per_batch:
        return batch is not None and batch <= latest_batch
    if spec.counts_every_batch:
        return latest_batch == FINAL_BATCH
    return not any(summed in later for summed in spec.summed)


def load_audit_table(
    connection: Any, staging_dir: Path | str, platform: str = SOURCE_DIALECT
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    paths = {"paths": [str(path) for path in sorted(Path(staging_dir).rglob(AUDIT_FILE_PATTERN))]}
    if not paths["paths"]:
        raise FileNotFoundError(f"No {AUDIT_FILE_PATTERN} files under {staging_dir}")
    not_stored = [
        {"data_set": data_set, "batch": batch, "attribute": attribute, "dvalue": dvalue}
        for data_set, batch, attribute, dvalue in execute_sql(
            connection, AUDIT_SQL["dvalues_not_stored"], platform, paths
        ).fetchall()
    ]
    execute_sql(connection, AUDIT_SQL["load_audit"], platform, paths)
    corrections = []
    for difference in recount_answer_key(staging_dir):
        execute_sql(
            connection,
            AUDIT_SQL["correct_value"],
            platform,
            {
                "value": difference.recount_value,
                "data_set": difference.data_set,
                "batch": difference.batch,
                "attribute": difference.attribute,
            },
        )
        corrections.append(
            {
                "data_set": difference.data_set,
                "batch": difference.batch,
                "attribute": difference.attribute,
                "digen_value": difference.digen_value,
                "recount_value": difference.recount_value,
                "clause": difference.clause,
            }
        )
    return corrections, not_stored


def run_audit(
    connection: Any, staging_dir: Path | str, *, latest_batch: int, platform: str = SOURCE_DIALECT
) -> AuditOutcome:
    enter_run_phase(AUDIT_PHASE)
    corrections, not_stored = load_audit_table(connection, staging_dir, platform)
    appendix = load_appendix_sql("audit", platform)
    rows: Sequence[tuple] = []
    for statement in appendix.statements():
        rows = connection.execute(statement).fetchall()
    specs = audit_test_specs()
    later = {
        (data_set, attribute)
        for data_set, attribute in execute_sql(
            connection, AUDIT_SQL["attributes_in_later_batches"], platform, {"latest_batch": latest_batch}
        ).fetchall()
    }
    results = tuple(
        AuditResult(
            test=test,
            batch=batch,
            result=result,
            description=description,
            evaluable=is_evaluable(specs.get(test), batch, latest_batch, later),
        )
        for test, batch, result, description in sorted(rows, key=lambda row: (row[0], row[1] is not None, row[1] or 0))
    )
    return AuditOutcome(
        results=results,
        corrections=tuple(corrections),
        dvalues_not_stored=tuple(not_stored),
        appendix_sha256=appendix.stored_sha256,
        pre_steps=tuple(
            {"token": step.token, "replacement": step.replacement, "occurrences": step.occurrences}
            for step in appendix.pre_steps
        ),
        latest_batch=latest_batch,
    )
