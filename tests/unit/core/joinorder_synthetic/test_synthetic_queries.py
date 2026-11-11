from __future__ import annotations

import pytest

from benchbox.core.joinorder.queries import CANONICAL_JOINORDER_QUERIES
from benchbox.core.joinorder_synthetic.queries import JoinOrderQueryManager

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestDefaultMergedSurface:
    def test_default_count_covers_full_canonical_set(self):
        manager = JoinOrderQueryManager()
        assert manager.get_query_count() == len(CANONICAL_JOINORDER_QUERIES) == 113

    def test_canonical_extension_id_resolves(self):
        manager = JoinOrderQueryManager()
        assert "SELECT" in manager.get_query("1c").upper()
        assert "FROM" in manager.get_query("1c").upper()

    def test_unknown_id_still_rejected(self):
        manager = JoinOrderQueryManager()
        with pytest.raises(ValueError, match="Invalid query ID"):
            manager.get_query("does-not-exist")

    def test_distributions_account_for_every_exposed_query(self):
        manager = JoinOrderQueryManager()
        exposed = set(manager.get_all_queries())
        assert len(exposed) == 113

        complexity = manager.get_queries_by_complexity()
        classified_complexity = {qid for ids in complexity.values() for qid in ids}
        assert classified_complexity == exposed

        patterns = manager.get_queries_by_pattern()
        classified_patterns = {qid for ids in patterns.values() for qid in ids}
        assert classified_patterns == exposed

    def test_benchmark_info_distributions_cover_total_queries(self):
        from benchbox.core.joinorder_synthetic.benchmark import JoinOrderSyntheticBenchmark

        benchmark = JoinOrderSyntheticBenchmark(scale_factor=0.001)
        info = benchmark.get_benchmark_info()
        complexity_total = sum(len(ids) for ids in info["query_complexity_distribution"].values())
        pattern_total = sum(len(ids) for ids in info["join_pattern_distribution"].values())
        assert info["total_queries"] == 113
        assert complexity_total == info["total_queries"]
        assert pattern_total == info["total_queries"]


class TestCustomDirectoryReplacement:
    @pytest.fixture()
    def query_dir(self, tmp_path):
        query_dir = tmp_path / "queries"
        query_dir.mkdir()
        (query_dir / "custom.sql").write_text("SELECT 1;")
        return query_dir

    def test_custom_directory_reports_only_its_files(self, query_dir):
        manager = JoinOrderQueryManager(str(query_dir))
        assert manager.get_all_queries() == {"custom": "SELECT 1;"}
        assert manager.get_query_count() == 1
        assert manager.get_query_ids() == ["custom"]

    def test_custom_directory_has_no_canonical_fallback(self, query_dir):
        manager = JoinOrderQueryManager(str(query_dir))
        with pytest.raises(ValueError, match="Invalid query ID: 1a"):
            manager.get_query("1a")

    def test_custom_directory_distributions_cover_only_custom_queries(self, query_dir):
        manager = JoinOrderQueryManager(str(query_dir))
        complexity = manager.get_queries_by_complexity()
        assert {qid for ids in complexity.values() for qid in ids} == {"custom"}
        patterns = manager.get_queries_by_pattern()
        assert {qid for ids in patterns.values() for qid in ids} == {"custom"}


class TestCanonicalIdentity:
    def test_default_surface_matches_canonical_text(self):
        from benchbox.core.joinorder.queries import JoinOrderQueryManager as CanonicalManager

        manager = JoinOrderQueryManager()
        canonical = CanonicalManager()
        assert manager.get_all_queries() == canonical.get_all_queries()

    def test_historical_10a_collision_is_gone(self):
        manager = JoinOrderQueryManager()
        sql_10a = manager.get_query("10a")
        sql_10c = manager.get_query("10c")
        assert "[ru]" in sql_10a
        assert sql_10a != sql_10c

    def test_canonical_manager_is_cached_per_instance(self):
        first = JoinOrderQueryManager()
        second = JoinOrderQueryManager()
        assert first._canonical is not None
        assert second._canonical is not None
        assert first._canonical is not second._canonical
        assert first.get_query("1a") == second.get_query("1a")

    def test_disk_queries_receive_portable_alias_normalization(self, tmp_path):
        query_dir = tmp_path / "queries"
        query_dir.mkdir()
        (query_dir / "15a.sql").write_text("SELECT at.movie_id FROM aka_title at WHERE at.movie_id = 1;")
        from benchbox.core.joinorder.queries import JoinOrderQueryManager as CanonicalManager

        manager = CanonicalManager(str(query_dir))
        assert "at1.movie_id" in manager.get_query("15a")
        assert "FROM aka_title at " not in manager.get_query("15a")


class TestScalarVacuityGuard:
    def test_report_fails_on_unclassified_vacuous_query(self):
        from benchbox.core.equivalence.cross_surface import _report

        exit_code = _report(
            [],
            total=2,
            coverage={"expression": 1, "pandas": 1},
            known={},
            benchmark="joinorder_synthetic",
            reference_row_counts={"1a": 0},
            legitimately_empty={},
        )
        assert exit_code == 1

    def test_report_tolerates_classified_vacuous_query(self):
        from benchbox.core.equivalence.cross_surface import _report

        exit_code = _report(
            [],
            total=2,
            coverage={"expression": 1, "pandas": 1},
            known={},
            benchmark="joinorder_synthetic",
            reference_row_counts={"1a": 0},
            legitimately_empty={"1a": "test rationale"},
        )
        assert exit_code == 0


