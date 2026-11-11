from __future__ import annotations

import csv
from pathlib import Path

import pytest

from benchbox.core.joinorder_synthetic.generator import JoinOrderGenerator

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestJoinOrderGeneratorInit:
    def test_default_init(self, tmp_path: Path):
        gen = JoinOrderGenerator(scale_factor=0.001, output_dir=tmp_path)
        assert gen.scale_factor == 0.001
        assert gen.output_dir == tmp_path
        assert gen.force_regenerate is False
        assert gen.quiet is False

    def test_verbose_bool_true(self, tmp_path: Path):
        gen = JoinOrderGenerator(scale_factor=0.001, output_dir=tmp_path, verbose=True)
        assert gen.verbose_level == 1
        assert gen.verbose_enabled is True

    def test_verbose_int_level(self, tmp_path: Path):
        gen = JoinOrderGenerator(scale_factor=0.001, output_dir=tmp_path, verbose=2)
        assert gen.verbose_level == 2
        assert gen.very_verbose is True

    def test_quiet_suppresses_verbose(self, tmp_path: Path):
        gen = JoinOrderGenerator(scale_factor=0.001, output_dir=tmp_path, verbose=2, quiet=True)
        assert gen.verbose_enabled is False
        assert gen.very_verbose is False

    def test_default_output_dir_when_none(self):

        pytest.importorskip("benchbox.utils.scale_factor")
        gen = JoinOrderGenerator(scale_factor=1.0)
        assert "joinorder" in str(gen.output_dir)

    def test_schema_initialized(self, tmp_path: Path):
        gen = JoinOrderGenerator(scale_factor=0.001, output_dir=tmp_path)
        assert gen.schema is not None

    def test_base_row_counts_populated(self, tmp_path: Path):
        gen = JoinOrderGenerator(scale_factor=0.001, output_dir=tmp_path)
        assert "title" in gen.base_row_counts
        assert "cast_info" in gen.base_row_counts
        assert len(gen.base_row_counts) > 10


class TestLookupTables:
    @pytest.fixture()
    def gen(self, tmp_path: Path) -> JoinOrderGenerator:
        return JoinOrderGenerator(scale_factor=0.001, output_dir=tmp_path)

    def test_kind_type_count(self, gen: JoinOrderGenerator):
        data = gen._generate_lookup_tables()
        assert len(data["kind_type"]) == 7

    def test_company_type_count(self, gen: JoinOrderGenerator):
        data = gen._generate_lookup_tables()
        assert len(data["company_type"]) == 4

    def test_info_type_has_entries(self, gen: JoinOrderGenerator):
        data = gen._generate_lookup_tables()
        assert len(data["info_type"]) == 24

    def test_role_type_count(self, gen: JoinOrderGenerator):
        data = gen._generate_lookup_tables()
        assert len(data["role_type"]) == 12

    def test_comp_cast_type_count(self, gen: JoinOrderGenerator):
        data = gen._generate_lookup_tables()
        assert len(data["comp_cast_type"]) == 4

    def test_link_type_count(self, gen: JoinOrderGenerator):
        data = gen._generate_lookup_tables()
        assert len(data["link_type"]) == 18

    def test_lookup_ids_sequential(self, gen: JoinOrderGenerator):
        data = gen._generate_lookup_tables()
        for table_name, rows in data.items():
            ids = [row[0] for row in rows]
            assert ids == list(range(1, len(rows) + 1)), f"{table_name} IDs not sequential"

    def test_all_lookup_tables_present(self, gen: JoinOrderGenerator):
        data = gen._generate_lookup_tables()
        expected = {"kind_type", "company_type", "info_type", "role_type", "comp_cast_type", "link_type"}
        assert set(data.keys()) == expected


