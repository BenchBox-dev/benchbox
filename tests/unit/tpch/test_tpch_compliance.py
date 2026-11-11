#!/usr/bin/env python3
# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from unittest.mock import MagicMock

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestTPCHCompliance:
    def test_tpch_power_test_uses_correct_permutation(self):
        from benchbox.core.tpch.power_test import TPCHPowerTest
        from benchbox.core.tpch.streams import TPCHStreams

        mock_benchmark = MagicMock()
        mock_connection = MagicMock()

        power_test = TPCHPowerTest(
            benchmark=mock_benchmark,
            connection=mock_connection,
            stream_id=0,
            verbose=True,
        )

        expected_permutation = TPCHStreams.PERMUTATION_MATRIX[0]
        assert expected_permutation == [
            14,
            2,
            9,
            20,
            6,
            17,
            18,
            8,
            21,
            13,
            3,
            22,
            16,
            4,
            11,
            15,
            1,
            10,
            19,
            5,
            7,
            12,
        ]

        executed_queries = []

        def mock_get_query(query_id, **kwargs):
            executed_queries.append(query_id)
            return f"SELECT {query_id}"

        power_test._preflight_validate_generation = MagicMock()

        mock_benchmark.get_query = mock_get_query

        result = power_test.run()

        assert executed_queries == expected_permutation
        assert len(executed_queries) == 22

        assert result.config.stream_id == 0

    def test_tpch_power_test_different_streams_use_different_permutations(self):
        from benchbox.core.tpch.streams import TPCHStreams

        stream_0_perm = TPCHStreams.PERMUTATION_MATRIX[0]
        stream_1_perm = TPCHStreams.PERMUTATION_MATRIX[1]
        stream_2_perm = TPCHStreams.PERMUTATION_MATRIX[2]

        assert stream_0_perm != stream_1_perm
        assert stream_1_perm != stream_2_perm
        assert stream_0_perm != stream_2_perm

        assert set(stream_0_perm) == set(range(1, 23))
        assert set(stream_1_perm) == set(range(1, 23))
        assert set(stream_2_perm) == set(range(1, 23))

    def test_tpch_power_test_disabled_mode_disables_validation(self):
        from benchbox.core.tpch.power_test import TPCHPowerTest

        power_test = TPCHPowerTest(
            benchmark=MagicMock(),
            connection=MagicMock(),
            stream_id=0,
            validation_mode="disabled",
        )

        assert power_test.config.validation is False
        assert power_test.config.validation_mode == "disabled"

    def test_tpch_power_test_explicit_mode_preserves_non_stream_validation(self):
        from benchbox.core.tpch.power_test import TPCHPowerTest

        power_test = TPCHPowerTest(
            benchmark=MagicMock(),
            connection=MagicMock(),
            stream_id=1,
            validation_mode="loose",
        )

        assert power_test.config.validation is True
        assert power_test.config.validation_mode == "loose"

    def test_tpch_throughput_stream_permutations_keep_stream_zero_validation_context(self):
        from benchbox.core.tpch.streams import TPCHStreams
        from benchbox.core.tpch.throughput_test import TPCHThroughputTest

        mock_benchmark = MagicMock()
        mock_benchmark.get_query.return_value = "SELECT 1"
        mock_connection = MagicMock()
        mock_connection_factory = MagicMock(return_value=mock_connection)

        throughput_test = TPCHThroughputTest(
            benchmark=mock_benchmark,
            connection_factory=mock_connection_factory,
            verbose=True,
        )

        stream_0_queries = []
        stream_1_queries = []

        def capture_queries_stream_0(query_id, **kwargs):
            if kwargs["params"]["stream_id"] == 0:
                stream_0_queries.append(query_id)
            elif kwargs["params"]["stream_id"] == 1:
                stream_1_queries.append(query_id)
            return f"SELECT {query_id}"

        mock_benchmark.get_query = capture_queries_stream_0

        config = MagicMock()
        config.num_streams = 2
        config.scale_factor = 0.01
        config.verbose = True
        config.base_seed = 1

        throughput_test._execute_stream(0, 1, config)
        throughput_test._execute_stream(1, 2, config)

        expected_stream_0 = TPCHStreams.PERMUTATION_MATRIX[0]
        expected_stream_1 = TPCHStreams.PERMUTATION_MATRIX[1]

        assert stream_0_queries == expected_stream_0
        assert stream_1_queries == expected_stream_1
        assert stream_0_queries != stream_1_queries
        assert len(mock_connection.set_query_context.call_args_list) == 44
        assert all(not call.kwargs for call in mock_connection.set_query_context.call_args_list)
        assert all(len(call.args) == 1 for call in mock_connection.set_query_context.call_args_list)


class TestTPCDSCompliance:
    def test_tpcds_power_test_uses_stream_permutation(self):
        from benchbox.core.tpcds.power_test import TPCDSPowerTest

        mock_benchmark = MagicMock()
        mock_query_manager = MagicMock()
        mock_benchmark.query_manager = mock_query_manager
        mock_benchmark.get_queries.return_value = {str(i): f"query_{i}" for i in range(1, 100)}
        MagicMock()

        executed_query_order = []

        def mock_get_query(query_id, **kwargs):
            executed_query_order.append(query_id)
            return f"SELECT {query_id}"

        mock_benchmark.get_query = mock_get_query

        power_test = TPCDSPowerTest(benchmark=mock_benchmark, stream_id=0, verbose=True)

        result = power_test.run()

        assert len(executed_query_order) > 0
        assert executed_query_order != list(range(1, len(executed_query_order) + 1))

        assert result.config.stream_id == 0

    def test_tpcds_stream_manager_generates_different_permutations(self):
        from benchbox.core.tpcds.streams import create_standard_streams

        mock_query_manager = MagicMock()

        stream_manager = create_standard_streams(
            query_manager=mock_query_manager,
            num_streams=3,
            query_range=(1, 10),
            base_seed=42,
        )

        streams = stream_manager.generate_streams()

        stream_0_order = [sq.query_id for sq in streams[0] if sq.variant is None]
        stream_1_order = [sq.query_id for sq in streams[1] if sq.variant is None]
        stream_2_order = [sq.query_id for sq in streams[2] if sq.variant is None]

        assert stream_0_order != stream_1_order
        assert stream_1_order != stream_2_order
        assert stream_0_order != stream_2_order

        assert set(stream_0_order) == set(stream_1_order) == set(stream_2_order)


