from __future__ import annotations

import json
import math
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest
import yaml

from benchbox.core.expected_results.loader import TpcdsAnswerBlock
from benchbox.core.tpcds.qualification import runner

pytestmark = [pytest.mark.unit, pytest.mark.medium, pytest.mark.tpcds]


def test_reference_sql_types_control_text_conversion_without_converting_numeric_strings():
    connection = duckdb.connect()
    try:
        cursor = connection.execute(
            "SELECT '0007' AS code, 12.34::DECIMAL(8,2), 7::INTEGER, DATE '2000-01-02', NULL::DOUBLE, 'NaN'::DOUBLE"
        )
        types = [str(column[1]) for column in cursor.description]
        actual = cursor.fetchall()
        answer = {
            "columns": ["code", "amount", "count", "date", "missing", "nan"],
            "rows": [["0007", "12.34", "7", "2000-01-02", "%", "NaN"]],
            "null_tokens": ["%"],
        }
        expected = runner.convert_rows(answer, types)
        assert expected[0][:5] == ("0007", Decimal("12.34"), 7, date(2000, 1, 2), None)
        assert math.isnan(expected[0][5]) and math.isnan(actual[0][5])
        assert runner.compare_rows([expected[0][:5]], [actual[0][:5]], "1", "SELECT 1")["status"] == "match"
        assert runner.compare_rows(expected, actual, "1", "SELECT 1")["status"] == "mismatch"
        wrong = [(*actual[0][:4], float("nan"), actual[0][5])]
        assert runner.compare_rows(expected, wrong, "1", "SELECT 1")["status"] == "mismatch"
    finally:
        connection.close()


def test_pinned_null_spellings_are_explicit_and_string_values_remain_strings():
    assert runner.convert_cell("NULL", "VARCHAR", []) == "NULL"
    assert runner.convert_cell("NULL", "VARCHAR", ["NULL"]) is None
    assert runner.convert_cell("%", "VARCHAR", []) == "%"
    with pytest.raises(ValueError):
        runner.convert_cell("unknown-null", "DOUBLE", ["%"])


@pytest.mark.parametrize(
    ("text", "kind"), [("1.5", "INTEGER"), ("NaN", "DECIMAL(8,2)"), ("0", "BLOB"), ("2001-02-29", "DATE")]
)
def test_malformed_or_unknown_typed_cells_refuse_conversion(text, kind):
    with pytest.raises(ValueError):
        runner.convert_cell(text, kind, [])


def test_malformed_official_width_refuses_conversion():
    with pytest.raises(ValueError, match="width"):
        runner.convert_rows({"columns": ["a", "b"], "rows": [["1"]], "null_tokens": []}, ["INTEGER", "INTEGER"])


def test_printed_precision_is_reported_as_mismatch_without_rounding_reference():
    result = runner.compare_rows([(Decimal("325552631"),)], [(Decimal("325552630.64"),)], "86", "SELECT 1")
    assert result["status"] == "mismatch"
    assert "325552630.64" in result["detail"]


def test_reversed_order_and_wrong_values_fail_the_existing_validator():
    sql = "SELECT a,b FROM x ORDER BY a"
    expected = [(1, "a"), (2, "b")]
    assert runner.compare_rows(expected, list(reversed(expected)), "1", sql)["status"] == "mismatch"
    assert runner.compare_rows(expected, [(1, "a"), (2, "wrong")], "1", sql)["status"] == "mismatch"


@pytest.mark.parametrize("printed", [False, True])
def test_sql_decimal_and_typed_printed_rows_share_numeric_tie_order_without_hiding_defects(printed):
    connection = duckdb.connect()
    sql = (
        "SELECT channel,id,amount FROM (VALUES "
        "('catalog',NULL,9::DECIMAL(15,2)), ('catalog',NULL,32::DECIMAL(15,2)), "
        "('web','customer',0::DECIMAL(15,2))) AS fixture(channel,id,amount) ORDER BY channel,id"
    )
    cursor = connection.execute(sql)
    types = [str(column[1]) for column in cursor.description]
    reference = cursor.fetchall()
    if printed:
        reference = runner.convert_rows(
            {
                "columns": ["channel", "id", "amount"],
                "rows": [["catalog", "NULL", "9.00"], ["catalog", "NULL", "32.00"], ["web", "customer", "0.00"]],
                "null_tokens": ["NULL"],
            },
            types,
        )
    actual = [("catalog", None, 9.0), ("catalog", None, 32.0), ("web", "customer", 0.0)]
    assert runner.compare_rows(reference, actual, "77", sql)["status"] == "match"
    wrong = [("catalog", None, 9.25), *actual[1:]]
    assert runner.compare_rows(reference, wrong, "77", sql)["status"] == "mismatch"
    assert runner.compare_rows(reference, list(reversed(actual)), "77", sql)["status"] == "mismatch"


