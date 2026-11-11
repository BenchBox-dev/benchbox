# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import tempfile
from pathlib import Path

import pytest

from benchbox.core.tpch.streams import TPCHStreamRunner, TPCHStreams

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestTPCHStreamRunner:
    @pytest.fixture
    def stream_runner(self):
        return TPCHStreamRunner(connection_string="test://localhost", dialect="standard", verbose=False)

    @pytest.fixture
    def mock_stream_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".sql", delete=False, encoding="utf-8") as f:
            f.write("""-- TPC-H Stream 0
-- Query 14 (Stream 0, Position 1)
SELECT 1;

-- Query 2 (Stream 0, Position 2)
SELECT 2;
""")
            return Path(f.name)

    def test_run_stream_raises_not_implemented(self, stream_runner, mock_stream_file):
        try:
            with pytest.raises(NotImplementedError, match="does not execute SQL"):
                stream_runner.run_stream(mock_stream_file, stream_id=0)
        finally:
            mock_stream_file.unlink()

    def test_run_stream_raises_not_implemented_even_for_missing_file(self, stream_runner):
        nonexistent_file = Path("/tmp/nonexistent_stream.sql")
        with pytest.raises(NotImplementedError, match="does not execute SQL"):
            stream_runner.run_stream(nonexistent_file, stream_id=0)

    def test_run_concurrent_streams_raises_not_implemented(self, stream_runner, mock_stream_file):
        try:
            with pytest.raises(NotImplementedError, match="does not execute SQL"):
                stream_runner.run_concurrent_streams([mock_stream_file])
        finally:
            mock_stream_file.unlink()

    def test_run_concurrent_streams_raises_not_implemented_for_empty_list(self, stream_runner):
        with pytest.raises(NotImplementedError, match="does not execute SQL"):
            stream_runner.run_concurrent_streams([])


class TestTPCHStreamsIntegration:
    def test_stream_generation_and_execution_integration(self):

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)

            streams_manager = TPCHStreams(num_streams=1, scale_factor=0.01, output_dir=output_dir, verbose=False)

            stream_info = streams_manager.get_stream_info(0)

            assert stream_info["stream_id"] == 0
            assert stream_info["scale_factor"] == 0.01
            assert stream_info["query_count"] == 22
            assert stream_info["rng_seed"] == 1
            assert len(stream_info["query_order"]) == 22

            assert stream_info["query_order"] == [
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
            assert stream_info["permutation_index"] == 0

    def test_multiple_streams_have_different_permutations(self):
        streams_manager = TPCHStreams(num_streams=3, scale_factor=1.0)

        stream_0_info = streams_manager.get_stream_info(0)
        stream_1_info = streams_manager.get_stream_info(1)
        stream_2_info = streams_manager.get_stream_info(2)

        assert stream_0_info["query_order"] != stream_1_info["query_order"]
        assert stream_1_info["query_order"] != stream_2_info["query_order"]
        assert stream_0_info["query_order"] != stream_2_info["query_order"]

        assert len(stream_0_info["query_order"]) == 22
        assert len(stream_1_info["query_order"]) == 22
        assert len(stream_2_info["query_order"]) == 22

        assert set(stream_0_info["query_order"]) == set(range(1, 23))
        assert set(stream_1_info["query_order"]) == set(range(1, 23))
        assert set(stream_2_info["query_order"]) == set(range(1, 23))


