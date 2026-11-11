from __future__ import annotations

import json

import duckdb
import pytest

from benchbox.core.expected_results.loader import _find_tpcds_answers_dir, parse_tpcds_answer_values
from benchbox.core.tpcds.qualification import runner

pytestmark = [pytest.mark.unit, pytest.mark.fast, pytest.mark.tpcds]
CASES = [
    (
        "64.ans",
        "c31d10836cd209e5dcebbcaf31d8a59b34b79638acf297b144a3e8ec59653b6c",
        "[NULL]",
        1,
        (9, 10),
        ("C_CITY", "C_ZIP"),
    ),
    (
        "73.ans",
        "03ef9ecdda1a968307aaf977b4a8e687d321f75259ef5625994b042d3905d077",
        "-",
        0,
        (2,),
        ("C_SALUTATION",),
    ),
]


@pytest.fixture(scope="module")
def inputs():
    return json.loads(runner._INPUTS.read_text(encoding="utf-8"))


@pytest.mark.parametrize("name,digest,token,row_index,indices,columns", CASES)
def test_pinned_official_null_fields_compare_with_sql_nulls(inputs, name, digest, token, row_index, indices, columns):
    path = _find_tpcds_answers_dir() / name
    assert runner.sha256(path) == inputs["answers"][name]["sha256"] == digest
    block = parse_tpcds_answer_values(path)[0]
    occurrences = {
        (row, column) for row, values in enumerate(block.rows) for column, value in enumerate(values) if value == token
    }
    assert occurrences == ({(1, 9), (1, 10), (2, 9), (2, 10)} if name == "64.ans" else {(0, 2)})
    assert tuple(block.columns[index] for index in indices) == columns
    printed = tuple(block.rows[row_index][index] for index in indices)
    assert printed == (token,) * len(indices)
    answer = {"columns": columns, "rows": [printed], "null_tokens": inputs["answers"][name]["null_tokens"]}
    sql = "SELECT " + ",".join(f"NULL::VARCHAR AS {column}" for column in columns)
    with duckdb.connect() as connection:
        cursor = connection.execute(sql)
        types = [str(column[1]) for column in cursor.description]
        reference = cursor.fetchall()
    converted = runner.convert_rows(answer, types)
    assert converted == reference == [(None,) * len(indices)]
    query = name.removesuffix(".ans")
    assert runner.compare_rows(converted, reference, query, sql)["status"] == "match"
    literal = [printed]
    assert runner.compare_rows(literal, reference, query, sql)["status"] == "mismatch"


@pytest.mark.parametrize("name,digest,token,row_index,indices,columns", CASES)
def test_file_specific_null_pins_preserve_other_literal_spellings(
    inputs, name, digest, token, row_index, indices, columns
):
    null_tokens = inputs["answers"][name]["null_tokens"]
    assert null_tokens == [token]
    for literal in ("[NULL]", "-", "NULL", "N/A", "0007", "Miss", "Reno"):
        expected = None if literal == token else literal
        assert runner.convert_cell(literal, "VARCHAR", null_tokens) == expected
        assert runner.convert_cell(literal, "VARCHAR", []) == literal
        assert runner.convert_cell(literal, "VARCHAR", inputs["answers"]["1.ans"]["null_tokens"]) == literal


@pytest.mark.parametrize("name,digest,token,row_index,indices,columns", CASES)
def test_changed_official_file_hash_still_refuses_before_null_conversion(
    inputs, name, digest, token, row_index, indices, columns
):
    changed = json.loads(json.dumps(inputs))
    changed["answers"][name]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match=f"Official answer hash differs: {name}"):
        runner.official_inventory(_find_tpcds_answers_dir(), changed)


def test_complete_official_inventory_retains_file_specific_null_pins(inputs):
    inventory = runner.official_inventory(_find_tpcds_answers_dir(), inputs)
    assert set(inventory) == set(runner.STATEMENTS)
    assert inventory["64"][0]["null_tokens"] == ["[NULL]"]
    assert inventory["73"][0]["null_tokens"] == ["-"]


