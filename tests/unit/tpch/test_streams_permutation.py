# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark™ H (TPC-H) - Copyright © Transaction Processing Performance Council
# This implementation is based on the TPC-H specification.

# Licensed under the MIT License. See LICENSE file in the project root for details.

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from benchbox.core.tpch.streams import TPCHStreamRunner, TPCHStreams

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestTPCHPermutationMatrix:
    def test_permutation_matrix_exists(self):
        assert hasattr(TPCHStreams, "PERMUTATION_MATRIX")
        matrix = TPCHStreams.PERMUTATION_MATRIX

        assert len(matrix) == 41

        for i, permutation in enumerate(matrix):
            assert len(permutation) == 22, f"Permutation {i} has {len(permutation)} queries, expected 22"

    def test_permutation_matrix_values(self):
        matrix = TPCHStreams.PERMUTATION_MATRIX

        for i, permutation in enumerate(matrix):
            query_set = set(permutation)
            expected_queries = set(range(1, 23))

            assert query_set == expected_queries, f"Permutation {i} missing or has extra queries"

            assert len(permutation) == len(query_set), f"Permutation {i} has duplicate queries"

    def test_permutation_matrix_uniqueness(self):
        matrix = TPCHStreams.PERMUTATION_MATRIX
        unique_permutations = {tuple(perm) for perm in matrix}

        assert len(unique_permutations) == 41, "Some permutations are duplicates"

    def test_permutation_retrieval(self):
        streams = TPCHStreams(num_streams=1)

        perm_0 = streams._get_stream_permutation(0)
        perm_1 = streams._get_stream_permutation(1)
        perm_40 = streams._get_stream_permutation(40)

        assert perm_0 == TPCHStreams.PERMUTATION_MATRIX[0]
        assert perm_1 == TPCHStreams.PERMUTATION_MATRIX[1]
        assert perm_40 == TPCHStreams.PERMUTATION_MATRIX[40]

        perm_41 = streams._get_stream_permutation(41)
        assert perm_41 == TPCHStreams.PERMUTATION_MATRIX[0]

        perm_82 = streams._get_stream_permutation(82)
        assert perm_82 == TPCHStreams.PERMUTATION_MATRIX[0]

        perm_43 = streams._get_stream_permutation(43)
        assert perm_43 == TPCHStreams.PERMUTATION_MATRIX[2]