def test_parameter_hash_covers_values_and_ignores_dictionary_insertion_order():
    assert runner.parameter_hash({"YEAR.01": "2002", "CATEGORY.01": "Books"}) == runner.parameter_hash(
        {"CATEGORY.01": "Books", "YEAR.01": "2002"}
    )
    assert runner.parameter_hash({"YEAR.01": "2002"}) != runner.parameter_hash({"YEAR.01": "1999"})


def test_parameter_file_changes_cannot_claim_pinned_qualification_values(tmp_path):
    path = tmp_path / "params.json"
    path.write_text('{"values": {"1": {"YEAR.01": "2002"}}}', encoding="utf-8")
    with pytest.raises(ValueError, match="pinned"):
        runner.qualification_parameters(path, {"parameters_sha256": "0" * 64})
    with pytest.raises(ValueError, match="inventory"):
        runner.qualification_parameters(path, {"parameters_sha256": runner.sha256(path)})


def test_missing_official_inventory_fails_before_a_partial_cache_can_run(tmp_path):
    with pytest.raises(ValueError, match="inventory"):
        runner.official_inventory(tmp_path, {"answers": {"1.ans": {"sha256": "0" * 64}}})


@pytest.mark.parametrize("change", ["missing", "extra"])
def test_template_pin_inventory_must_cover_exactly_all_99_queries_before_preparation(tmp_path, monkeypatch, change):
    inputs = json.loads(runner._INPUTS.read_text(encoding="utf-8"))
    if change == "missing":
        del inputs["templates"]["query99.tpl"]
    else:
        inputs["templates"]["query100.tpl"] = inputs["templates"]["query99.tpl"]
    manifest = tmp_path / "inputs.json"
    manifest.write_text(json.dumps(inputs), encoding="utf-8")
    monkeypatch.setattr(runner, "_INPUTS", manifest)

    def entered_preparation(**kwargs):
        raise AssertionError("Template inventory must be checked before parameter preparation")

    monkeypatch.setattr(runner, "qualification_parameters", entered_preparation)
    with pytest.raises(ValueError, match="template inventory"):
        runner.prepare(_ANSWERS)


