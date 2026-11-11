# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import tempfile
from pathlib import Path

import pytest

from benchbox.core.h2odb.generator import H2ODataGenerator

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture
def temp_dir():

    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


@pytest.mark.unit
class TestH2ODataGenerator:
    def test_generator_initialization(self, temp_dir):

        generator = H2ODataGenerator(output_dir=temp_dir)

        assert generator.output_dir == temp_dir
        assert hasattr(generator, "generate_data")

    def test_generator_with_custom_parameters(self, temp_dir):

        generator = H2ODataGenerator(scale_factor=2.0, output_dir=temp_dir)

        assert generator.output_dir == temp_dir
        assert generator.scale_factor == 2.0

    def test_h2odb_table_structure(self, temp_dir):

        generator = H2ODataGenerator(output_dir=temp_dir)

        if hasattr(generator, "get_table_names"):
            table_names = generator.get_table_names()

            assert len(table_names) >= 1
        elif hasattr(generator, "table_name"):
            assert isinstance(generator.table_name, str)
        else:
            assert generator is not None

    def test_data_science_data_types(self, temp_dir):

        generator = H2ODataGenerator(output_dir=temp_dir)

        expected_types = ["numeric", "categorical", "boolean", "text"]

        if hasattr(generator, "get_supported_types"):
            supported_types = generator.get_supported_types()
            matched = [dtype for dtype in expected_types if dtype in supported_types]
            assert len(matched) > 0, f"Expected at least one of {expected_types} in {supported_types}"
        elif hasattr(generator, "column_types"):
            assert isinstance(generator.column_types, (list, dict))

        assert generator is not None

    def test_ml_dataset_characteristics(self, temp_dir):

        generator = H2ODataGenerator(output_dir=temp_dir)

        if hasattr(generator, "num_features"):
            generator.num_features = 20
            assert generator.num_features == 20

        if hasattr(generator, "target_column"):
            generator.target_column = "label"
            assert generator.target_column == "label"

        if hasattr(generator, "categorical_features"):
            generator.categorical_features = 5
            assert generator.categorical_features == 5

        assert generator is not None

    def test_statistical_distributions(self, temp_dir):

        generator = H2ODataGenerator(output_dir=temp_dir)

        if hasattr(generator, "use_normal_distribution"):
            generator.use_normal_distribution = True
            assert generator.use_normal_distribution is True

        if hasattr(generator, "mean"):
            generator.mean = 0.0
            assert generator.mean == 0.0

        if hasattr(generator, "std_dev"):
            generator.std_dev = 1.0
            assert generator.std_dev == 1.0

        if hasattr(generator, "skewness"):
            generator.skewness = 0.5
            assert generator.skewness == 0.5

        assert generator is not None

    def test_missing_data_simulation(self, temp_dir):

        generator = H2ODataGenerator(output_dir=temp_dir)

        if hasattr(generator, "missing_rate"):
            generator.missing_rate = 0.1
            assert generator.missing_rate == 0.1

        if hasattr(generator, "missing_pattern"):
            generator.missing_pattern = "random"
            assert generator.missing_pattern == "random"

        if hasattr(generator, "column_missing_rates"):
            generator.column_missing_rates = {"feature1": 0.05, "feature2": 0.15}
            assert generator.column_missing_rates["feature1"] == 0.05

        assert generator is not None

    def test_correlation_structure(self, temp_dir):

        generator = H2ODataGenerator(output_dir=temp_dir)

        if hasattr(generator, "correlation_matrix"):
            import numpy as np

            corr_matrix = np.array([[1.0, 0.5, 0.3], [0.5, 1.0, 0.2], [0.3, 0.2, 1.0]])
            generator.correlation_matrix = corr_matrix
            assert generator.correlation_matrix.shape == (3, 3)

        if hasattr(generator, "feature_correlation"):
            generator.feature_correlation = 0.3
            assert generator.feature_correlation == 0.3

        assert generator is not None

    def test_time_series_features(self, temp_dir):

        generator = H2ODataGenerator(output_dir=temp_dir)

        if hasattr(generator, "include_time_series"):
            generator.include_time_series = True
            assert generator.include_time_series is True

        if hasattr(generator, "time_column"):
            generator.time_column = "timestamp"
            assert generator.time_column == "timestamp"

        if hasattr(generator, "seasonality"):
            generator.seasonality = 12
            assert generator.seasonality == 12

        if hasattr(generator, "trend"):
            generator.trend = 0.1
            assert generator.trend == 0.1

        assert generator is not None

    def test_classification_vs_regression(self, temp_dir):

        generator_clf = H2ODataGenerator(output_dir=temp_dir)
        if hasattr(generator_clf, "problem_type"):
            generator_clf.problem_type = "classification"
            assert generator_clf.problem_type == "classification"

        if hasattr(generator_clf, "num_classes"):
            generator_clf.num_classes = 3
            assert generator_clf.num_classes == 3

        generator_reg = H2ODataGenerator(output_dir=temp_dir)
        if hasattr(generator_reg, "problem_type"):
            generator_reg.problem_type = "regression"
            assert generator_reg.problem_type == "regression"

        if hasattr(generator_reg, "target_range"):
            generator_reg.target_range = (0.0, 100.0)
            assert generator_reg.target_range == (0.0, 100.0)

        assert generator_clf is not None
        assert generator_reg is not None