class TestDimensionTables:
    @pytest.fixture()
    def gen(self, tmp_path: Path) -> JoinOrderGenerator:
        return JoinOrderGenerator(scale_factor=0.001, output_dir=tmp_path)

    @pytest.fixture()
    def lookup_data(self, gen: JoinOrderGenerator) -> dict:
        return gen._generate_lookup_tables()

    def test_title_count_scales(self, gen: JoinOrderGenerator, lookup_data: dict):
        data = gen._generate_dimension_tables(lookup_data)
        expected = int(gen.base_row_counts["title"] * 0.001)
        assert len(data["title"]) == expected

    def test_name_count_scales(self, gen: JoinOrderGenerator, lookup_data: dict):
        data = gen._generate_dimension_tables(lookup_data)
        expected = int(gen.base_row_counts["name"] * 0.001)
        assert len(data["name"]) == expected

    def test_title_has_expected_columns(self, gen: JoinOrderGenerator, lookup_data: dict):
        data = gen._generate_dimension_tables(lookup_data)

        row = data["title"][0]
        assert len(row) == 12
        assert isinstance(row[0], int)
        assert isinstance(row[1], str)

    def test_name_has_expected_columns(self, gen: JoinOrderGenerator, lookup_data: dict):
        data = gen._generate_dimension_tables(lookup_data)
        row = data["name"][0]
        assert len(row) == 9
        assert isinstance(row[0], int)
        assert isinstance(row[1], str)

    def test_all_dimension_tables_present(self, gen: JoinOrderGenerator, lookup_data: dict):
        data = gen._generate_dimension_tables(lookup_data)
        expected = {"title", "name", "company_name", "keyword", "char_name"}
        assert set(data.keys()) == expected

    def test_ids_start_at_one(self, gen: JoinOrderGenerator, lookup_data: dict):
        data = gen._generate_dimension_tables(lookup_data)
        for table_name, rows in data.items():
            assert rows[0][0] == 1, f"{table_name} first ID not 1"


class TestRelationshipTables:
    @pytest.fixture()
    def gen(self, tmp_path: Path) -> JoinOrderGenerator:
        return JoinOrderGenerator(scale_factor=0.001, output_dir=tmp_path)

    @pytest.fixture()
    def dimension_data(self, gen: JoinOrderGenerator) -> dict:
        lookup = gen._generate_lookup_tables()
        return gen._generate_dimension_tables(lookup)

    def test_cast_info_count_scales(self, gen: JoinOrderGenerator, dimension_data: dict):
        data = gen._generate_relationship_tables(dimension_data)
        expected = int(gen.base_row_counts["cast_info"] * 0.001)
        assert len(data["cast_info"]) == expected

    def test_movie_companies_references_valid_ids(self, gen: JoinOrderGenerator, dimension_data: dict):
        data = gen._generate_relationship_tables(dimension_data)
        max_title_id = max(row[0] for row in dimension_data["title"])
        max_company_id = max(row[0] for row in dimension_data["company_name"])
        for row in data["movie_companies"]:
            assert 1 <= row[1] <= max_title_id, "movie_id out of range"
            assert 1 <= row[2] <= max_company_id, "company_id out of range"

    def test_movie_keyword_references_valid_ids(self, gen: JoinOrderGenerator, dimension_data: dict):
        data = gen._generate_relationship_tables(dimension_data)
        max_title_id = max(row[0] for row in dimension_data["title"])
        max_keyword_id = max(row[0] for row in dimension_data["keyword"])
        for row in data["movie_keyword"]:
            assert 1 <= row[1] <= max_title_id
            assert 1 <= row[2] <= max_keyword_id

    def test_all_relationship_tables_present(self, gen: JoinOrderGenerator, dimension_data: dict):
        data = gen._generate_relationship_tables(dimension_data)
        expected = {
            "cast_info",
            "movie_companies",
            "movie_info",
            "movie_info_idx",
            "movie_keyword",
            "movie_link",
            "person_info",
            "complete_cast",
            "aka_name",
            "aka_title",
        }
        assert set(data.keys()) == expected


class TestWriteTableData:
    def test_writes_csv_file(self, tmp_path: Path):
        gen = JoinOrderGenerator(scale_factor=0.001, output_dir=tmp_path)
        data = [(1, "hello", None), (2, "world", 42)]
        path = gen._write_table_data("test_table", data)
        assert path.exists()
        assert path.name == "test_table.csv"

    def test_none_becomes_empty_string(self, tmp_path: Path):
        gen = JoinOrderGenerator(scale_factor=0.001, output_dir=tmp_path)
        data = [(1, None, "ok")]
        gen._write_table_data("test_table", data)
        content = (tmp_path / "test_table.csv").read_text()
        assert content == "1,,ok\n"

    def test_commas_are_quoted(self, tmp_path: Path):
        gen = JoinOrderGenerator(scale_factor=0.001, output_dir=tmp_path)
        data = [(1, "hello, world")]
        gen._write_table_data("test_table", data)
        content = (tmp_path / "test_table.csv").read_text()
        assert '"hello, world"' in content

    def test_quotes_are_escaped(self, tmp_path: Path):
        gen = JoinOrderGenerator(scale_factor=0.001, output_dir=tmp_path)
        data = [(1, 'say "hi"')]
        gen._write_table_data("test_table", data)
        content = (tmp_path / "test_table.csv").read_text()
        assert '""hi""' in content


