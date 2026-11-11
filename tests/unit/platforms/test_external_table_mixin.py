import pytest

from benchbox.platforms.base.external_table_mixin import HiveExternalTableMixin

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class ConcreteAdapter(HiveExternalTableMixin):
    def __init__(self, staging_root=None, catalog="hive", schema="default"):
        self.staging_root = staging_root
        self.catalog = catalog
        self.schema = schema
        self.platform_name = "TestAdapter"

    @staticmethod
    def _validate_identifier(identifier: str) -> bool:
        import re

        return bool(re.match(r"^[a-zA-Z_][a-zA-Z0-9_-]*$", identifier))

    def _resolve_data_files(self, benchmark, data_dir):
        return getattr(benchmark, "tables", {})


class TestValidateExternalTableRequirements:
    def test_raises_when_staging_root_missing(self):
        adapter = ConcreteAdapter(staging_root=None)
        with pytest.raises(ValueError, match="staging_root"):
            adapter.validate_external_table_requirements()

    def test_passes_when_staging_root_set(self):
        adapter = ConcreteAdapter(staging_root="s3://bucket/path")
        adapter.validate_external_table_requirements()


class TestMapExternalColumnType:
    @pytest.mark.parametrize(
        "input_type,expected",
        [
            ("", "VARCHAR"),
            ("BIGINT", "BIGINT"),
            ("SMALLINT", "SMALLINT"),
            ("INTEGER", "INTEGER"),
            ("INT", "INTEGER"),
            ("DOUBLE", "DOUBLE"),
            ("REAL", "REAL"),
            ("FLOAT", "REAL"),
            ("DATE", "DATE"),
            ("BOOLEAN", "BOOLEAN"),
            ("BOOL", "BOOLEAN"),
            ("DECIMAL(10,2)", "DECIMAL(10,2)"),
            ("VARCHAR(100)", "VARCHAR(100)"),
            ("TIMESTAMP", "TIMESTAMP"),
            ("UNKNOWN_TYPE", "VARCHAR"),
        ],
    )
    def test_type_mapping(self, input_type, expected):
        result = HiveExternalTableMixin._map_external_column_type(input_type)
        assert result == expected


class TestBuildExternalColumnDefinitions:
    def _make_benchmark(self, schema_dict):
        class FakeBench:
            def get_schema(self):
                return schema_dict

        return FakeBench()

    def test_builds_column_defs_from_schema(self):
        bench = self._make_benchmark(
            {
                "lineitem": {
                    "columns": [
                        {"name": "l_orderkey", "type": "BIGINT"},
                        {"name": "l_quantity", "type": "DECIMAL(15,2)"},
                    ]
                }
            }
        )
        adapter = ConcreteAdapter(staging_root="s3://bucket/path")
        result = adapter._build_external_column_definitions(bench, "lineitem")
        assert "l_orderkey BIGINT" in result
        assert "l_quantity DECIMAL(15,2)" in result

    def test_raises_on_missing_schema(self):
        bench = self._make_benchmark({})
        adapter = ConcreteAdapter(staging_root="s3://bucket/path")
        with pytest.raises(ValueError, match="No schema definition found"):
            adapter._build_external_column_definitions(bench, "missing_table")

    def test_raises_on_empty_column_name(self):
        bench = self._make_benchmark({"orders": {"columns": [{"name": "", "type": "INT"}]}})
        adapter = ConcreteAdapter(staging_root="s3://bucket/path")
        with pytest.raises(ValueError, match="empty name"):
            adapter._build_external_column_definitions(bench, "orders")

    def test_raises_on_invalid_identifier(self):
        bench = self._make_benchmark({"orders": {"columns": [{"name": "1bad_col", "type": "INT"}]}})
        adapter = ConcreteAdapter(staging_root="s3://bucket/path")
        with pytest.raises(ValueError, match="not a valid SQL identifier"):
            adapter._build_external_column_definitions(bench, "orders")

    def test_raises_on_no_get_schema(self):
        class NoBench:
            pass

        adapter = ConcreteAdapter(staging_root="s3://bucket/path")
        with pytest.raises(ValueError, match="schema metadata unavailable"):
            adapter._build_external_column_definitions(NoBench(), "lineitem")


class TestBuildExternalLocation:
    def test_builds_location_with_trailing_slash(self):
        adapter = ConcreteAdapter(staging_root="s3://bucket/prefix/")
        assert adapter._build_external_location("lineitem") == "s3://bucket/prefix/lineitem/"

    def test_handles_no_trailing_slash(self):
        adapter = ConcreteAdapter(staging_root="s3://bucket/prefix")
        assert adapter._build_external_location("orders") == "s3://bucket/prefix/orders/"

    def test_escapes_single_quotes(self):
        adapter = ConcreteAdapter(staging_root="s3://bucket/it's-a-path")
        loc = adapter._build_external_location("orders")
        assert "''" in loc
        assert "it''s-a-path" in loc


class TestTrinoPrestoMixinParity:
    def test_trino_uses_mixin(self):
        try:
            from benchbox.platforms.trino import TrinoAdapter
        except ImportError:
            pytest.skip("Trino adapter not importable")
        assert issubclass(TrinoAdapter, HiveExternalTableMixin)

    def test_presto_uses_mixin(self):
        try:
            from benchbox.platforms.presto import PrestoAdapter
        except ImportError:
            pytest.skip("Presto adapter not importable")
        assert issubclass(PrestoAdapter, HiveExternalTableMixin)


