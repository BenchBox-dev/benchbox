# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark™ DS (TPC-DS) - Copyright © Transaction Processing Performance Council
# This implementation is based on the TPC-DS specification.

# Licensed under the MIT License. See LICENSE file in the project root for details.

import random
import sqlite3
from unittest.mock import Mock, patch

import pytest

from benchbox.core.tpcds.streams import (
    PermutationMode,
    QueryStreamConfig,
    StreamQuery,
    TPCDSPermutationGenerator,
    TPCDSStreamManager,
    TPCDSStreamRunner,
    TPCDSStreams,
    create_standard_streams,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestPermutationMode:
    def test_enum_values(self):
        assert PermutationMode.SEQUENTIAL.value == "sequential"
        assert PermutationMode.RANDOM.value == "random"
        assert PermutationMode.TPCDS_STANDARD.value == "tpcds"

    def test_enum_members(self):
        expected_members = {"SEQUENTIAL", "RANDOM", "TPCDS_STANDARD"}
        actual_members = {member.name for member in PermutationMode}
        assert actual_members == expected_members

    def test_enum_comparison(self):
        assert PermutationMode.SEQUENTIAL == PermutationMode.SEQUENTIAL
        assert PermutationMode.SEQUENTIAL != PermutationMode.RANDOM
        assert PermutationMode.RANDOM != PermutationMode.TPCDS_STANDARD


class TestQueryStreamConfig:
    def test_constructor_basic(self):
        config = QueryStreamConfig(
            stream_id=1,
            query_ids=[1, 2, 3],
            permutation_mode=PermutationMode.SEQUENTIAL,
        )

        assert config.stream_id == 1
        assert config.query_ids == [1, 2, 3]
        assert config.permutation_mode == PermutationMode.SEQUENTIAL
        assert config.seed is None
        assert config.parameter_seed is None

    def test_constructor_with_seeds(self):
        config = QueryStreamConfig(
            stream_id=2,
            query_ids=[4, 5, 6],
            permutation_mode=PermutationMode.RANDOM,
            seed=42,
            parameter_seed=100,
        )

        assert config.stream_id == 2
        assert config.query_ids == [4, 5, 6]
        assert config.permutation_mode == PermutationMode.RANDOM
        assert config.seed == 42
        assert config.parameter_seed == 100

    def test_constructor_empty_query_ids(self):
        config = QueryStreamConfig(stream_id=0, query_ids=[], permutation_mode=PermutationMode.TPCDS_STANDARD)

        assert config.stream_id == 0
        assert config.query_ids == []
        assert config.permutation_mode == PermutationMode.TPCDS_STANDARD

    def test_dataclass_equality(self):
        config1 = QueryStreamConfig(1, [1, 2, 3], PermutationMode.SEQUENTIAL)
        config2 = QueryStreamConfig(1, [1, 2, 3], PermutationMode.SEQUENTIAL)
        config3 = QueryStreamConfig(2, [1, 2, 3], PermutationMode.SEQUENTIAL)

        assert config1 == config2
        assert config1 != config3

    def test_dataclass_repr(self):
        config = QueryStreamConfig(1, [1, 2], PermutationMode.RANDOM, seed=42)
        repr_str = repr(config)

        assert "QueryStreamConfig" in repr_str
        assert "stream_id=1" in repr_str
        assert "query_ids=[1, 2]" in repr_str
        assert "seed=42" in repr_str


class TestStreamQuery:
    def test_constructor_basic(self):
        query = StreamQuery(stream_id=1, position=0, query_id=5)

        assert query.stream_id == 1
        assert query.position == 0
        assert query.query_id == 5
        assert query.variant is None
        assert query.parameters is None
        assert query.sql is None

    def test_constructor_with_variant(self):
        query = StreamQuery(stream_id=2, position=1, query_id=14, variant="a")

        assert query.stream_id == 2
        assert query.position == 1
        assert query.query_id == 14
        assert query.variant == "a"

    def test_constructor_with_parameters(self):
        params = {"YEAR": 2000, "STATE": "CA"}
        query = StreamQuery(stream_id=3, position=2, query_id=10, parameters=params)

        assert query.stream_id == 3
        assert query.position == 2
        assert query.query_id == 10
        assert query.parameters == params

    def test_constructor_with_sql(self):
        sql = "SELECT * FROM test WHERE id = 1"
        query = StreamQuery(stream_id=4, position=3, query_id=7, sql=sql)

        assert query.stream_id == 4
        assert query.position == 3
        assert query.query_id == 7
        assert query.sql == sql

    def test_constructor_full(self):
        params = {"MONTH": 12}
        sql = "SELECT SUM(amount) FROM sales WHERE month = 12"
        query = StreamQuery(
            stream_id=5,
            position=4,
            query_id=23,
            variant="b",
            parameters=params,
            sql=sql,
        )

        assert query.stream_id == 5
        assert query.position == 4
        assert query.query_id == 23
        assert query.variant == "b"
        assert query.parameters == params
        assert query.sql == sql

    def test_dataclass_equality(self):
        query1 = StreamQuery(1, 0, 5, "a")
        query2 = StreamQuery(1, 0, 5, "a")
        query3 = StreamQuery(1, 0, 5, "b")

        assert query1 == query2
        assert query1 != query3

    def test_dataclass_repr(self):
        query = StreamQuery(1, 0, 5, "a")
        repr_str = repr(query)

        assert "StreamQuery" in repr_str
        assert "stream_id=1" in repr_str
        assert "query_id=5" in repr_str
        assert "variant='a'" in repr_str


class TestTPCDSPermutationGenerator:
    @pytest.fixture
    def generator(self):
        return TPCDSPermutationGenerator()

    @pytest.fixture
    def seeded_generator(self):
        return TPCDSPermutationGenerator(seed=42)

    def test_constructor_without_seed(self, generator):
        assert generator.seed is None

    def test_constructor_with_seed(self, seeded_generator):
        assert seeded_generator.seed == 42

    def test_generate_permutation_sequential(self, generator):
        items = [3, 1, 4, 2]
        result = generator.generate_permutation(items, PermutationMode.SEQUENTIAL)

        assert result == [1, 2, 3, 4]
        assert len(result) == len(items)

    def test_generate_permutation_sequential_empty(self, generator):
        items = []
        result = generator.generate_permutation(items, PermutationMode.SEQUENTIAL)

        assert result == []

    def test_generate_permutation_sequential_single(self, generator):
        items = [42]
        result = generator.generate_permutation(items, PermutationMode.SEQUENTIAL)

        assert result == [42]

    def test_generate_permutation_random(self, generator):
        items = [1, 2, 3, 4, 5]
        result = generator.generate_permutation(items, PermutationMode.RANDOM)

        assert sorted(result) == sorted(items)
        assert len(result) == len(items)

    def test_generate_permutation_random_reproducible(self):
        items = [1, 2, 3, 4, 5]

        random.seed(42)
        gen1 = TPCDSPermutationGenerator()
        result1 = gen1.generate_permutation(items, PermutationMode.RANDOM)

        random.seed(42)
        gen2 = TPCDSPermutationGenerator()
        result2 = gen2.generate_permutation(items, PermutationMode.RANDOM)

        assert result1 == result2

    def test_generate_permutation_tpcds_standard(self, seeded_generator):
        items = [1, 2, 3, 4, 5]
        result = seeded_generator.generate_permutation(items, PermutationMode.TPCDS_STANDARD)

        assert sorted(result) == sorted(items)
        assert len(result) == len(items)

    def test_generate_permutation_tpcds_standard_reproducible(self):
        items = [1, 2, 3, 4, 5]

        gen1 = TPCDSPermutationGenerator(seed=42)
        result1 = gen1.generate_permutation(items, PermutationMode.TPCDS_STANDARD)

        gen2 = TPCDSPermutationGenerator(seed=42)
        result2 = gen2.generate_permutation(items, PermutationMode.TPCDS_STANDARD)

        assert result1 == result2

    def test_generate_permutation_tpcds_standard_empty(self, seeded_generator):
        items = []
        result = seeded_generator.generate_permutation(items, PermutationMode.TPCDS_STANDARD)

        assert result == []

    def test_generate_permutation_tpcds_standard_single(self, seeded_generator):
        items = [42]
        result = seeded_generator.generate_permutation(items, PermutationMode.TPCDS_STANDARD)

        assert result == [42]

    def test_generate_permutation_invalid_mode(self, generator):
        items = [1, 2, 3]

        with pytest.raises(ValueError, match="Unknown permutation mode"):
            generator.generate_permutation(items, "invalid_mode")

    def test_random_permutation_direct(self, generator):
        items = [1, 2, 3, 4, 5]
        result = generator._random_permutation(items)

        assert sorted(result) == sorted(items)
        assert len(result) == len(items)
        assert items == [1, 2, 3, 4, 5]

    def test_tpcds_permutation_direct(self, seeded_generator):
        items = [1, 2, 3, 4, 5]
        result = seeded_generator._tpcds_permutation(items)

        assert sorted(result) == sorted(items)
        assert len(result) == len(items)
        assert items == [1, 2, 3, 4, 5]

    def test_tpcds_permutation_different_seeds(self):
        items = [1, 2, 3, 4, 5, 6, 7, 8]

        gen1 = TPCDSPermutationGenerator(seed=42)
        result1 = gen1._tpcds_permutation(items)

        gen2 = TPCDSPermutationGenerator(seed=100)
        result2 = gen2._tpcds_permutation(items)

        assert result1 != result2
        assert sorted(result1) == sorted(result2)


class TestTPCDSStreamManager:
    @pytest.fixture
    def mock_query_manager(self):
        mock_qm = Mock()
        mock_qm.get_query.return_value = "SELECT * FROM test;"
        mock_qm.get_enhanced_query.return_value = "SELECT * FROM test WHERE enhanced = 1;"
        return mock_qm

    @pytest.fixture
    def stream_manager(self, mock_query_manager):
        return TPCDSStreamManager(mock_query_manager)

    @pytest.fixture
    def sample_config(self):
        return QueryStreamConfig(
            stream_id=1,
            query_ids=[1, 2, 3],
            permutation_mode=PermutationMode.SEQUENTIAL,
            seed=42,
            parameter_seed=100,
        )

    def test_constructor_basic(self, mock_query_manager):
        manager = TPCDSStreamManager(mock_query_manager)

        assert manager.query_manager == mock_query_manager
        assert manager.stream_configs == []
        assert manager.streams == {}
        assert isinstance(manager.permutation_generator, TPCDSPermutationGenerator)

    def test_constructor_with_configs(self, mock_query_manager, sample_config):
        configs = [sample_config]
        manager = TPCDSStreamManager(mock_query_manager, configs)

        assert manager.query_manager == mock_query_manager
        assert manager.stream_configs == configs
        assert manager.streams == {}

    def test_add_stream(self, stream_manager, sample_config):
        assert len(stream_manager.stream_configs) == 0

        stream_manager.add_stream(sample_config)

        assert len(stream_manager.stream_configs) == 1
        assert stream_manager.stream_configs[0] == sample_config

    def test_add_stream_multiple(self, stream_manager, sample_config):
        config2 = QueryStreamConfig(2, [4, 5], PermutationMode.RANDOM)

        stream_manager.add_stream(sample_config)
        stream_manager.add_stream(config2)

        assert len(stream_manager.stream_configs) == 2
        assert stream_manager.stream_configs[0] == sample_config
        assert stream_manager.stream_configs[1] == config2

    def test_generate_streams_empty(self, stream_manager):
        result = stream_manager.generate_streams()

        assert result == {}
        assert stream_manager.streams == {}

    def test_generate_streams_single_config(self, stream_manager, sample_config):
        stream_manager.add_stream(sample_config)

        result = stream_manager.generate_streams()

        assert len(result) == 1
        assert 1 in result
        assert isinstance(result[1], list)
        assert len(result[1]) == 3

    def test_generate_streams_multiple_configs(self, stream_manager, sample_config):
        config2 = QueryStreamConfig(2, [4, 5], PermutationMode.RANDOM, seed=43)

        stream_manager.add_stream(sample_config)
        stream_manager.add_stream(config2)

        result = stream_manager.generate_streams()

        assert len(result) == 2
        assert 1 in result
        assert 2 in result
        assert len(result[1]) == 3
        assert len(result[2]) == 2

    def test_generate_single_stream_basic(self, stream_manager, sample_config):
        stream = stream_manager._generate_single_stream(sample_config)

        assert len(stream) == 3
        for i, query in enumerate(stream):
            assert isinstance(query, StreamQuery)
            assert query.stream_id == 1
            assert query.position == i
            assert query.query_id in [1, 2, 3]
            assert query.sql is not None

    def test_generate_single_stream_sequential_order(self, stream_manager, mock_query_manager):
        config = QueryStreamConfig(
            stream_id=1,
            query_ids=[3, 1, 4, 2],
            permutation_mode=PermutationMode.SEQUENTIAL,
            seed=42,
        )

        stream = stream_manager._generate_single_stream(config)

        query_ids = [query.query_id for query in stream]
        assert query_ids == [1, 2, 3, 4]

    def test_generate_single_stream_with_enhanced_query_manager(self, stream_manager, mock_query_manager):
        mock_query_manager.get_query.return_value = "SELECT * FROM test;"

        config = QueryStreamConfig(1, [1, 2], PermutationMode.SEQUENTIAL)
        stream = stream_manager._generate_single_stream(config)

        assert len(stream) == 2
        for query in stream:
            assert query.sql == "SELECT * FROM test;"

        assert mock_query_manager.get_query.called

    def test_generate_single_stream_without_enhanced_query_manager(self, stream_manager, mock_query_manager):
        del mock_query_manager.get_enhanced_query
        mock_query_manager.get_query.return_value = "STANDARD SQL"

        config = QueryStreamConfig(1, [1, 2], PermutationMode.SEQUENTIAL)
        stream = stream_manager._generate_single_stream(config)

        assert len(stream) == 2
        for query in stream:
            assert query.sql == "STANDARD SQL"

        assert mock_query_manager.get_query.called

    def test_generate_single_stream_empty_queries(self, stream_manager):
        config = QueryStreamConfig(1, [], PermutationMode.SEQUENTIAL)
        stream = stream_manager._generate_single_stream(config)

        assert stream == []

    def test_get_query_variants_single_query(self, stream_manager):
        variants = stream_manager._get_query_variants(1)
        assert variants == [None]

        variants = stream_manager._get_query_variants(5)
        assert variants == [None]

        variants = stream_manager._get_query_variants(50)
        assert variants == [None]

    def test_get_query_variants_multi_query(self, stream_manager):
        variants = stream_manager._get_query_variants(14)
        assert variants == ["a", "b"]

        variants = stream_manager._get_query_variants(23)
        assert variants == ["a", "b"]

        variants = stream_manager._get_query_variants(24)
        assert variants == ["a", "b"]

        variants = stream_manager._get_query_variants(39)
        assert variants == ["a", "b"]

    def test_generate_single_stream_with_multi_query(self, stream_manager, mock_query_manager):
        config = QueryStreamConfig(1, [14, 23], PermutationMode.SEQUENTIAL)
        stream = stream_manager._generate_single_stream(config)

        assert len(stream) == 4

        query_variants = [(q.query_id, q.variant) for q in stream]
        expected = [(14, "a"), (14, "b"), (23, "a"), (23, "b")]
        assert query_variants == expected

    def test_get_stream_existing(self, stream_manager, sample_config):
        stream_manager.add_stream(sample_config)
        stream_manager.generate_streams()

        result = stream_manager.get_stream(1)

        assert result is not None
        assert isinstance(result, list)
        assert len(result) == 3

    def test_get_stream_nonexistent(self, stream_manager):
        result = stream_manager.get_stream(999)

        assert result is None

    def test_get_stream_before_generation(self, stream_manager, sample_config):
        stream_manager.add_stream(sample_config)

        result = stream_manager.get_stream(1)

        assert result is None

    def test_get_stream_summary_existing(self, stream_manager, sample_config):
        stream_manager.add_stream(sample_config)
        stream_manager.generate_streams()

        summary = stream_manager.get_stream_summary(1)

        assert summary is not None
        assert summary["stream_id"] == 1
        assert summary["total_queries"] == 3
        assert summary["unique_queries"] == 3
        assert summary["query_list"] == ["1", "2", "3"]

    def test_get_stream_summary_nonexistent(self, stream_manager):
        summary = stream_manager.get_stream_summary(999)

        assert summary is None

    def test_get_stream_summary_with_multi_query(self, stream_manager, mock_query_manager):
        config = QueryStreamConfig(1, [14, 1], PermutationMode.SEQUENTIAL)
        stream_manager.add_stream(config)
        stream_manager.generate_streams()

        summary = stream_manager.get_stream_summary(1)

        assert summary is not None
        assert summary["stream_id"] == 1
        assert summary["total_queries"] == 3
        assert summary["unique_queries"] == 3
        assert summary["query_list"] == [
            "1",
            "14a",
            "14b",
        ]

    def test_permutation_generator_seed_setting(self, stream_manager, mock_query_manager):
        config = QueryStreamConfig(1, [1, 2, 3], PermutationMode.TPCDS_STANDARD, seed=42)

        stream_manager._generate_single_stream(config)

        assert stream_manager.permutation_generator.seed == 42

    def test_parameter_seed_setting(self, stream_manager, mock_query_manager):
        config = QueryStreamConfig(1, [1, 2], PermutationMode.SEQUENTIAL, parameter_seed=100)

        with patch("random.seed") as mock_seed:
            stream_manager._generate_single_stream(config)
            mock_seed.assert_called_with(100)


class TestCreateStandardStreams:
    @pytest.fixture
    def mock_query_manager(self):
        mock_qm = Mock()
        mock_qm.get_query.return_value = "SELECT * FROM test;"
        return mock_qm

    def test_create_standard_streams_basic(self, mock_query_manager):
        manager = create_standard_streams(mock_query_manager)

        assert isinstance(manager, TPCDSStreamManager)
        assert manager.query_manager == mock_query_manager
        assert len(manager.stream_configs) == 2

    def test_create_standard_streams_custom_count(self, mock_query_manager):
        manager = create_standard_streams(mock_query_manager, num_streams=5)

        assert len(manager.stream_configs) == 5

        stream_ids = [config.stream_id for config in manager.stream_configs]
        assert stream_ids == [0, 1, 2, 3, 4]

    def test_create_standard_streams_custom_query_range(self, mock_query_manager):
        manager = create_standard_streams(mock_query_manager, num_streams=1, query_range=(5, 10))

        assert len(manager.stream_configs) == 1
        config = manager.stream_configs[0]
        assert config.query_ids == [5, 6, 7, 8, 9, 10]

    def test_create_standard_streams_custom_seed(self, mock_query_manager):
        manager = create_standard_streams(mock_query_manager, num_streams=2, base_seed=1000)

        assert len(manager.stream_configs) == 2

        config1 = manager.stream_configs[0]
        config2 = manager.stream_configs[1]

        assert config1.seed == 1000
        assert config1.parameter_seed == 2000
        assert config2.seed == 1001
        assert config2.parameter_seed == 2001

    def test_create_standard_streams_permutation_mode(self, mock_query_manager):
        manager = create_standard_streams(mock_query_manager, num_streams=1)

        config = manager.stream_configs[0]
        assert config.permutation_mode == PermutationMode.TPCDS_STANDARD

    def test_create_standard_streams_full_range(self, mock_query_manager):
        manager = create_standard_streams(mock_query_manager, num_streams=1)

        config = manager.stream_configs[0]
        assert config.query_ids == list(range(1, 100))

    def test_create_standard_streams_single_query(self, mock_query_manager):
        manager = create_standard_streams(mock_query_manager, num_streams=1, query_range=(42, 42))

        config = manager.stream_configs[0]
        assert config.query_ids == [42]

    def test_create_standard_streams_generation(self, mock_query_manager):
        manager = create_standard_streams(mock_query_manager, num_streams=2, query_range=(1, 3))

        streams = manager.generate_streams()

        assert len(streams) == 2
        assert 0 in streams
        assert 1 in streams
        assert len(streams[0]) == 3
        assert len(streams[1]) == 3


class TestBackwardsCompatibility:
    def test_tpcds_streams_alias(self):
        assert TPCDSStreams == TPCDSStreamManager

    def test_tpcds_stream_runner_alias(self):
        assert TPCDSStreamRunner == TPCDSStreamManager

    def test_alias_functionality(self):
        mock_qm = Mock()

        streams = TPCDSStreams(mock_qm)
        runner = TPCDSStreamRunner(mock_qm)

        assert isinstance(streams, TPCDSStreamManager)
        assert isinstance(runner, TPCDSStreamManager)


class _NoVariantQueryManager:
    def __init__(self, body: str = "SELECT 42 AS v") -> None:
        self._body = body

    def get_query(self, query_id, seed=None):
        return self._body


class _VariantQueryManager:
    def get_query(self, query_id, seed=None, variant=None):
        return f"SELECT {query_id} AS q_{variant}"


class _RaisingQueryManager:
    def get_query(self, query_id, seed=None):
        raise RuntimeError("boom")


class TestGenerateSingleStreamSqlShape:
    @staticmethod
    def _rows(sql: str):
        connection = sqlite3.connect(":memory:")
        try:
            return connection.execute(sql).fetchall()
        finally:
            connection.close()

    @staticmethod
    def _generate(manager, query_id: int = 14):
        stream_manager = TPCDSStreamManager(manager)
        config = QueryStreamConfig(1, [query_id], PermutationMode.SEQUENTIAL)
        return stream_manager._generate_single_stream(config)

    def test_variant_fallback_executes_query_body(self):
        stream = self._generate(_NoVariantQueryManager())

        assert [query.variant for query in stream] == ["a", "b"]
        for query in stream:
            lines = query.sql.split("\n")
            assert lines[0].startswith("-- Query")
            assert lines[1] == "SELECT 42 AS v"
            assert self._rows(query.sql) == [(42,)]

    def test_variant_query_manager_executes_variant_body(self):
        stream = self._generate(_VariantQueryManager())

        assert [query.variant for query in stream] == ["a", "b"]
        for variant, query in zip(["a", "b"], stream):
            assert query.sql == f"SELECT 14 AS q_{variant}"
            assert self._rows(query.sql) == [(14,)]

    def test_generation_failure_propagates(self):
        stream_manager = TPCDSStreamManager(_RaisingQueryManager())
        config = QueryStreamConfig(1, [14], PermutationMode.SEQUENTIAL)

        with pytest.raises(RuntimeError, match="generation failed"):
            stream_manager._generate_single_stream(config)