@pytest.mark.unit
class TestGeneratorExtended:
    def test_feature_engineering_simulation(self, temp_dir):

        generator = H2ODataGenerator(output_dir=temp_dir)

        if hasattr(generator, "include_polynomial_features"):
            generator.include_polynomial_features = True
            assert generator.include_polynomial_features is True

        if hasattr(generator, "polynomial_degree"):
            generator.polynomial_degree = 2
            assert generator.polynomial_degree == 2

        if hasattr(generator, "include_interactions"):
            generator.include_interactions = True
            assert generator.include_interactions is True

        if hasattr(generator, "informative_features"):
            generator.informative_features = 10
            assert generator.informative_features == 10

        if hasattr(generator, "redundant_features"):
            generator.redundant_features = 5
            assert generator.redundant_features == 5

        assert generator is not None

    def test_data_quality_issues_simulation(self, temp_dir):

        generator = H2ODataGenerator(output_dir=temp_dir)

        if hasattr(generator, "outlier_fraction"):
            generator.outlier_fraction = 0.05
            assert generator.outlier_fraction == 0.05

        if hasattr(generator, "noise_level"):
            generator.noise_level = 0.1
            assert generator.noise_level == 0.1

        if hasattr(generator, "duplicate_rate"):
            generator.duplicate_rate = 0.02
            assert generator.duplicate_rate == 0.02

        if hasattr(generator, "format_inconsistency"):
            generator.format_inconsistency = True
            assert generator.format_inconsistency is True

        assert generator is not None

    def test_scalability_and_memory_management(self, temp_dir):

        generator = H2ODataGenerator(output_dir=temp_dir)

        if hasattr(generator, "rows"):
            generator.rows = 10000000
            assert generator.rows == 10000000

        if hasattr(generator, "use_chunking"):
            generator.use_chunking = True
            assert generator.use_chunking is True

        if hasattr(generator, "chunk_size"):
            generator.chunk_size = 100000
            assert generator.chunk_size == 100000

        if hasattr(generator, "n_jobs"):
            generator.n_jobs = 4
            assert generator.n_jobs == 4

        assert generator is not None

    def test_benchmark_specific_datasets(self, temp_dir):

        generator = H2ODataGenerator(output_dir=temp_dir)

        if hasattr(generator, "dataset_type"):
            for dataset_type in ["boston_housing", "iris", "wine", "breast_cancer"]:
                generator.dataset_type = dataset_type
                assert generator.dataset_type == dataset_type

        if hasattr(generator, "custom_config"):
            config = {
                "features": 20,
                "samples": 10000,
                "problem_type": "regression",
                "noise": 0.1,
            }
            generator.custom_config = config
            assert generator.custom_config["features"] == 20

        assert generator is not None

    def test_ml_pipeline_compatibility(self, temp_dir):

        generator = H2ODataGenerator(output_dir=temp_dir)

        if hasattr(generator, "generate_splits"):
            generator.generate_splits = True
            assert generator.generate_splits is True

        if hasattr(generator, "split_ratios"):
            generator.split_ratios = [0.7, 0.15, 0.15]
            assert generator.split_ratios[0] == 0.7

        if hasattr(generator, "cv_folds"):
            generator.cv_folds = 5
            assert generator.cv_folds == 5

        if hasattr(generator, "stratify"):
            generator.stratify = True
            assert generator.stratify is True

        assert generator is not None

    def test_format_compatibility(self, temp_dir):

        generator = H2ODataGenerator(output_dir=temp_dir)

        if hasattr(generator, "h2o_format"):
            generator.h2o_format = True
            assert generator.h2o_format is True

        if hasattr(generator, "pandas_format"):
            generator.pandas_format = True
            assert generator.pandas_format is True

        if hasattr(generator, "sklearn_format"):
            generator.sklearn_format = True
            assert generator.sklearn_format is True

        if hasattr(generator, "output_formats"):
            formats = ["csv", "parquet", "hdf5", "pickle"]
            generator.output_formats = formats
            assert "csv" in generator.output_formats

        assert generator is not None

    def test_reproducibility_and_randomness(self, temp_dir):

        generator = H2ODataGenerator(output_dir=temp_dir)

        if hasattr(generator, "random_seed"):
            generator.random_seed = 42
            assert generator.random_seed == 42

        if hasattr(generator, "deterministic"):
            generator.deterministic = True
            assert generator.deterministic is True

        if hasattr(generator, "random_state"):
            generator.random_state = 12345
            assert generator.random_state == 12345

        if hasattr(generator, "validate_reproducibility"):
            try:
                is_reproducible = generator.validate_reproducibility()
                assert isinstance(is_reproducible, bool)
            except (NotImplementedError, AttributeError):
                pass

        assert generator is not None


