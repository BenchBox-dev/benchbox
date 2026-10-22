from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]

_BENCHBOX_ROOT = Path(__file__).parent.parent.parent / "benchbox"


def test_ddl_drift_check_is_clean():
    from benchbox.sql_compat.inventory import check_ddl_drift

    drift = check_ddl_drift(_BENCHBOX_ROOT)

    if drift:
        lines = [
            f"  {d.file}:{d.line}  {d.func_name}()  [inferred platform key: {d.inferred_platform_key!r}]" for d in drift
        ]
        pytest.fail(
            f"{len(drift)} unregistered DDL-optimize transform(s) found:\n"
            + "\n".join(lines)
            + "\n\nRegister each in benchbox/sql_compat/rules/ddl_optimize/"
            "{platform}_ddl_rewrites.py — see benchbox/sql_compat/README.md."
        )


def test_behavior_detector_catches_custom_named_create_table_rewrites(tmp_path):
    from benchbox.sql_compat.inventory import collect_ddl_governance_statuses

    root = tmp_path / "benchbox"
    platforms = root / "platforms"
    platforms.mkdir(parents=True)
    (platforms / "custom.py").write_text(
        "\n".join(
            [
                "class CustomAdapter:",
                "    def arbitrary_converter(self, statement: str) -> str:",
                "        if not statement.upper().startswith('CREATE TABLE'):",
                "            return statement",
                "        return statement.replace('CREATE TABLE', 'CREATE OR REPLACE TABLE', 1)",
            ]
        ),
        encoding="utf-8",
    )

    statuses = collect_ddl_governance_statuses(root)

    assert [(entry.func_name, entry.inferred_platform_key, entry.status) for entry in statuses] == [
        ("arbitrary_converter", "custom", "unregistered_detected_behavior")
    ]


def test_ddl_rule_does_not_blanket_cover_unmapped_new_platform_function(tmp_path):
    from benchbox.sql_compat.inventory import collect_ddl_governance_statuses

    root = tmp_path / "benchbox"
    platforms = root / "platforms"
    platforms.mkdir(parents=True)
    (platforms / "bigquery.py").write_text(
        "\n".join(
            [
                "class BigQueryAdapter:",
                "    def arbitrary_converter(self, statement: str) -> str:",
                "        if not statement.upper().startswith('CREATE TABLE'):",
                "            return statement",
                "        return statement.replace('CREATE TABLE', 'CREATE OR REPLACE TABLE', 1)",
            ]
        ),
        encoding="utf-8",
    )

    statuses = collect_ddl_governance_statuses(root)

    assert [(entry.func_name, entry.inferred_platform_key, entry.status) for entry in statuses] == [
        ("arbitrary_converter", "bigquery", "unregistered_detected_behavior")
    ]


def test_ddl_governance_statuses_distinguish_runtime_governance_and_no_rewrite_platforms():
    from benchbox.sql_compat.inventory import collect_ddl_governance_statuses

    statuses = collect_ddl_governance_statuses(_BENCHBOX_ROOT)
    by_key = {(entry.inferred_platform_key, entry.func_name): entry for entry in statuses}

    assert by_key[("bigquery", "_convert_to_bigquery_table")].status == "registered_governance_only_intent"
    assert by_key[("databricks", "_convert_to_delta_table")].status == "registered_governance_only_intent"
    assert by_key[("singlestore", "_transform_create_statement")].status == "registered_runtime_behavior"
    assert by_key[("spark", "optimize_spark_table_definition")].status == "registered_governance_only_intent"
    assert all(entry.inferred_platform_key != "duckdb" for entry in statuses)


def test_bigquery_runtime_ddl_rewrite_is_represented_in_governance_inventory():
    from benchbox.platforms.bigquery import BigQueryAdapter
    from benchbox.sql_compat.inventory import collect_ddl_governance_statuses

    adapter = BigQueryAdapter.__new__(BigQueryAdapter)
    adapter.project_id = "proj"
    adapter.dataset_id = "ds"
    adapter.partitioning_field = "created_at"
    adapter.clustering_fields = ["customer_id"]

    converted = adapter._convert_to_bigquery_table("CREATE TABLE orders (created_at DATE, customer_id INT64)")

    assert "CREATE OR REPLACE TABLE `proj.ds.ORDERS`" in converted
    assert "PARTITION BY DATE(created_at)" in converted
    assert "CLUSTER BY customer_id" in converted
    status = next(
        entry
        for entry in collect_ddl_governance_statuses(_BENCHBOX_ROOT)
        if entry.inferred_platform_key == "bigquery" and entry.func_name == "_convert_to_bigquery_table"
    )
    assert status.status == "registered_governance_only_intent"


def test_athena_runtime_ddl_comes_from_translate_ddl():
    from benchbox.platforms.athena import AthenaAdapter
    from benchbox.sql_compat.inventory import collect_ddl_governance_statuses

    adapter = AthenaAdapter.__new__(AthenaAdapter)
    adapter.s3_bucket = "bucket"
    adapter.s3_prefix = "prefix"
    adapter.database = "db"
    adapter.data_format = "parquet"
    adapter.default_format = "PARQUET"

    [converted] = adapter._external_table_ddl("CREATE TABLE orders (id VARCHAR(20) NOT NULL, amount DECIMAL)")

    assert "CREATE EXTERNAL TABLE IF NOT EXISTS `orders`" in converted
    assert "`id` STRING" in converted
    assert "NOT NULL" not in converted
    assert "STORED AS PARQUET" in converted
    assert "LOCATION 's3://bucket/prefix/db/orders/'" in converted
    assert not [
        entry
        for entry in collect_ddl_governance_statuses(_BENCHBOX_ROOT)
        if entry.inferred_platform_key == "athena"
        and entry.status in {"unregistered_detected_behavior", "unknown_uninspectable_behavior"}
    ]


