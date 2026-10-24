from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[3]
SCHEMA = ROOT / "quality" / "docs-data.schema.json"
FAST_BENCHMARKS = frozenset({"clickbench", "datavault"})

pytestmark = [pytest.mark.unit]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


exporter = _load("export_docs_data")
gqd = _load("generate_query_docs")
gcd = _load("generate_compat_docs")
gts = _load("generate_tuning_support_matrix")


def _validator() -> Any:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


def _errors(data: dict) -> list[str]:
    return [f"{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in _validator().iter_errors(data)]


@pytest.fixture(scope="module")
def data() -> dict:
    return exporter.build_docs_data(set(FAST_BENCHMARKS))


@pytest.fixture(scope="module")
def pages() -> dict[str, str]:
    return gqd.render_all(set(FAST_BENCHMARKS))


@pytest.mark.fast
class TestSchema:
    def test_export_validates(self, data):
        assert _errors(data) == []

    def test_schema_rejects_unknown_and_missing_members(self, data):
        broken = json.loads(exporter.render_json(data))
        broken["queries"]["benchmarks"][0]["queries"][0]["surprise"] = 1
        del broken["tuning"]["aliases"]
        assert len(_errors(broken)) == 2


@pytest.mark.fast
class TestDeterminism:
    def test_two_exports_are_byte_identical(self, data):
        again = exporter.build_docs_data(set(FAST_BENCHMARKS))
        assert exporter.render_json(again) == exporter.render_json(data)

    def test_check_flag_detects_drift(self, data, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(exporter, "build_docs_data", lambda benchmarks=None: data)
        target = tmp_path / "docs-data.json"
        assert exporter.main(["--output", str(target)]) == 0
        assert exporter.main(["--output", str(target), "--check"]) == 0
        target.write_text(target.read_text(encoding="utf-8") + " ", encoding="utf-8")
        assert exporter.main(["--output", str(target), "--check"]) == 1
        assert "out of date" in capsys.readouterr().err

    def test_check_requires_output(self):
        with pytest.raises(SystemExit):
            exporter.main(["--check"])


@pytest.mark.fast
class TestQueriesMatchGeneratorInputs:
    def test_benchmark_set_and_counts_match_catalog(self, data):
        from benchbox.core.query_catalog import list_query_ids

        by_id = {b["id"]: b for b in data["queries"]["benchmarks"]}
        assert set(by_id) == set(FAST_BENCHMARKS)
        for benchmark_id, record in by_id.items():
            ids = list_query_ids(benchmark_id)
            assert [q["id"] for q in record["queries"]] == ids
            assert record["query_count"] == len(ids)

    def test_every_query_page_prints_the_exported_facts(self, data, pages):
        for benchmark in data["queries"]["benchmarks"]:
            for query in benchmark["queries"]:
                page = pages[f"{benchmark['id']}/{query['slug']}.md"]
                assert f"# {query['heading']}\n" in page
                if query["description"]:
                    assert query["description"] in page
                if query["source_path"]:
                    assert f"[`{query['source_path']}`]" in page
                else:
                    assert "## Source" not in page
                assert (f"print(bench.get_query({query['id']}, " in page) is query["seed_driven"]
                assert f"[{query['native_key_repr']}]" in page
                if query["sql"] is None:
                    assert "could not be rendered as standalone SQL" in page
                else:
                    assert query["sql"]["pretty_sql"] in page
                    if query["reference_dialect_label"]:
                        assert f"Rendered for **{query['reference_dialect_label']}**" in page
                if query["dataframe"] is None:
                    assert "No DataFrame implementation is registered" in page
                else:
                    assert query["dataframe"]["source"] in page
                    assert query["dataframe"]["family"] in {r["family"] for r in query["dataframe_renderings"]}

    def test_benchmark_index_matches_grouping_and_notice(self, data, pages):
        for benchmark in data["queries"]["benchmarks"]:
            index = pages[f"{benchmark['id']}/index.md"]
            assert f"# {benchmark['display_name']} Query Templates" in index
            assert (data["queries"]["tpc_notice"] in index) is benchmark["tpc_derived"]
            assert f"../../{benchmark['overview_slug']}.md" in index
            assert f"{benchmark['query_count']} {benchmark['display_name']} queries" in index
            for group in benchmark["groups"] or []:
                assert f"- [{group['name']}](group-{group['slug']}.md) ({len(group['query_ids'])})" in index
                assert f"{benchmark['id']}/group-{group['slug']}.md" in pages
            if benchmark["groups"] is None:
                assert "| Query | Name |" in index

    def test_top_index_lists_every_exported_benchmark(self, data, pages):
        for benchmark in data["queries"]["benchmarks"]:
            row = f"| [{benchmark['display_name']}]({benchmark['id']}/index.md) | {benchmark['query_count']} |"
            assert row in pages["index.md"]

    def test_dataframe_and_sql_renderings_come_from_core_apis(self, data):
        from benchbox.core.query_catalog import get_dataframe_render, get_sql_render

        for benchmark in data["queries"]["benchmarks"]:
            for query in benchmark["queries"]:
                render = get_sql_render(benchmark["id"], query["id"], bulk=True)
                assert (query["sql"] or {}).get("sql") == (render.sql if render else None)
                assert (query["sql"] or {}).get("dialect") == (render.dialect if render else None)
                df = get_dataframe_render(benchmark["id"], query["id"])
                assert (query["dataframe"] or {}).get("source") == (df.source if df else None)

    def test_recipe_inputs_match_generator(self, data):
        for benchmark in data["queries"]["benchmarks"]:
            import_line, class_name = gqd._recipe_class(benchmark["id"])
            assert benchmark["recipe"] == {"import_line": import_line, "class_name": class_name}
            assert benchmark["overview_slug"] == gqd._overview_slug(benchmark["id"])
            assert benchmark["tpc_derived"] == (benchmark["id"] in gqd._TPC_DERIVED)

    def test_global_constants_match_generator(self, data):
        queries = data["queries"]
        assert queries["tpc_notice"] == gqd._TPC_NOTICE
        assert queries["group_threshold"] == gqd._GROUP_THRESHOLD
        assert queries["bucket_size"] == gqd._BUCKET_SIZE


@pytest.mark.fast
class TestTpcdsNotes:
    def test_notes_export_for_flagged_queries(self):
        from benchbox.core.tpcds.c_tools import SUBSCALE_TEMPLATE_OVERRIDES
        from benchbox.core.tpcds.qualification.classification import KNOWN, RETURNS_DIFFERENCE

        flagged_answer = next(qid for qid, known in KNOWN.items() if RETURNS_DIFFERENCE in known.classes)
        flagged_subscale = next(
            template.removeprefix("query").removesuffix(".tpl") for template in sorted(SUBSCALE_TEMPLATE_OVERRIDES)
        )
        answer = gqd._answer_set_note("tpcds", flagged_answer)
        subscale = gqd._subscale_note("tpcds", flagged_subscale)
        assert answer and subscale

        record = exporter._query_record(gqd, "tpcds", "TPC-DS", flagged_answer, {})
        assert record["answer_set_note"] == answer[0]
        record = exporter._query_record(gqd, "tpcds", "TPC-DS", flagged_subscale, {})
        assert record["subscale_note"] == subscale[0]


@pytest.mark.fast
class TestCompatMatchesGeneratorInputs:
    def test_rules_match_registry_rows_and_generated_tables(self, data):
        gcd._load_all_rules()
        rows = gcd._rule_rows()
        rules = data["compat"]["rules"]
        assert [
            (
                r["platform"],
                r["phase"],
                r["scope"],
                r["action"],
                r["support_level"],
                r["failure_mode"],
                r["rule_id"],
                r["reason"],
            )
            for r in rules
        ] == rows
        matrix = gcd._render_capability_matrix(rows)
        skip = gcd._render_skip_reference(rows)
        for rule in rules:
            assert (
                f"| {rule['phase']} | {rule['scope']} | {rule['action']} | {rule['support_level']} | "
                f"{rule['failure_mode']} | `{rule['rule_id']}` |"
            ) in matrix
            if (
                rule["support_level"]
                in data["compat"]["will_not_run_levels"] + data["compat"]["runs_with_caveats_levels"]
            ):
                reason = rule["reason"].replace("|", "\\|").replace("\n", " ").strip()
                assert (
                    f"| {rule['support_level']} | {rule['scope']} | {rule['phase']} | {reason} | `{rule['rule_id']}` |"
                    in skip
                )

    def test_scope_fields_round_trip_to_label(self, data):
        for rule in data["compat"]["rules"]:
            assert rule["scope"] == gcd._scope_label(rule["benchmark"], rule["query_id"])

    def test_phase_order_matches_generator(self, data):
        assert data["compat"]["phase_order"] == gcd._PHASE_ORDER


@pytest.mark.fast
class TestTuningMatchesGeneratorInputs:
    def test_platform_rows_match_generated_matrix(self, data):
        text = gts.render()
        for entry in data["tuning"]["platforms"]:
            applied = ", ".join(
                item["type"] + (" (with sorted ingestion)" if item["requires_sorted_ingestion"] else "")
                for item in entry["applied"]
            )
            not_applied = ", ".join(entry["not_applied"])
            templates = ", ".join(entry["shipped_templates"]) or "none"
            introspector = {True: "yes", False: "no", None: "no adapter"}[entry["catalog_introspector"]]
            row = (
                f"| `{entry['platform']}` | {templates} | {applied or 'none'} | {not_applied or 'none'} | "
                f"{'applied' if entry['session_config'] else '-'} | {introspector} | "
                f"{'yes' if entry['can_reach_applied_verified'] else 'no'} |\n"
            )
            assert row in text

    def test_aliases_and_platform_set_match_registry(self, data):
        from benchbox.core.tuning.capability_registry import (
            PLATFORM_ALIASES,
            PLATFORM_TUNING_CAPABILITIES,
            SESSION_CONFIG_CAPABILITIES,
        )

        assert data["tuning"]["aliases"] == dict(PLATFORM_ALIASES)
        assert {e["platform"] for e in data["tuning"]["platforms"]} == (
            PLATFORM_TUNING_CAPABILITIES.keys() | SESSION_CONFIG_CAPABILITIES.keys()
        )


@pytest.mark.medium
class TestFullCatalog:
    def test_full_export_validates_and_covers_every_generated_page(self):
        full = exporter.build_docs_data()
        assert _errors(full) == []
        pages = gqd.render_all()
        exported = {f"{b['id']}/{q['slug']}.md" for b in full["queries"]["benchmarks"] for q in b["queries"]}
        generated_queries = {
            rel for rel in pages if rel.count("/") == 1 and not rel.endswith(("/index.md",)) and "/group-" not in rel
        }
        assert exported == generated_queries
        assert exporter.render_json(full) == exporter.render_json(exporter.build_docs_data())