class TestRowCountAndSizeEstimation:
    def test_get_table_row_count_scales(self, tmp_path: Path):
        gen = JoinOrderGenerator(scale_factor=2.0, output_dir=tmp_path)
        count = gen.get_table_row_count("title")
        assert count == int(gen.base_row_counts["title"] * 2.0)

    def test_get_table_row_count_unknown_table(self, tmp_path: Path):
        gen = JoinOrderGenerator(scale_factor=1.0, output_dir=tmp_path)
        assert gen.get_table_row_count("nonexistent") == 0

    def test_get_total_size_estimate_positive(self, tmp_path: Path):
        gen = JoinOrderGenerator(scale_factor=0.001, output_dir=tmp_path)
        size = gen.get_total_size_estimate()
        assert size > 0

    def test_size_scales_with_factor(self, tmp_path: Path):
        gen1 = JoinOrderGenerator(scale_factor=0.001, output_dir=tmp_path)
        gen2 = JoinOrderGenerator(scale_factor=0.002, output_dir=tmp_path)
        assert gen2.get_total_size_estimate() > gen1.get_total_size_estimate()


class TestGenerateData:
    def test_generates_all_table_files(self, tmp_path: Path):
        gen = JoinOrderGenerator(scale_factor=0.001, output_dir=tmp_path)
        files = gen.generate_data()
        assert len(files) > 10
        for f in files:
            assert Path(f).exists()

    def test_generated_csv_files_have_content(self, tmp_path: Path):
        gen = JoinOrderGenerator(scale_factor=0.001, output_dir=tmp_path)
        gen.generate_data()

        kind_type_csv = tmp_path / "kind_type.csv"
        assert kind_type_csv.exists()
        lines = kind_type_csv.read_text().strip().split("\n")
        assert len(lines) == 7

    def test_manifest_row_counts_populated(self, tmp_path: Path):
        gen = JoinOrderGenerator(scale_factor=0.001, output_dir=tmp_path)
        gen.generate_data()
        assert len(gen._manifest_row_counts) > 10
        for table_name, count in gen._manifest_row_counts.items():
            assert count > 0, f"{table_name} has 0 rows in manifest"


