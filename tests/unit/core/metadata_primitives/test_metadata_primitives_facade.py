# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestMetadataPrimitivesFacadeInstantiation:
    def test_import_and_create_default(self):
        from benchbox.metadata_primitives import MetadataPrimitives

        obj = MetadataPrimitives()
        assert obj is not None

    def test_create_with_scale_factor(self):
        from benchbox.metadata_primitives import MetadataPrimitives

        obj = MetadataPrimitives(scale_factor=2.0)
        assert obj.scale_factor == 2.0

    def test_create_with_output_dir(self, tmp_path):
        from benchbox.metadata_primitives import MetadataPrimitives

        obj = MetadataPrimitives(output_dir=tmp_path)
        assert obj is not None

    def test_internal_impl_created(self):
        from benchbox.metadata_primitives import MetadataPrimitives

        obj = MetadataPrimitives()
        assert obj._impl is not None


class TestMetadataPrimitivesFacadeSchema:
    def test_get_schema_returns_dict(self):
        from benchbox.metadata_primitives import MetadataPrimitives

        obj = MetadataPrimitives()
        schema = obj.get_schema()
        assert isinstance(schema, dict)

    def test_get_table_names_returns_list(self):
        from benchbox.metadata_primitives import MetadataPrimitives

        obj = MetadataPrimitives()
        names = obj.get_table_names()
        assert isinstance(names, list)
        assert len(names) > 0

    def test_get_create_tables_sql_returns_string(self):
        from benchbox.metadata_primitives import MetadataPrimitives

        obj = MetadataPrimitives()
        sql = obj.get_create_tables_sql()
        assert isinstance(sql, str)
        assert len(sql) > 0

    def test_get_create_tables_sql_with_dialect(self):
        from benchbox.metadata_primitives import MetadataPrimitives

        obj = MetadataPrimitives()
        sql = obj.get_create_tables_sql(dialect="duckdb")
        assert isinstance(sql, str)


class TestMetadataPrimitivesFacadeDataGeneration:
    def test_generate_data_returns_dict(self):
        from benchbox.metadata_primitives import MetadataPrimitives

        obj = MetadataPrimitives()
        result = obj.generate_data()
        assert isinstance(result, dict)

    def test_generate_data_with_tables_arg(self):
        from benchbox.metadata_primitives import MetadataPrimitives

        obj = MetadataPrimitives()
        result = obj.generate_data(tables=None)
        assert isinstance(result, dict)


class TestMetadataPrimitivesFacadeBenchmarkInfo:
    def test_get_benchmark_info_returns_dict(self):
        from benchbox.metadata_primitives import MetadataPrimitives

        obj = MetadataPrimitives()
        info = obj.get_benchmark_info()
        assert isinstance(info, dict)

    def test_get_benchmark_info_has_name(self):
        from benchbox.metadata_primitives import MetadataPrimitives

        obj = MetadataPrimitives()
        info = obj.get_benchmark_info()
        assert "name" in info
        assert "Metadata Primitives" in info["name"]

    def test_get_benchmark_info_has_version(self):
        from benchbox.metadata_primitives import MetadataPrimitives

        obj = MetadataPrimitives()
        info = obj.get_benchmark_info()
        assert "version" in info

    def test_get_benchmark_info_has_query_count(self):
        from benchbox.metadata_primitives import MetadataPrimitives

        obj = MetadataPrimitives()
        info = obj.get_benchmark_info()
        assert "query_count" in info
        assert isinstance(info["query_count"], int)
        assert info["query_count"] > 0

    def test_get_benchmark_info_has_categories(self):
        from benchbox.metadata_primitives import MetadataPrimitives

        obj = MetadataPrimitives()
        info = obj.get_benchmark_info()
        assert "categories" in info
        assert isinstance(info["categories"], list)

    def test_get_benchmark_info_has_complexity_categories(self):
        from benchbox.metadata_primitives import MetadataPrimitives

        obj = MetadataPrimitives()
        info = obj.get_benchmark_info()
        assert "complexity_categories" in info


class TestMetadataPrimitivesFacadeComplexity:
    def test_get_complexity_categories_returns_list(self):
        from benchbox.metadata_primitives import MetadataPrimitives

        obj = MetadataPrimitives()
        cats = obj.get_complexity_categories()
        assert isinstance(cats, list)