def test_official_hash_changes_fail_before_parsing(tmp_path):
    (tmp_path / "1.ans").write_text("A|B\n1|2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="hash differs"):
        runner.official_inventory(tmp_path, {"answers": {"1.ans": {"sha256": "0" * 64}}})


def test_multi_statement_blocks_remain_separate_and_single_statement_pages_keep_file_order():
    first = TpcdsAnswerBlock(("A",), (("1",), ("2",)))
    second = TpcdsAnswerBlock(("A",), (("3",),))
    inputs = {"block_rows": {"39": [2, 1], "98": [2, 1]}}
    assert runner.mapped_blocks(39, (first, second), inputs) == (first, second)
    assert runner.mapped_blocks(98, (first, second), inputs) == (TpcdsAnswerBlock(("A",), (("1",), ("2",), ("3",))),)
    with pytest.raises(ValueError, match="shape"):
        runner.mapped_blocks(39, (first,), inputs)
    with pytest.raises(ValueError, match="columns"):
        runner.mapped_blocks(98, (first, TpcdsAnswerBlock(("B",), (("3",),))), inputs)
    with pytest.raises(ValueError, match="statement blocks"):
        runner.mapped_blocks(23, (first,), inputs)


def test_query_timeout_terminates_a_stalled_worker_and_retains_the_query(tmp_path):
    events = tmp_path / "events.jsonl"
    program = (
        "import json,time; from pathlib import Path; p=Path("
        + repr(str(events))
        + "); p.write_text(json.dumps({'event':'query_start','query':'39b'})+'\\n'); time.sleep(10)"
    )
    assert runner.supervise([sys.executable, "-c", program], events, tmp_path / "worker.log", 0.15, 2) == 1
    assert json.loads(events.read_text(encoding="utf-8").splitlines()[-1]) == {
        "event": "timeout",
        "query": "39b",
        "status": "error",
    }


def test_loading_or_preflight_is_bounded_by_the_overall_timeout(tmp_path):
    events = tmp_path / "events.jsonl"
    assert (
        runner.supervise([sys.executable, "-c", "import time;time.sleep(10)"], events, tmp_path / "worker.log", 1, 0.15)
        == 1
    )
    assert json.loads(events.read_text(encoding="utf-8"))["query"] is None


def test_clean_worker_exit_retains_resource_evidence(tmp_path):
    events = tmp_path / "events.jsonl"
    assert runner.supervise([sys.executable, "-c", "pass"], events, tmp_path / "worker.log", 1, 2) == 0
    event = json.loads(events.read_text(encoding="utf-8"))
    assert event["event"] == "worker_exit" and event["returncode"] == 0
    assert event["peak_rss_bytes"] >= 0


_ANSWERS = Path(__file__).resolve().parents[4] / "_sources/tpc-ds/answer_sets"


@pytest.mark.skipif(not _ANSWERS.exists(), reason="official answer files are absent")
def test_frozen_official_pack_maps_all_103_statements_with_both_null_order_files():
    inventory = runner.official_inventory(_ANSWERS, json.loads(runner._INPUTS.read_text(encoding="utf-8")))
    assert len(inventory) == 103
    assert len(inventory["39a"][0]["rows"]) == 243
    assert len(inventory["39b"][0]["rows"]) == 14
    assert {len(answer["rows"]) for answer in inventory["98"]} == {2516}
    assert {answer["null_order"] for answer in inventory["86"]} == {"first", "last"}
    assert all(answer["sha256"] and answer["file"] for answers in inventory.values() for answer in answers)


def test_scheduled_lane_fails_on_gate_failures_stays_private_and_preserves_failure_artifacts():
    root = Path(__file__).resolve().parents[4]
    workflow = yaml.safe_load((root / ".github/workflows/tpcds-official-qualification.yml").read_text(encoding="utf-8"))
    triggers = workflow.get("on", workflow.get(True))
    assert triggers["schedule"] and "workflow_dispatch" in triggers
    assert "pull_request" not in triggers and "push" not in triggers
    job = workflow["jobs"]["qualification-report"]
    assert job["timeout-minutes"] == 25
    report = next(step for step in job["steps"] if step.get("id") == "report")
    assert "continue-on-error" not in report
    assert "--overall-seconds 1200" in report["run"] and "--query-seconds 120" in report["run"]
    assert "|| true" not in report["run"]
    uploads = [step for step in job["steps"] if "upload-artifact@" in step.get("uses", "")]
    assert len(uploads) == 1 and uploads[0]["if"] == "always()"
    assert "*.jsonl" in uploads[0]["with"]["path"]
    assert "cache" not in uploads[0]["with"]["path"]
    summary_step = job["steps"][-2]
    assert summary_step["if"] == "always()" and "REPORT_OUTCOME" in summary_step["env"]
    assert "benchbox.core.tpcds.qualification.summary" in summary_step["run"]


def _appendix_fixture():
    return (
        " ".join(
            f"B.{number} query{number}.tpl Qualification Substitution Parameters: • YEAR.01 = 2002 Comment: YEAR.01=1999."
            for number in range(1, 100)
        )
        + " Appendix C:"
    )


def test_appendix_extraction_cannot_replace_a_table_value_with_following_comment():
    from scripts.audit_tpcds_qualification import extract_appendix_values

    values = extract_appendix_values(_appendix_fixture())
    assert set(values) == {str(number) for number in range(1, 100)}
    assert all(row == {"YEAR.01": "2002"} for row in values.values())


@pytest.mark.parametrize("replacement", ["B.75 query75.tpl", "Qualification Substitution Parameters: • YEAR.01 = 2002"])
def test_appendix_extraction_rejects_missing_sections_or_tables(replacement):
    from scripts.audit_tpcds_qualification import extract_appendix_values

    with pytest.raises(ValueError):
        extract_appendix_values(_appendix_fixture().replace(replacement, "", 1))


def test_appendix_extraction_pads_suffixes_and_preserves_four_hundred_zip_values():
    from scripts.audit_tpcds_qualification import extract_appendix_values

    zips = " ".join(f"• ZIP.{number} = {number:05d}" for number in range(1, 401))
    text = _appendix_fixture().replace(
        "B.8 query8.tpl Qualification Substitution Parameters: • YEAR.01 = 2002",
        "B.8 query8.tpl Qualification Substitution Parameters: " + zips,
    )
    values = extract_appendix_values(text)
    assert len(values["8"]) == 400
    assert values["8"]["ZIP.01"] == "00001" and values["8"]["ZIP.400"] == "00400"


def test_native_datafusion_arrow_collection_uses_shared_scalar_and_column_order_semantics():
    import pyarrow as pa
    from datafusion import SessionContext

    from benchbox.core.equivalence.dataframe_surface import materialize_rows

    context = SessionContext()
    actual = context.sql("SELECT 7::BIGINT AS z, NULL::VARCHAR AS a, 12.34::DECIMAL(8,2) AS amount")
    table = pa.Table.from_batches(actual.collect(), schema=actual.schema())
    assert materialize_rows(table) == [(7, None, 12.34)]
    empty = context.sql("SELECT 7::BIGINT AS z WHERE false")
    assert materialize_rows(pa.Table.from_batches(empty.collect(), schema=empty.schema())) == []


def test_actual_qualification_parameter_map_matches_the_complete_specification_receipt():
    document = runner.qualification_parameters()
    assert len(document["values"]) == 99
    assert sum(map(len, document["values"].values())) == 829
    assert document["values"]["75"] == {"CATEGORY.01": "Books", "YEAR.01": "2002"}


@pytest.mark.parametrize("nested", [False, True])
def test_worker_binds_real_query_1_and_detects_wrong_official_values(tmp_path, monkeypatch, nested):
    import polars as pl

    from benchbox.core.equivalence.builders import base
    from benchbox.core.tpcds.c_tools import DSQGenBinary
    from benchbox.core.tpcds.schema.registry import TABLES
    from benchbox.core.tpcds.schema.tables import DBGEN_VERSION
    from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter
    from benchbox.tpcds import TPCDS

    values = runner.qualification_parameters()["values"]["1"]
    benchmark = TPCDS(scale_factor=1.0)
    raw = DSQGenBinary().generate_with_parameters(1, values)
    sql = benchmark._impl.translate_query_text(raw, "netezza", "duckdb")
    sql = benchmark._impl._apply_target_dialect_overrides(1, sql, "duckdb")
    tables = {
        "date_dim": pl.DataFrame({"d_date_sk": [1], "d_year": [2000]}),
        "customer": pl.DataFrame({"c_customer_sk": [1, 2], "c_customer_id": ["C1", "C2"]}),
        "store": pl.DataFrame({"s_store_sk": [1], "s_state": ["TN"]}),
        "store_returns": pl.DataFrame(
            {
                "sr_customer_sk": [1, 2],
                "sr_store_sk": [1, 1],
                "sr_returned_date_sk": [1, 1],
                "sr_return_amt": [100.0, 10.0],
            }
        ),
    }
    context = PolarsDataFrameAdapter().create_context()
    for name, frame in tables.items():
        context.register_table(name, frame.lazy())
    binary = tmp_path / "fixture-input"
    binary.write_text("fixture", encoding="utf-8")
    data_dir = tmp_path / "data"
    generated = []
    for part in ("first", "second"):
        directory = data_dir / part
        directory.mkdir(parents=True)
        path = directory / "customer.dat"
        path.write_text(part, encoding="utf-8")
        generated.append(path)
    job = tmp_path / "inputs.json"
    prepared = {
        "binaries": {"dsqgen": {"path": str(binary), "sha256": runner.sha256(binary)}},
        "parameters": {"values": {"1": values}},
        "sql": {"1": sql},
        "answers": {
            "1": [
                {
                    "file": "fixture.ans",
                    "sha256": runner.sha256(binary),
                    "null_order": "unspecified",
                    "null_tokens": [],
                    "columns": ["C_CUSTOMER_ID"],
                    "rows": [["C1"]],
                }
            ]
        },
    }
    job.write_text(json.dumps(prepared), encoding="utf-8")

    class FixtureBenchmark:
        def __init__(self, **kwargs):
            self.tables = dict.fromkeys((table.name for table in TABLES if table.name != DBGEN_VERSION.name), generated)

        def generate_data(self):
            return [[path] for path in generated] if nested else generated

    def loaded(*args, **kwargs):
        connection = duckdb.connect()
        for name, frame in tables.items():
            connection.register(name, frame.to_arrow())
        return connection

    monkeypatch.setattr("benchbox.tpcds.TPCDS", FixtureBenchmark)
    monkeypatch.setattr(base, "_load_duckdb_cell", loaded)
    monkeypatch.setattr(PolarsDataFrameAdapter, "load_benchmark_into_context", lambda *args, **kwargs: context)
    monkeypatch.setattr(runner, "STATEMENTS", ("1",))
    events = tmp_path / "first.jsonl"
    assert runner.worker(job, "polars", events, data_dir) == 0
    loaded_event = json.loads(events.read_text(encoding="utf-8").splitlines()[0])
    assert loaded_event["data_sha256"] == {
        "first/customer.dat": runner.sha256(generated[0]),
        "second/customer.dat": runner.sha256(generated[1]),
    }
    assert len(loaded_event["table_data_sha256"]) == 24
    result = json.loads(events.read_text(encoding="utf-8").splitlines()[-1])
    assert result["status"] == "match" and result["dataframe_to_sql"]["rows"] == 1
    assert result["parameters"] == values and result["df_parameters"]["year"] == 2000
    valid_answer = dict(prepared["answers"]["1"][0])
    prepared["answers"]["1"][0]["columns"] = ["BROKEN", "WIDTH"]
    prepared["answers"]["1"].append(valid_answer)
    job.write_text(json.dumps(prepared), encoding="utf-8")
    malformed_events = tmp_path / "malformed.jsonl"
    assert runner.worker(job, "polars", malformed_events, data_dir) == 1
    malformed = json.loads(malformed_events.read_text(encoding="utf-8").splitlines()[-1])
    assert malformed["status"] == "error"
    assert malformed["dataframe_to_sql"] == result["dataframe_to_sql"]
    assert malformed["sql_sha256"] == result["sql_sha256"]
    assert malformed["parameters"] == values
    assert malformed["official_files"][0]["status"] == "error"
    assert "width" in malformed["official_files"][0]["detail"]
    assert malformed["official_files"][1]["sql_to_printed"]["status"] == "match"
    prepared["answers"]["1"].pop()
    prepared["answers"]["1"][0]["columns"] = ["C_CUSTOMER_ID"]
    prepared["answers"]["1"][0]["rows"] = [["wrong-customer"]]
    job.write_text(json.dumps(prepared), encoding="utf-8")
    assert runner.worker(job, "polars", tmp_path / "wrong.jsonl", tmp_path / "data") == 1
    monkeypatch.setattr(FixtureBenchmark, "generate_data", lambda self: [generated[0], generated[0]])
    with pytest.raises(ValueError, match="Duplicate qualification data path"):
        runner.worker(job, "polars", tmp_path / "duplicate.jsonl", data_dir)
    monkeypatch.setattr(FixtureBenchmark, "generate_data", lambda self: [[binary]])
    with pytest.raises(ValueError, match="not in the subpath"):
        runner.worker(job, "polars", tmp_path / "outside.jsonl", data_dir)
    monkeypatch.setattr(FixtureBenchmark, "generate_data", lambda self: generated)
    monkeypatch.setattr(
        FixtureBenchmark,
        "__init__",
        lambda self, **kwargs: setattr(
            self,
            "tables",
            dict.fromkeys(
                (table.name for table in TABLES if table.name not in {DBGEN_VERSION.name, "customer"}), generated
            ),
        ),
    )
    with pytest.raises(ValueError, match="Missing qualification schema table: customer"):
        runner.worker(job, "polars", tmp_path / "missing-table.jsonl", data_dir)


@pytest.mark.parametrize("table_changed", [False, True])
def test_engine_identity_records_generator_metadata_but_rejects_schema_table_drift(
    tmp_path, monkeypatch, table_changed
):
    from benchbox.core.tpcds.schema.registry import TABLES
    from benchbox.core.tpcds.schema.tables import DBGEN_VERSION

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    table_files = {}
    for table in TABLES:
        if table.name == DBGEN_VERSION.name:
            continue
        path = data_dir / f"{table.name}.dat"
        path.write_text("stable-data", encoding="utf-8")
        table_files[table.name] = [path]
    chunk = data_dir / "customer.part2.dat"
    chunk.write_text("second-chunk", encoding="utf-8")
    table_files["customer"].append(chunk)
    assert len(table_files) == 24

    def supervised(command, events, log, *limits):
        if "--prepare-worker" in command:
            (tmp_path / "inputs.json").write_text("{}", encoding="utf-8")
            return 0
        engine = command[command.index("--worker") + 1]
        if engine == "pandas" and table_changed:
            table_files["customer"][1].write_text("changed-table-content", encoding="utf-8")
        identity = {
            name: {path.relative_to(data_dir).as_posix(): runner.sha256(path) for path in paths}
            for name, paths in table_files.items()
        }
        version = data_dir / "dbgen_version.dat"
        version.write_text(f"generation-timestamp-{engine}", encoding="utf-8")
        metadata = {"dbgen_version.dat": runner.sha256(version)}
        runner.emit(
            events,
            {
                "event": "loaded",
                "data_sha256": {
                    **{path: value for files in identity.values() for path, value in files.items()},
                    **metadata,
                },
                "table_data_sha256": identity,
                "generator_metadata_sha256": metadata,
            },
        )
        runner.emit(events, {"event": "query_result", "query": "1", "status": "match"})
        return 0

    monkeypatch.setattr(runner, "supervise", supervised)
    monkeypatch.setenv("BENCHBOX_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(runner, "STATEMENTS", ("1",))
    monkeypatch.setattr(
        runner, "_ENGINES", {name: definition for name, definition in runner._ENGINES.items() if name != "datafusion"}
    )
    assert runner.main(["--output-dir", str(tmp_path)]) == int(table_changed)
    events = [json.loads(line) for line in (tmp_path / "pandas.jsonl").read_text().splitlines()]
    assert any(event["event"] == "generation_metadata_difference" for event in events)
    assert any(event["event"] == "input_drift" for event in events) == table_changed


def test_resident_memory_limit_terminates_worker_and_records_failure(tmp_path):
    events = tmp_path / "events.jsonl"
    result = runner.supervise(
        [sys.executable, "-c", "import time;time.sleep(10)"], events, tmp_path / "worker.log", 1, 2, 1
    )
    assert result == 1
    event = json.loads(events.read_text(encoding="utf-8").splitlines()[-1])
    assert event["event"] == "memory_limit" and event["status"] == "error"
    assert event["rss_bytes"] > 1


def test_compare_rows_checks_order_for_a_case_sort_key():
    sql = "SELECT total, category, level, rnk FROM t ORDER BY level DESC, CASE WHEN level = 0 THEN category END, rnk"
    columns = [("total", "DOUBLE"), ("category", "VARCHAR"), ("level", "INTEGER"), ("rnk", "BIGINT")]
    reference = [(90.0, None, 2, 1), (60.0, "Music", 1, 1), (30.0, "Books", 1, 2)]
    misordered = [reference[0], reference[2], reference[1]]
    assert runner.compare_rows(reference, reference, "86", sql, columns)["status"] == "match"
    result = runner.compare_rows(reference, misordered, "86", sql, columns)
    assert result["status"] == "mismatch"
    assert "breaks the ORDER BY" in result["detail"]
    assert runner.compare_rows(reference, misordered, "86", sql)["status"] == "match"


def test_display_rounding_classifies_only_printed_precision():
    types = ["VARCHAR", "DOUBLE", "DECIMAL(17,2)"]
    answer = {"columns": ["k", "ratio", "total"], "rows": [["a", "2551.86333", "31428816.3"]], "null_tokens": []}
    expected = runner.convert_rows(answer, types)
    actual = [("a", 2551.8633333333332, Decimal("31428816.29"))]
    assert runner.compare_rows(expected, actual, "13", "SELECT 1")["status"] == "mismatch"
    assert (
        runner.compare_rows(expected, runner.display_rounded(actual, answer, types), "13", "SELECT 1")["status"]
        == "match"
    )

    wrong = [("a", 2551.8643333333332, Decimal("31428816.29"))]
    assert (
        runner.compare_rows(expected, runner.display_rounded(wrong, answer, types), "13", "SELECT 1")["status"]
        == "mismatch"
    )


def test_display_rounding_leaves_rows_unpaired_when_counts_differ():
    answer = {"columns": ["ratio"], "rows": [["0.5"], ["0.25"]], "null_tokens": []}
    actual = [(0.50001,)]
    assert runner.display_rounded(actual, answer, ["DOUBLE"]) == actual


def test_kill_worker_tolerates_a_worker_that_already_exited(monkeypatch):
    class ExitedProcess:
        pid = 4242
        waited = False

        def kill(self):
            raise ProcessLookupError

        def wait(self):
            self.waited = True

    def vanished(pid, sig):
        raise ProcessLookupError

    monkeypatch.setattr(runner.os, "killpg", vanished, raising=False)
    process = ExitedProcess()
    runner._kill_worker(process)
    assert process.waited