class TestNewRelationshipTables:
    @pytest.fixture()
    def gen(self, tmp_path: Path) -> JoinOrderGenerator:
        return JoinOrderGenerator(scale_factor=0.001, output_dir=tmp_path)

    def test_movie_link_row_count(self, gen: JoinOrderGenerator):
        rows = gen._generate_movie_link(count=50, max_title_id=100)
        assert len(rows) == 50

    def test_movie_link_column_count(self, gen: JoinOrderGenerator):
        rows = gen._generate_movie_link(count=5, max_title_id=100)
        assert all(len(row) == 4 for row in rows)

    def test_movie_link_ids_sequential(self, gen: JoinOrderGenerator):
        rows = gen._generate_movie_link(count=10, max_title_id=100)
        assert [r[0] for r in rows] == list(range(1, 11))

    def test_movie_link_type_id_in_range(self, gen: JoinOrderGenerator):
        rows = gen._generate_movie_link(count=100, max_title_id=200)
        for row in rows:
            assert 1 <= row[3] <= 18, f"link_type_id {row[3]} out of 1-18 range"

    def test_movie_link_title_ids_in_range(self, gen: JoinOrderGenerator):
        max_id = 50
        rows = gen._generate_movie_link(count=100, max_title_id=max_id)
        for row in rows:
            assert 1 <= row[1] <= max_id, f"movie_id {row[1]} out of range"
            assert 1 <= row[2] <= max_id, f"linked_movie_id {row[2]} out of range"

    def test_person_info_row_count(self, gen: JoinOrderGenerator):
        rows = gen._generate_person_info(count=30, max_name_id=50)
        assert len(rows) == 30

    def test_person_info_column_count(self, gen: JoinOrderGenerator):
        rows = gen._generate_person_info(count=5, max_name_id=50)
        assert all(len(row) == 5 for row in rows)

    def test_person_info_ids_sequential(self, gen: JoinOrderGenerator):
        rows = gen._generate_person_info(count=10, max_name_id=50)
        assert [r[0] for r in rows] == list(range(1, 11))

    def test_person_info_type_id_in_range(self, gen: JoinOrderGenerator):
        rows = gen._generate_person_info(count=100, max_name_id=100)
        for row in rows:
            assert 1 <= row[2] <= 113, f"info_type_id {row[2]} out of 1-113 range"

    def test_person_info_person_id_in_range(self, gen: JoinOrderGenerator):
        max_id = 40
        rows = gen._generate_person_info(count=100, max_name_id=max_id)
        for row in rows:
            assert 1 <= row[1] <= max_id

    def test_complete_cast_row_count(self, gen: JoinOrderGenerator):
        rows = gen._generate_complete_cast(count=20, max_title_id=50)
        assert len(rows) == 20

    def test_complete_cast_column_count(self, gen: JoinOrderGenerator):
        rows = gen._generate_complete_cast(count=5, max_title_id=50)
        assert all(len(row) == 4 for row in rows)

    def test_complete_cast_ids_sequential(self, gen: JoinOrderGenerator):
        rows = gen._generate_complete_cast(count=10, max_title_id=50)
        assert [r[0] for r in rows] == list(range(1, 11))

    def test_complete_cast_subject_status_in_range(self, gen: JoinOrderGenerator):
        rows = gen._generate_complete_cast(count=100, max_title_id=100)
        for row in rows:
            assert 1 <= row[2] <= 4, f"subject_id {row[2]} out of 1-4 range"
            assert 1 <= row[3] <= 4, f"status_id {row[3]} out of 1-4 range"

    def test_aka_name_row_count(self, gen: JoinOrderGenerator):
        rows = gen._generate_aka_name(count=25, max_name_id=50)
        assert len(rows) == 25

    def test_aka_name_column_count(self, gen: JoinOrderGenerator):
        rows = gen._generate_aka_name(count=5, max_name_id=50)
        assert all(len(row) == 8 for row in rows)

    def test_aka_name_ids_sequential(self, gen: JoinOrderGenerator):
        rows = gen._generate_aka_name(count=10, max_name_id=50)
        assert [r[0] for r in rows] == list(range(1, 11))

    def test_aka_name_person_id_in_range(self, gen: JoinOrderGenerator):
        max_id = 30
        rows = gen._generate_aka_name(count=50, max_name_id=max_id)
        for row in rows:
            assert 1 <= row[1] <= max_id

    def test_aka_name_has_string_name(self, gen: JoinOrderGenerator):
        rows = gen._generate_aka_name(count=10, max_name_id=50)
        for row in rows:
            assert isinstance(row[2], str) and len(row[2]) > 0

    def test_aka_title_row_count(self, gen: JoinOrderGenerator):
        rows = gen._generate_aka_title(count=15, max_title_id=50)
        assert len(rows) == 15

    def test_aka_title_column_count(self, gen: JoinOrderGenerator):
        rows = gen._generate_aka_title(count=5, max_title_id=50)
        assert all(len(row) == 12 for row in rows)

    def test_aka_title_ids_sequential(self, gen: JoinOrderGenerator):
        rows = gen._generate_aka_title(count=10, max_title_id=50)
        assert [r[0] for r in rows] == list(range(1, 11))

    def test_aka_title_movie_id_in_range(self, gen: JoinOrderGenerator):
        max_id = 40
        rows = gen._generate_aka_title(count=50, max_title_id=max_id)
        for row in rows:
            assert 1 <= row[1] <= max_id

    def test_aka_title_has_string_title(self, gen: JoinOrderGenerator):
        rows = gen._generate_aka_title(count=10, max_title_id=50)
        for row in rows:
            assert isinstance(row[2], str) and len(row[2]) > 0

    def test_new_tables_present_in_relationship_tables(self, gen: JoinOrderGenerator):
        lookup = gen._generate_lookup_tables()
        dim = gen._generate_dimension_tables(lookup)
        rel = gen._generate_relationship_tables(dim)
        for table in ("movie_link", "person_info", "complete_cast", "aka_name", "aka_title"):
            assert table in rel, f"{table} missing from relationship tables"
            assert len(rel[table]) > 0, f"{table} has 0 rows"