def test_singlestore_ddl_rules_resolve_via_base_optimizer():
    import importlib
    import pkgutil

    import benchbox.sql_compat.rules.ddl_optimize as _ddl_pkg

    for _, mod_name, _ispkg in pkgutil.walk_packages(_ddl_pkg.__path__, _ddl_pkg.__name__ + "."):
        if not _ispkg:
            importlib.import_module(mod_name)

    from benchbox.sql_compat.context import CompatibilityContext, Phase
    from benchbox.sql_compat.decision import RewriteDDLPayload
    from benchbox.sql_compat.registry import REGISTRY

    ctx = CompatibilityContext(
        platform="singlestore",
        platform_version=None,
        benchmark="",
        query_id=None,
        phase=Phase.DDL_OPTIMIZE,
        mode="sql",
        dialect=None,
    )
    decisions = REGISTRY.resolve_all(ctx)

    assert len(decisions) == 4, f"Expected 4 SingleStore DDL rules, got {len(decisions)}"

    transformer_ids = [d.payload.transformer_id for d in decisions if isinstance(d.payload, RewriteDDLPayload)]
    assert transformer_ids == [
        "singlestore_strip_foreign_keys",
        "singlestore_reference_table",
        "singlestore_inject_shard_key",
        "singlestore_inject_sort_key",
    ], f"Unexpected transformer_id order: {transformer_ids}"

    for decision in decisions:
        assert isinstance(decision.payload, RewriteDDLPayload)
        assert decision.payload.transformer_id, "transformer_id must not be empty"
        assert decision.payload.description, "description must not be empty"


def test_base_ddl_optimizer_dispatches_correctly():
    from benchbox.platforms.singlestore import SingleStoreAdapter

    with patch("benchbox.platforms.singlestore._s2", MagicMock()):
        adapter = SingleStoreAdapter()

    lineitem_stmt = "CREATE TABLE `lineitem` (\n  l_orderkey BIGINT,\n  l_linenumber INT\n)"
    result = adapter._transform_create_statement(lineitem_stmt)
    assert "SHARD KEY (l_orderkey)" in result
    assert "SORT KEY (l_orderkey, l_linenumber)" in result
    assert "FOREIGN KEY" not in result

    nation_stmt = "CREATE TABLE nation (\n  n_nationkey INT\n)"
    result = adapter._transform_create_statement(nation_stmt)
    assert "CREATE REFERENCE TABLE" in result
    assert "SHARD KEY" not in result
    assert "SORT KEY" not in result


def test_every_rule_file_loads_via_base_optimizer():
    from benchbox.platforms.base.ddl_optimizer import _clear_load_cache, _ensure_platform_rules_loaded
    from benchbox.sql_compat.context import Phase
    from benchbox.sql_compat.registry import REGISTRY

    rules_dir = _BENCHBOX_ROOT / "sql_compat" / "rules" / "ddl_optimize"
    rule_files = sorted(p.stem for p in rules_dir.glob("*_ddl_rewrites.py"))
    assert rule_files, "No DDL rewrite rule files found"

    _clear_load_cache()
    for stem in rule_files:
        platform_key = stem.removesuffix("_ddl_rewrites")
        _ensure_platform_rules_loaded(platform_key)
        registered = {platform for (phase, platform, _, _), _ in REGISTRY.all_rules() if phase == Phase.DDL_OPTIMIZE}
        assert platform_key in registered, (
            f"Rule file {stem}.py loaded but registered no DDL_OPTIMIZE rule for "
            f"platform_key={platform_key!r}. File-stem and registered platform_key must match."
        )


_DISPATCHED_ADAPTERS: dict[str, str] = {
    "singlestore": "benchbox.platforms.singlestore.SingleStoreAdapter",
}


def test_runtime_dispatch_or_governance_only():
    import importlib
    import pkgutil

    import benchbox.sql_compat.rules.ddl_optimize as _ddl_pkg

    for _, mod_name, _ispkg in pkgutil.walk_packages(_ddl_pkg.__path__, _ddl_pkg.__name__ + "."):
        if not _ispkg:
            importlib.import_module(mod_name)

    from benchbox.sql_compat.context import Phase
    from benchbox.sql_compat.decision import RewriteDDLPayload
    from benchbox.sql_compat.registry import REGISTRY

    failures: list[str] = []
    for (phase, platform, _, _), entry in REGISTRY.all_rules():
        if phase != Phase.DDL_OPTIMIZE:
            continue
        payload = entry.decision.payload
        if not isinstance(payload, RewriteDDLPayload):
            continue
        if payload.governance_only:
            continue
        adapter_path = _DISPATCHED_ADAPTERS.get(platform)
        if adapter_path is None:
            failures.append(
                f"{entry.rule_id}: platform={platform!r} has no entry in _DISPATCHED_ADAPTERS "
                f"and governance_only=False — either mark the rule governance_only=True or "
                f"register the adapter class here."
            )
            continue
        module_name, class_name = adapter_path.rsplit(".", 1)
        adapter_cls = getattr(importlib.import_module(module_name), class_name)
        if not callable(getattr(adapter_cls, payload.transformer_id, None)):
            failures.append(
                f"{entry.rule_id}: transformer_id={payload.transformer_id!r} is not a callable on {adapter_path}."
            )
    if failures:
        pytest.fail("Dispatch/governance contract violations:\n" + "\n".join(f"  {f}" for f in failures))
