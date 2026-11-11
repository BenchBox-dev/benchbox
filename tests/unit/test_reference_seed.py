import pytest

from benchbox.core.tpch import benchmark as tpch_benchmark
from benchbox.core.tpch.benchmark import (
    binds_answer_set_parameters,
    describe_query_parameters,
    has_answer_set,
    power_stream_seed,
)
from benchbox.core.tpch.power_test import TPCHPowerTestConfig

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.mark.unit
class TestAnswerSetParameters:
    def test_only_no_seed_binds_answer_set(self):
        assert binds_answer_set_parameters(None) is True
        for seed in (0, 1, 42, 17039360, 101000000):
            assert binds_answer_set_parameters(seed) is False

    def test_reference_seed_constant_is_gone(self):
        assert not hasattr(tpch_benchmark, "TPCH_SF1_REFERENCE_SEED")
        assert not hasattr(tpch_benchmark, "get_reference_seed")

    def test_answer_set_exists_only_at_sf1(self):
        assert has_answer_set(1.0) is True
        assert has_answer_set(1) is True
        assert has_answer_set(0.01) is False
        assert has_answer_set(10.0) is False

    def test_power_stream_seed(self):
        assert power_stream_seed(None, 0) is None
        assert power_stream_seed(None, 3) is None
        assert power_stream_seed(17039360, 0) == 17039360
        assert power_stream_seed(17039360, 1) == 17040360

    def test_describe_query_parameters(self):
        assert describe_query_parameters(None) == "qgen -d (TPC-H default substitution parameters)"
        assert describe_query_parameters(7) == "qgen -r (7 + 1000 * stream_id)"


@pytest.mark.unit
class TestPowerTestConfigDefaults:
    def test_default_seed_is_none(self):
        config = TPCHPowerTestConfig()
        assert config.seed is None

    def test_explicit_seed_preserved(self):
        config = TPCHPowerTestConfig(seed=42)
        assert config.seed == 42


@pytest.mark.unit
@pytest.mark.tpch
class TestQgenDefaultsMode:
    @pytest.fixture
    def qgen(self):
        try:
            from benchbox.core.tpch.queries import QGenBinary

            return QGenBinary()
        except RuntimeError:
            pytest.skip("qgen binary not available")

    def test_defaults_mode_q3_contains_building(self, qgen):
        sql = qgen.generate(query_id=3, seed=None, scale_factor=1.0)
        assert "BUILDING" in sql.upper(), f"Expected BUILDING in Q3 defaults output, got: {sql[:200]}"

    def test_seed1_q3_contains_furniture(self, qgen):
        sql = qgen.generate(query_id=3, seed=1, scale_factor=1.0)
        assert "FURNITURE" in sql.upper(), f"Expected FURNITURE in Q3 seed=1 output, got: {sql[:200]}"

    def test_defaults_mode_differs_from_seed1(self, qgen):
        sql_defaults = qgen.generate(query_id=3, seed=None, scale_factor=1.0)
        sql_seed1 = qgen.generate(query_id=3, seed=1, scale_factor=1.0)
        assert sql_defaults != sql_seed1, "Defaults mode and seed=1 should produce different SQL"

    def test_defaults_mode_q6_discount(self, qgen):
        sql = qgen.generate(query_id=6, seed=None, scale_factor=1.0)
        assert ".06" in sql, f"Expected .06 discount in Q6 defaults output, got: {sql[:200]}"

    def test_defaults_mode_q5_asia(self, qgen):
        sql = qgen.generate(query_id=5, seed=None, scale_factor=1.0)
        assert "ASIA" in sql.upper(), f"Expected ASIA in Q5 defaults output, got: {sql[:200]}"

    @pytest.mark.parametrize(
        ("query_id", "answer_rows"),
        [(11, 1048), (18, 57), (20, 186)],
    )
    def test_answer_files_match_defaults_not_old_reference_seed(self, qgen, query_id, answer_rows):
        from pathlib import Path

        answers = Path(__file__).resolve().parents[2] / "_sources/tpc-h/dbgen/answers" / f"q{query_id}.out"
        if not answers.exists():
            pytest.skip("TPC-H answer files not available")
        data_lines = [line for line in answers.read_text().splitlines()[1:] if line.strip()]
        assert len(data_lines) == answer_rows
        assert qgen.generate(query_id, seed=None) != qgen.generate(query_id, seed=17039360)


