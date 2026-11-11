# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest

from benchbox.experimental.aiml_functions.functions import (
    AIMLFunction,
    AIMLFunctionCategory,
    AIMLFunctionRegistry,
    PlatformSupport,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestAIMLFunctionCategory:
    def test_all_categories(self):
        categories = list(AIMLFunctionCategory)
        assert AIMLFunctionCategory.SENTIMENT in categories
        assert AIMLFunctionCategory.CLASSIFICATION in categories
        assert AIMLFunctionCategory.SUMMARIZATION in categories
        assert AIMLFunctionCategory.COMPLETION in categories
        assert AIMLFunctionCategory.EMBEDDING in categories
        assert AIMLFunctionCategory.TRANSLATION in categories
        assert AIMLFunctionCategory.EXTRACTION in categories
        assert AIMLFunctionCategory.PREDICTION in categories

    def test_category_values(self):
        assert AIMLFunctionCategory.SENTIMENT.value == "sentiment"
        assert AIMLFunctionCategory.CLASSIFICATION.value == "classification"
        assert AIMLFunctionCategory.SUMMARIZATION.value == "summarization"


class TestPlatformSupport:
    def test_basic_creation(self):
        support = PlatformSupport(
            platform="snowflake",
            function_name="SNOWFLAKE.CORTEX.SENTIMENT",
            syntax_template="SNOWFLAKE.CORTEX.SENTIMENT({input})",
        )
        assert support.platform == "snowflake"
        assert support.function_name == "SNOWFLAKE.CORTEX.SENTIMENT"
        assert support.requires_model is False

    def test_with_model(self):
        support = PlatformSupport(
            platform="snowflake",
            function_name="SNOWFLAKE.CORTEX.COMPLETE",
            syntax_template="SNOWFLAKE.CORTEX.COMPLETE('{model}', {input})",
            requires_model=True,
            default_model="mistral-large",
        )
        assert support.requires_model is True
        assert support.default_model == "mistral-large"

    def test_format_query(self):
        support = PlatformSupport(
            platform="snowflake",
            function_name="SNOWFLAKE.CORTEX.SENTIMENT",
            syntax_template="SNOWFLAKE.CORTEX.SENTIMENT({input})",
        )
        query = support.format_query("text_column")
        assert query == "SNOWFLAKE.CORTEX.SENTIMENT(text_column)"

    def test_format_query_with_model(self):
        support = PlatformSupport(
            platform="snowflake",
            function_name="SNOWFLAKE.CORTEX.COMPLETE",
            syntax_template="SNOWFLAKE.CORTEX.COMPLETE('{model}', {input})",
            requires_model=True,
        )
        query = support.format_query("prompt_col", model="llama3")
        assert query == "SNOWFLAKE.CORTEX.COMPLETE('llama3', prompt_col)"

    def test_cost_per_token(self):
        support = PlatformSupport(
            platform="snowflake",
            function_name="test",
            syntax_template="{input}",
            cost_per_1k_tokens=0.01,
        )
        assert support.cost_per_1k_tokens == 0.01


class TestAIMLFunction:
    def test_basic_creation(self):
        func = AIMLFunction(
            function_id="sentiment_analysis",
            category=AIMLFunctionCategory.SENTIMENT,
            name="Sentiment Analysis",
            description="Analyze text sentiment",
        )
        assert func.function_id == "sentiment_analysis"
        assert func.category == AIMLFunctionCategory.SENTIMENT
        assert func.name == "Sentiment Analysis"

    def test_with_platforms(self):
        func = AIMLFunction(
            function_id="sentiment_analysis",
            category=AIMLFunctionCategory.SENTIMENT,
            name="Sentiment Analysis",
            description="Analyze text sentiment",
            platforms={
                "snowflake": PlatformSupport(
                    platform="snowflake",
                    function_name="SNOWFLAKE.CORTEX.SENTIMENT",
                    syntax_template="SNOWFLAKE.CORTEX.SENTIMENT({input})",
                ),
            },
        )
        assert "snowflake" in func.platforms
        assert func.is_supported_on("snowflake")
        assert not func.is_supported_on("bigquery")

    def test_is_supported_on_case_insensitive(self):
        func = AIMLFunction(
            function_id="test",
            category=AIMLFunctionCategory.SENTIMENT,
            name="Test",
            description="Test",
            platforms={
                "snowflake": PlatformSupport(
                    platform="snowflake",
                    function_name="test",
                    syntax_template="{input}",
                ),
            },
        )
        assert func.is_supported_on("Snowflake")
        assert func.is_supported_on("SNOWFLAKE")

    def test_get_platform_support(self):
        support = PlatformSupport(
            platform="snowflake",
            function_name="test",
            syntax_template="{input}",
            cost_per_1k_tokens=0.01,
        )
        func = AIMLFunction(
            function_id="test",
            category=AIMLFunctionCategory.SENTIMENT,
            name="Test",
            description="Test",
            platforms={"snowflake": support},
        )
        retrieved = func.get_platform_support("snowflake")
        assert retrieved is support
        assert func.get_platform_support("unknown") is None

    def test_to_dict(self):
        func = AIMLFunction(
            function_id="test",
            category=AIMLFunctionCategory.SENTIMENT,
            name="Test",
            description="Test",
            platforms={
                "snowflake": PlatformSupport(
                    platform="snowflake",
                    function_name="test",
                    syntax_template="{input}",
                ),
            },
            latency_class="medium",
        )
        d = func.to_dict()
        assert d["function_id"] == "test"
        assert d["category"] == "sentiment"
        assert d["name"] == "Test"
        assert "snowflake" in d["platforms"]
        assert d["latency_class"] == "medium"


class TestAIMLFunctionRegistry:
    @pytest.fixture
    def registry(self):
        return AIMLFunctionRegistry()

    def test_basic_creation(self, registry):
        functions = registry.get_all_functions()
        assert len(functions) > 0
        assert "sentiment_analysis" in functions

    def test_get_function(self, registry):
        func = registry.get_function("sentiment_analysis")
        assert func is not None
        assert func.function_id == "sentiment_analysis"
        assert func.category == AIMLFunctionCategory.SENTIMENT

    def test_get_function_not_found(self, registry):
        assert registry.get_function("unknown_function") is None

    def test_get_functions_by_category(self, registry):
        sentiment_funcs = registry.get_functions_by_category(AIMLFunctionCategory.SENTIMENT)
        assert len(sentiment_funcs) >= 1
        assert all(f.category == AIMLFunctionCategory.SENTIMENT for f in sentiment_funcs)

    def test_get_functions_for_platform(self, registry):
        snowflake_funcs = registry.get_functions_for_platform("snowflake")
        assert len(snowflake_funcs) > 0
        assert all(f.is_supported_on("snowflake") for f in snowflake_funcs)

    def test_get_supported_platforms(self, registry):
        platforms = registry.get_supported_platforms()
        assert "snowflake" in platforms
        assert "bigquery" in platforms
        assert "databricks" in platforms

    def test_get_categories(self, registry):
        categories = registry.get_categories()
        assert AIMLFunctionCategory.SENTIMENT in categories
        assert AIMLFunctionCategory.COMPLETION in categories

    def test_registered_functions(self, registry):
        assert registry.get_function("sentiment_analysis") is not None
        assert registry.get_function("text_classification") is not None
        assert registry.get_function("summarization") is not None
        assert registry.get_function("completion") is not None
        assert registry.get_function("embedding") is not None
        assert registry.get_function("translation") is not None
        assert registry.get_function("entity_extraction") is not None

    def test_sentiment_function_platforms(self, registry):
        func = registry.get_function("sentiment_analysis")
        assert func is not None
        assert func.is_supported_on("snowflake")
        assert func.is_supported_on("databricks")

    def test_completion_function_requires_model(self, registry):
        func = registry.get_function("completion")
        assert func is not None
        for platform in func.platforms.values():
            assert platform.requires_model is True

    def test_export_registry(self, registry):
        export = registry.export_registry()
        assert "functions" in export
        assert "platforms" in export
        assert "categories" in export
        assert len(export["functions"]) > 0


class TestAIMLFunctionPlatformCoverage:
    @pytest.fixture
    def registry(self):
        return AIMLFunctionRegistry()

    def test_snowflake_coverage(self, registry):
        funcs = registry.get_functions_for_platform("snowflake")
        categories = {f.category for f in funcs}
        assert AIMLFunctionCategory.SENTIMENT in categories
        assert AIMLFunctionCategory.CLASSIFICATION in categories
        assert AIMLFunctionCategory.SUMMARIZATION in categories
        assert AIMLFunctionCategory.COMPLETION in categories
        assert AIMLFunctionCategory.EMBEDDING in categories
        assert AIMLFunctionCategory.TRANSLATION in categories

    def test_databricks_coverage(self, registry):
        funcs = registry.get_functions_for_platform("databricks")
        assert len(funcs) >= 4
        categories = {f.category for f in funcs}
        assert AIMLFunctionCategory.SENTIMENT in categories

    def test_bigquery_coverage(self, registry):
        funcs = registry.get_functions_for_platform("bigquery")
        assert len(funcs) >= 3
        categories = {f.category for f in funcs}
        assert AIMLFunctionCategory.COMPLETION in categories or AIMLFunctionCategory.SUMMARIZATION in categories


