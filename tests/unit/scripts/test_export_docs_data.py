from __future__ import annotations

import importlib.util
import json
import sys
from functools import cache
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[3]
SCHEMA = ROOT / "quality" / "docs-data.schema.json"
FAST_BENCHMARKS = frozenset({"clickbench", "datavault"})

pytestmark = [pytest.mark.unit, pytest.mark.medium]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


exporter = _load("export_docs_data")
sources = _load("docs_data_sources")


def _validator() -> Any:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


def _errors(data: dict) -> list[str]:
    return [f"{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in _validator().iter_errors(data)]


@pytest.fixture(scope="module")
def data() -> dict:
    return exporter.build_docs_data(set(FAST_BENCHMARKS))


class TestSchema:
    def test_export_validates(self, data):
        assert _errors(data) == []

    def test_schema_rejects_unknown_and_missing_members(self, data):
        broken = json.loads(exporter.render_json(data))
        broken["queries"]["benchmarks"][0]["queries"][0]["surprise"] = 1
        del broken["tuning"]["aliases"]
        assert len(_errors(broken)) == 2


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


class TestQueriesMatchCatalog:
    def test_benchmark_set_and_counts_match_catalog(self, data):
        from benchbox.core.query_catalog import list_query_ids

        by_id = {b["id"]: b for b in data["queries"]["benchmarks"]}
        assert set(by_id) == set(FAST_BENCHMARKS)
        for benchmark_id, record in by_id.items():
            ids = list_query_ids(benchmark_id)
            assert [q["id"] for q in record["queries"]] == ids
            assert record["query_count"] == len(ids)

    def test_tsbs_sql_is_stable_across_catalog_resets(self, monkeypatch):
        from benchbox.core import query_catalog

        def export():
            for name in ("_benchmark_instance", "_query_dict", "_raw_query_keys"):
                monkeypatch.setattr(query_catalog, name, cache(getattr(query_catalog, name).__wrapped__))
            return exporter.export_queries({"tsbs_devops"})["benchmarks"][0]["queries"]

        first = export()
        assert len(first) == 18
        assert export() == first

    def test_exported_query_facts_are_internally_consistent(self, data):
        for benchmark in data["queries"]["benchmarks"]:
            for query in benchmark["queries"]:
                assert query["heading"].startswith(benchmark["display_name"])
                assert (query["reference_dialect_label"] is None) or query["sql"] is not None
                if query["dataframe"] is not None:
                    assert query["dataframe"]["family"] in {r["family"] for r in query["dataframe_renderings"]}
            for group in benchmark["groups"] or []:
                assert group["query_ids"]

    def test_dataframe_and_sql_renderings_come_from_core_apis(self, data):
        from benchbox.core.query_catalog import get_dataframe_render, get_sql_render

        for benchmark in data["queries"]["benchmarks"]:
            for query in benchmark["queries"]:
                render = get_sql_render(benchmark["id"], query["id"], bulk=True)
                assert (query["sql"] or {}).get("sql") == (render.sql if render else None)
                assert (query["sql"] or {}).get("dialect") == (render.dialect if render else None)
                df = get_dataframe_render(benchmark["id"], query["id"])
                assert (query["dataframe"] or {}).get("source") == (df.source if df else None)

    def test_recipe_inputs_match_sources(self, data):
        for benchmark in data["queries"]["benchmarks"]:
            import_line, class_name = sources._recipe_class(benchmark["id"])
            assert benchmark["recipe"] == {"import_line": import_line, "class_name": class_name}
            assert benchmark["overview_slug"] == sources._overview_slug(benchmark["id"])
            assert benchmark["tpc_derived"] == (benchmark["id"] in sources._TPC_DERIVED)

    def test_global_constants_match_sources(self, data):
        queries = data["queries"]
        assert queries["tpc_notice"] == sources._TPC_NOTICE
        assert queries["group_threshold"] == sources._GROUP_THRESHOLD
        assert queries["bucket_size"] == sources._BUCKET_SIZE


class TestTpcdsNotes:
    def test_notes_export_for_flagged_queries(self):
        from benchbox.core.tpcds.c_tools import SUBSCALE_TEMPLATE_OVERRIDES
        from benchbox.core.tpcds.qualification.classification import KNOWN, RETURNS_DIFFERENCE

        flagged_answer = next(qid for qid, known in KNOWN.items() if RETURNS_DIFFERENCE in known.classes)
        flagged_subscale = next(
            template.removeprefix("query").removesuffix(".tpl") for template in sorted(SUBSCALE_TEMPLATE_OVERRIDES)
        )
        answer = sources._answer_set_note("tpcds", flagged_answer)
        subscale = sources._subscale_note("tpcds", flagged_subscale)
        assert answer and subscale

        record = exporter._query_record(sources, "tpcds", "TPC-DS", flagged_answer, {})
        assert record["answer_set_note"] == answer[0]
        record = exporter._query_record(sources, "tpcds", "TPC-DS", flagged_subscale, {})
        assert record["subscale_note"] == subscale[0]


class TestCompatMatchesRegistry:
    def test_rules_match_registry_rows(self, data):
        from benchbox.sql_compat.context import Phase
        from benchbox.sql_compat.registry import REGISTRY

        sources._load_all_rules()
        phase_order = [phase.value for phase in Phase]
        expected = sorted(
            (
                (
                    platform,
                    phase.value,
                    sources._scope_label(benchmark, query_id),
                    entry.decision.action.value,
                    entry.decision.support_level.value,
                    entry.decision.failure_mode.value,
                    entry.decision.rule_id,
                    entry.decision.reason,
                )
                for (phase, platform, benchmark, query_id), entry in REGISTRY.all_rules()
            ),
            key=lambda r: (r[0], phase_order.index(r[1]) if r[1] in phase_order else len(phase_order), r[2], r[6]),
        )
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
            for r in data["compat"]["rules"]
        ] == expected

    def test_scope_fields_round_trip_to_label(self, data):
        for rule in data["compat"]["rules"]:
            assert rule["scope"] == sources._scope_label(rule["benchmark"], rule["query_id"])

    def test_phase_order_matches_phase_enum(self, data):
        from benchbox.sql_compat.context import Phase

        assert data["compat"]["phase_order"] == [phase.value for phase in Phase]


class TestTuningMatchesRegistry:
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


class TestFullCatalog:
    def test_full_export_validates_and_is_deterministic(self):
        full = exporter.build_docs_data()
        assert _errors(full) == []
        assert exporter.render_json(full) == exporter.render_json(exporter.build_docs_data())
