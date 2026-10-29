from __future__ import annotations

import json

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
    pytest.mark.duckdb,
]

_QUERIES = {
    "Q1": "SELECT i FROM range(5) t(i) WHERE i > 2",
    "Q6": "SELECT count(*) AS c FROM range(5) t(i)",
}


def _capture_rows(
    variants: list[tuple[int, str]], queries: dict[str, str] | None = None, setup: tuple[str, ...] = ()
) -> list[dict]:
    from benchbox.platforms.duckdb import DuckDBAdapter

    adapter = DuckDBAdapter(capture_plans=True)
    conn = adapter.create_connection()
    rows: list[dict] = []
    try:
        for statement in setup:
            conn.execute(statement)
        order = 0
        for stream_id, test_type in variants:
            for query_id, sql in (queries or _QUERIES).items():
                captured = adapter.execute_query(
                    connection=conn, query=sql, query_id=query_id, validate_row_count=False
                )
                assert captured["status"] == "SUCCESS", captured
                order += 1
                rows.append(
                    {
                        "query_id": query_id,
                        "status": "SUCCESS",
                        "execution_order": order,
                        "execution_time_ms": 100.0 * (stream_id + 1) + (1.0 if query_id == "Q6" else 0.0),
                        "rows_returned": captured.get("rows_returned", 0),
                        "query_plan": captured.get("query_plan"),
                        "plan_fingerprint": captured.get("plan_fingerprint"),
                        "stream_id": stream_id,
                        "test_type": test_type,
                    }
                )
    finally:
        adapter.close_connection(conn)
    return rows


def _export(output_dir, variants, queries=None, setup=()):
    from benchbox.core.results.exporter import ResultExporter
    from tests.fixtures.result_dict_fixtures import make_benchmark_results

    rows = _capture_rows(variants, queries, setup)
    results = make_benchmark_results(
        benchmark_id="mcp-plan-lookup",
        benchmark_name="mcp-plan-lookup",
        platform="duckdb",
        scale_factor=0.01,
        execution_id="mcp-plan-lookup",
        duration_seconds=0.0,
        total_queries=len(rows),
        successful_queries=len(rows),
        query_plans_captured=len(rows),
        query_results=rows,
    )
    return ResultExporter(output_dir=output_dir).export_result(results, ["json"])["json"]


def _plan_keys(bundle) -> list[str]:
    plans_path = bundle.with_suffix("").with_suffix(".plans.json")
    return sorted(json.loads(plans_path.read_text(encoding="utf-8"))["queries"])


class TestMCPQueryPlanBundleLookup:
    @pytest.mark.parametrize("requested_id", ["1", "Q1", "q1"])
    @pytest.mark.parametrize("fmt", ["tree", "summary", "json"])
    def test_qualified_single_variant_keys_resolve(self, tmp_path, requested_id, fmt):
        from benchbox.mcp.tools.analytics import _get_query_plan_impl

        bundle = _export(tmp_path, [(0, "power"), (0, "power")])
        assert any("#" in key for key in _plan_keys(bundle))

        result = _get_query_plan_impl(bundle, str(bundle), requested_id, fmt)

        assert result["status"] == "success", result
        assert result["query_id"] == "1"

    def test_multiple_variants_require_qualifier(self, tmp_path):
        from benchbox.mcp.tools.analytics import _get_query_plan_impl

        bundle = _export(tmp_path, [(0, "power"), (1, "throughput")])

        result = _get_query_plan_impl(bundle, str(bundle), "Q1", "tree")

        assert result.get("error") is True or "error" in result, result
        assert result["error_code"] == "VALIDATION_ERROR"
        assert len(result["details"]["available_variants"]) == 2

    def test_qualifier_selects_variant_with_its_runtime(self, tmp_path):
        from benchbox.mcp.tools.analytics import _get_query_plan_impl

        bundle = _export(tmp_path, [(0, "power"), (1, "throughput")])

        power = _get_query_plan_impl(bundle, str(bundle), "1", "json", test_type="power")
        throughput = _get_query_plan_impl(bundle, str(bundle), "Q1", "json", stream="1")
        q6 = _get_query_plan_impl(bundle, str(bundle), "6", "json", stream="1", test_type="throughput")

        assert power["status"] == "success"
        assert power["runtime_ms"] == 100.0
        assert throughput["status"] == "success"
        assert throughput["runtime_ms"] == 200.0
        assert q6["runtime_ms"] == 201.0

    def test_unknown_qualifier_is_not_found(self, tmp_path):
        from benchbox.mcp.tools.analytics import _get_query_plan_impl

        bundle = _export(tmp_path, [(0, "power"), (1, "throughput")])

        result = _get_query_plan_impl(bundle, str(bundle), "1", "tree", stream="7")

        assert result["error_code"] == "RESOURCE_NOT_FOUND"
        assert result["details"]["available"]


class TestMCPQueryPlanRuntimeSelection:
    def test_runtime_comes_from_measurement_not_warmup(self, tmp_path):
        from benchbox.mcp.tools.analytics import _get_query_plan_impl

        bundle = _export(tmp_path, [(0, "power"), (1, "throughput")])
        data = json.loads(bundle.read_text(encoding="utf-8"))
        warmups = []
        for q in data["queries"]:
            warmup = dict(q, run_type="warmup", ms=9999.0)
            q["run_type"] = "measurement"
            warmups.append(warmup)
        data["queries"] = warmups + data["queries"]
        bundle.write_text(json.dumps(data), encoding="utf-8")

        bare = _get_query_plan_impl(bundle, str(bundle), "1", "json", stream="0")
        variant = _get_query_plan_impl(bundle, str(bundle), "1", "json", stream="1", test_type="throughput")

        assert bare["runtime_ms"] == 100.0
        assert variant["runtime_ms"] == 200.0


_JOIN_AGG_SETUP = (
    "CREATE TABLE a AS SELECT i FROM range(10) t(i)",
    "CREATE TABLE b AS SELECT i FROM range(10) t(i)",
)
_JOIN_AGG_QUERY = (
    "SELECT a.i % 2 AS g, count(*) AS c, sum(b.i) AS s FROM a JOIN b ON a.i = b.i WHERE a.i > 1 GROUP BY 1 ORDER BY 1"
)


class TestMCPCanonicalPlanRendering:
    def _collect(self, node, out):
        out.append(node)
        for child in node.get("children", []):
            self._collect(child, out)
        return out

    def test_captured_plan_renders_and_summarizes_canonically(self, tmp_path):
        from benchbox.mcp.tools.analytics import _get_query_plan_impl

        bundle = _export(tmp_path, [(0, "power")], {"Q1": _JOIN_AGG_QUERY}, _JOIN_AGG_SETUP)

        plan = _get_query_plan_impl(bundle, str(bundle), "1", "json")["plan"]
        tree = _get_query_plan_impl(bundle, str(bundle), "1", "tree")["plan_tree"]
        summary = _get_query_plan_impl(bundle, str(bundle), "1", "summary")["summary"]

        nodes = self._collect(plan["logical_root"], [])
        by_type: dict[str, int] = {}
        for node in nodes:
            by_type[node["operator_type"]] = by_type.get(node["operator_type"], 0) + 1

        assert "Node" not in tree
        assert summary["operator_count"] == len(nodes)
        assert summary["scan_count"] == by_type.get("Scan", 0) >= 1
        assert summary["join_count"] == by_type.get("Join", 0) >= 1
        assert summary["aggregate_count"] == by_type.get("Aggregate", 0) >= 1
        assert tree.count("Scan") == summary["scan_count"]