class TestTPCHStreamsInitialization:
    def test_default_initialization(self):
        streams = TPCHStreams()

        assert streams.num_streams == 1
        assert streams.scale_factor == 1.0
        assert streams.rng_seed == 1
        assert not streams.verbose
        assert streams.query_count == 22

    def test_custom_initialization(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            streams = TPCHStreams(
                num_streams=5,
                scale_factor=10.0,
                output_dir=output_dir,
                rng_seed=42,
                verbose=True,
            )

            assert streams.num_streams == 5
            assert streams.scale_factor == 10.0
            assert streams.output_dir == output_dir
            assert streams.rng_seed == 42
            assert streams.verbose

    def test_tools_path_detection(self):
        streams = TPCHStreams()

        assert hasattr(streams, "tpch_tools_path")
        assert streams.tpch_tools_path is not None

        assert isinstance(streams.tpch_tools_path, Path)


class TestTPCHStreamGeneration:
    @pytest.fixture
    def mock_streams(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            streams = TPCHStreams(
                num_streams=2,
                scale_factor=1.0,
                output_dir=output_dir,
                rng_seed=12345,
                verbose=False,
            )
            yield streams

    def test_stream_info_structure(self, mock_streams):
        stream_info = mock_streams.get_stream_info(0)

        required_fields = [
            "stream_id",
            "query_order",
            "scale_factor",
            "rng_seed",
            "query_count",
            "output_file",
            "permutation_index",
        ]

        for field in required_fields:
            assert field in stream_info, f"Missing field: {field}"

        assert stream_info["stream_id"] == 0
        assert isinstance(stream_info["query_order"], list)
        assert len(stream_info["query_order"]) == 22
        assert stream_info["scale_factor"] == 1.0
        assert stream_info["rng_seed"] == 12345
        assert stream_info["query_count"] == 22
        assert isinstance(stream_info["output_file"], Path)
        assert stream_info["permutation_index"] == 0

    def test_multiple_stream_info(self, mock_streams):
        stream_0_info = mock_streams.get_stream_info(0)
        stream_1_info = mock_streams.get_stream_info(1)

        assert stream_0_info["stream_id"] == 0
        assert stream_1_info["stream_id"] == 1

        assert stream_0_info["rng_seed"] == 12345
        assert stream_1_info["rng_seed"] == 12346

        assert stream_0_info["query_order"] != stream_1_info["query_order"]

        assert stream_0_info["permutation_index"] == 0
        assert stream_1_info["permutation_index"] == 1

    def test_all_streams_info(self, mock_streams):
        all_info = mock_streams.get_all_streams_info()

        assert len(all_info) == 2
        assert all_info[0]["stream_id"] == 0
        assert all_info[1]["stream_id"] == 1

        order_0 = all_info[0]["query_order"]
        order_1 = all_info[1]["query_order"]
        assert order_0 != order_1

    def test_invalid_stream_id(self, mock_streams):
        with pytest.raises(ValueError, match="Invalid stream ID: 5. Max streams: 2"):
            mock_streams.get_stream_info(5)


class TestTPCHStreamPermutationCompliance:
    def test_stream_permutation_deterministic(self):
        streams1 = TPCHStreams(num_streams=5, rng_seed=42)
        streams2 = TPCHStreams(num_streams=5, rng_seed=42)

        for stream_id in range(5):
            order1 = streams1.get_stream_info(stream_id)["query_order"]
            order2 = streams2.get_stream_info(stream_id)["query_order"]

            assert order1 == order2, f"Stream {stream_id} permutation not deterministic"

    def test_stream_permutation_different_for_different_streams(self):
        streams = TPCHStreams(num_streams=10)

        permutations = []
        for stream_id in range(10):
            order = tuple(streams.get_stream_info(stream_id)["query_order"])
            permutations.append(order)

        unique_permutations = set(permutations)
        assert len(unique_permutations) == 10, "Some streams have identical permutations"

    def test_permutation_wrapping_behavior(self):
        streams = TPCHStreams(num_streams=100)

        order_0 = streams.get_stream_info(0)["query_order"]
        order_41 = streams.get_stream_info(41)["query_order"]
        assert order_0 == order_41

        order_1 = streams.get_stream_info(1)["query_order"]
        order_42 = streams.get_stream_info(42)["query_order"]
        assert order_1 == order_42

        order_40 = streams.get_stream_info(40)["query_order"]
        order_81 = streams.get_stream_info(81)["query_order"]
        assert order_40 == order_81

    def test_specification_permutation_values(self):
        streams = TPCHStreams(num_streams=2)

        order_0 = streams.get_stream_info(0)["query_order"]

        expected_0 = [
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
        assert order_0 == expected_0

        order_1 = streams.get_stream_info(1)["query_order"]
        expected_1 = [
            21,
            3,
            18,
            5,
            11,
            7,
            6,
            20,
            17,
            12,
            16,
            15,
            13,
            10,
            2,
            8,
            14,
            19,
            9,
            22,
            1,
            4,
        ]
        assert order_1 == expected_1


class TestTPCHStreamQueryGeneration:
    @pytest.fixture
    def mock_qgen_compilation(self):
        with patch("benchbox.core.tpch.streams.TPCHStreams._compile_qgen") as mock_compile:
            with patch("benchbox.core.tpch.streams.TPCHStreams._generate_stream_queries_qgen") as mock_gen_qgen:
                mock_qgen_path = Path("/mock/qgen")
                mock_compile.return_value = mock_qgen_path

                mock_stream_file = Path("/mock/stream_0.sql")
                mock_gen_qgen.return_value = mock_stream_file

                yield {
                    "compile": mock_compile,
                    "gen_qgen": mock_gen_qgen,
                    "qgen_path": mock_qgen_path,
                    "stream_file": mock_stream_file,
                }

    def test_generate_streams_with_qgen(self, mock_qgen_compilation):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            streams = TPCHStreams(num_streams=2, output_dir=output_dir, verbose=False)

            result = streams.generate_streams()

            mock_qgen_compilation["compile"].assert_called_once()

            assert mock_qgen_compilation["gen_qgen"].call_count == 2

            assert len(result) == 2
            assert all(isinstance(f, Path) for f in result)

    def test_generate_streams_fails_without_qgen(self, mock_qgen_compilation):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            streams = TPCHStreams(num_streams=1, output_dir=output_dir, verbose=False)

            mock_qgen_compilation["compile"].side_effect = RuntimeError("qgen compilation failed")

            with pytest.raises(RuntimeError, match="TPC-H streams generation requires qgen compilation"):
                streams.generate_streams()

            mock_qgen_compilation["compile"].assert_called_once()

            assert mock_qgen_compilation["gen_qgen"].call_count == 0


class TestTPCHStreamRunner:
    def test_stream_runner_initialization(self):
        runner = TPCHStreamRunner(connection_string="sqlite:///test.db", dialect="sqlite", verbose=True)

        assert runner.connection_string == "sqlite:///test.db"
        assert runner.dialect == "sqlite"
        assert runner.verbose

    def test_stream_runner_methods_raise_not_implemented(self):
        runner = TPCHStreamRunner("connection_string")

        with pytest.raises(NotImplementedError, match="does not execute SQL"):
            runner.run_stream(Path("/fake/stream.sql"), 1)

        with pytest.raises(NotImplementedError, match="does not execute SQL"):
            runner.run_concurrent_streams([])


class TestTPCHStreamsIntegration:
    def test_stream_generation_full_workflow(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)

            streams = TPCHStreams(
                num_streams=3,
                scale_factor=0.01,
                output_dir=output_dir,
                rng_seed=999,
                verbose=False,
            )

            for i in range(3):
                info = streams.get_stream_info(i)
                assert info["stream_id"] == i
                assert len(info["query_order"]) == 22
                assert info["rng_seed"] == 999 + i

                query_set = set(info["query_order"])
                expected_queries = set(range(1, 23))
                assert query_set == expected_queries

            all_info = streams.get_all_streams_info()
            assert len(all_info) == 3

            orders = [info["query_order"] for info in all_info]
            unique_orders = {tuple(order) for order in orders}
            assert len(unique_orders) == 3, "All streams should have unique permutations"

    def test_permutation_spec_compliance_sample(self):
        streams = TPCHStreams(num_streams=41)

        for stream_id in range(41):
            actual_order = streams.get_stream_info(stream_id)["query_order"]
            expected_order = TPCHStreams.PERMUTATION_MATRIX[stream_id]

            assert actual_order == expected_order, f"Stream {stream_id} permutation doesn't match specification"


