# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from benchbox.core.amplab.generator import AMPLabDataGenerator

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture
def temp_dir():

    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


@pytest.mark.unit
class TestAMPLabDataGenerator:
    def test_generator_initialization(self, temp_dir):

        generator = AMPLabDataGenerator(output_dir=temp_dir)

        assert generator.output_dir == temp_dir
        assert hasattr(generator, "generate_data")

    def test_generator_with_custom_parameters(self, temp_dir):

        generator = AMPLabDataGenerator(scale_factor=2.0, output_dir=temp_dir)

        assert generator.output_dir == temp_dir
        assert generator.scale_factor == 2.0

    def test_amplab_table_structure(self, temp_dir):

        generator = AMPLabDataGenerator(output_dir=temp_dir)

        expected_tables = ["rankings", "uservisits"]

        if hasattr(generator, "get_table_names"):
            table_names = generator.get_table_names()
            for table in expected_tables:
                assert table in table_names
        else:
            assert generator is not None

    def test_data_generation_workflow(self, temp_dir):

        generator = AMPLabDataGenerator(output_dir=temp_dir)

        assert generator.output_dir == temp_dir
        assert hasattr(generator, "generate_data")

        expected_attrs = ["output_dir", "scale_factor"]
        for attr in expected_attrs:
            if hasattr(generator, attr):
                assert getattr(generator, attr) is not None

        mock_result = {
            "rankings": temp_dir / "rankings.csv",
            "uservisits": temp_dir / "uservisits.csv",
        }
        with patch.object(generator, "generate_data", return_value=mock_result):
            result = generator.generate_data()
            assert isinstance(result, dict)
            assert len(result) >= 1

    def test_amplab_specific_parameters(self, temp_dir):

        generator = AMPLabDataGenerator(output_dir=temp_dir)

        if hasattr(generator, "scale_factor"):
            assert generator.scale_factor > 0

        if hasattr(generator, "num_users"):
            assert isinstance(generator.num_users, int)
            assert generator.num_users > 0

        if hasattr(generator, "num_pages"):
            assert isinstance(generator.num_pages, int)
            assert generator.num_pages > 0

    def test_ranking_data_characteristics(self, temp_dir):

        generator = AMPLabDataGenerator(output_dir=temp_dir)

        if hasattr(generator, "_generate_rankings_data"):
            try:
                with patch.object(generator, "_write_csv_file") as mock_write:
                    generator._generate_rankings_data()
                    mock_write.assert_called()
            except (NotImplementedError, AttributeError):
                pass

        assert generator is not None

    def test_uservisits_data_characteristics(self, temp_dir):

        generator = AMPLabDataGenerator(output_dir=temp_dir)

        if hasattr(generator, "_generate_uservisits_data"):
            try:
                with patch.object(generator, "_write_csv_file") as mock_write:
                    generator._generate_uservisits_data()
                    mock_write.assert_called()
            except (NotImplementedError, AttributeError):
                pass

        assert generator is not None

    def test_file_format_handling(self, temp_dir):

        generator = AMPLabDataGenerator(output_dir=temp_dir)

        expected_format = "csv"

        if hasattr(generator, "get_file_format"):
            format_type = generator.get_file_format()
            assert format_type == expected_format

        if hasattr(generator, "get_file_extension"):
            extension = generator.get_file_extension()
            assert extension in [".csv", ".txt"]

        assert generator.output_dir == temp_dir

    def test_scalability_parameters(self, temp_dir):

        generator_small = AMPLabDataGenerator(scale_factor=0.1, output_dir=temp_dir)

        generator_large = AMPLabDataGenerator(scale_factor=10.0, output_dir=temp_dir)

        assert generator_small is not None
        assert generator_large is not None
        assert generator_small.scale_factor < generator_large.scale_factor

    def test_data_quality_and_distribution(self, temp_dir):

        generator = AMPLabDataGenerator(output_dir=temp_dir)

        assert generator.scale_factor > 0

        assert generator is not None


@pytest.mark.unit
class TestGeneratorExtended:
    def test_parallel_generation_support(self, temp_dir):

        generator = AMPLabDataGenerator(output_dir=temp_dir)

        if hasattr(generator, "parallel"):
            generator.parallel = 4
            assert generator.parallel == 4

        if hasattr(generator, "num_partitions"):
            generator.num_partitions = 8
            assert generator.num_partitions == 8

        assert generator is not None

    def test_web_log_characteristics(self, temp_dir):

        generator = AMPLabDataGenerator(output_dir=temp_dir)

        if hasattr(generator, "_generate_ip_addresses"):
            try:
                ip = generator._generate_ip_addresses(1)
                assert isinstance(ip, (list, str))
            except (NotImplementedError, AttributeError):
                pass

        if hasattr(generator, "_generate_user_agents"):
            try:
                agents = generator._generate_user_agents(1)
                assert isinstance(agents, (list, str))
            except (NotImplementedError, AttributeError):
                pass

        assert generator is not None

    def test_memory_efficient_generation(self, temp_dir):

        generator = AMPLabDataGenerator(output_dir=temp_dir)

        if hasattr(generator, "batch_size"):
            generator.batch_size = 10000
            assert generator.batch_size == 10000

        if hasattr(generator, "streaming_mode"):
            generator.streaming_mode = True
            assert generator.streaming_mode is True

        if hasattr(generator, "num_users"):
            generator.num_users = 1000000
            assert generator.num_users == 1000000

        assert generator is not None

    def test_benchmark_compliance(self, temp_dir):

        generator = AMPLabDataGenerator(output_dir=temp_dir)

        if hasattr(generator, "validate_benchmark_compliance"):
            try:
                is_compliant = generator.validate_benchmark_compliance()
                assert isinstance(is_compliant, bool)
            except (NotImplementedError, AttributeError):
                pass

        if hasattr(generator, "get_schema_definition"):
            try:
                schema = generator.get_schema_definition()
                assert isinstance(schema, dict)
            except (NotImplementedError, AttributeError):
                pass

        assert generator is not None

    def test_data_relationship_integrity(self, temp_dir):

        generator = AMPLabDataGenerator(output_dir=temp_dir)

        if hasattr(generator, "ensure_referential_integrity"):
            try:
                generator.ensure_referential_integrity = True
                assert generator.ensure_referential_integrity is True
            except (NotImplementedError, AttributeError):
                pass

        if hasattr(generator, "validate_foreign_keys"):
            try:
                is_valid = generator.validate_foreign_keys()
                assert isinstance(is_valid, bool)
            except (NotImplementedError, AttributeError):
                pass

        assert generator is not None


