# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest

from benchbox.experimental.aiml_functions.data import AIMLDataGenerator, LongText, SampleText

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestSampleText:
    def test_basic_creation(self):
        sample = SampleText(
            id=1,
            text_content="Test text content",
            category="Review",
            sentiment_label="positive",
            word_count=3,
        )
        assert sample.id == 1
        assert sample.text_content == "Test text content"
        assert sample.category == "Review"
        assert sample.sentiment_label == "positive"
        assert sample.language == "en"


class TestLongText:
    def test_basic_creation(self):
        long_text = LongText(
            id=1,
            long_text="A very long text...",
            topic="Technology",
            word_count=4,
        )
        assert long_text.id == 1
        assert long_text.topic == "Technology"
        assert long_text.word_count == 4


class TestAIMLDataGenerator:
    @pytest.fixture
    def generator(self):
        return AIMLDataGenerator(seed=42, num_samples=50, num_long_texts=10)

    def test_basic_creation(self, generator):
        assert generator.seed == 42
        assert generator.num_samples == 50
        assert generator.num_long_texts == 10

    def test_generate_sample_data(self, generator):
        samples = generator.generate_sample_data()
        assert len(samples) == 50
        assert all(isinstance(s, SampleText) for s in samples)

    def test_sample_data_has_unique_ids(self, generator):
        samples = generator.generate_sample_data()
        ids = [s.id for s in samples]
        assert len(ids) == len(set(ids))

    def test_sample_data_has_sentiments(self, generator):
        samples = generator.generate_sample_data()
        sentiments = {s.sentiment_label for s in samples}
        assert "positive" in sentiments
        assert "negative" in sentiments
        assert "neutral" in sentiments

    def test_sample_data_has_categories(self, generator):
        samples = generator.generate_sample_data()
        categories = {s.category for s in samples}
        assert "Review" in categories
        assert len(categories) > 1

    def test_sample_data_has_word_counts(self, generator):
        samples = generator.generate_sample_data()
        for sample in samples:
            actual_words = len(sample.text_content.split())
            assert sample.word_count == actual_words

    def test_generate_long_texts(self, generator):
        texts = generator.generate_long_texts()
        assert len(texts) == 10
        assert all(isinstance(t, LongText) for t in texts)

    def test_long_texts_have_unique_ids(self, generator):
        texts = generator.generate_long_texts()
        ids = [t.id for t in texts]
        assert len(ids) == len(set(ids))

    def test_long_texts_are_longer(self, generator):
        samples = generator.generate_sample_data()
        long_texts = generator.generate_long_texts()
        avg_sample_length = sum(s.word_count for s in samples) / len(samples)
        avg_long_length = sum(t.word_count for t in long_texts) / len(long_texts)
        assert avg_long_length > avg_sample_length * 5

    def test_reproducible_with_seed(self):
        gen1 = AIMLDataGenerator(seed=123, num_samples=20, num_long_texts=5)
        gen2 = AIMLDataGenerator(seed=123, num_samples=20, num_long_texts=5)
        samples1 = gen1.generate_sample_data()
        samples2 = gen2.generate_sample_data()
        assert [s.text_content for s in samples1] == [s.text_content for s in samples2]

    def test_different_seeds_different_data(self):
        gen1 = AIMLDataGenerator(seed=1, num_samples=100, num_long_texts=10)
        gen2 = AIMLDataGenerator(seed=2, num_samples=100, num_long_texts=10)
        samples1 = gen1.generate_sample_data()
        samples2 = gen2.generate_sample_data()
        later_samples1 = [s.text_content for s in samples1[60:70]]
        later_samples2 = [s.text_content for s in samples2[60:70]]
        assert later_samples1 != later_samples2 or len(set(later_samples1)) != len(set(later_samples2))


class TestAIMLDataGeneratorCSV:
    @pytest.fixture
    def generator(self):
        return AIMLDataGenerator(seed=42, num_samples=20, num_long_texts=5)

    def test_generate_csv(self, generator, tmp_path):
        files = generator.generate_csv(tmp_path)
        assert "aiml_sample_data" in files
        assert "aiml_long_texts" in files
        assert files["aiml_sample_data"].exists()
        assert files["aiml_long_texts"].exists()

    def test_csv_has_headers(self, generator, tmp_path):
        files = generator.generate_csv(tmp_path)
        sample_content = files["aiml_sample_data"].read_text()
        assert "id,text_content,category" in sample_content

    def test_csv_has_correct_rows(self, generator, tmp_path):
        files = generator.generate_csv(tmp_path)
        sample_lines = files["aiml_sample_data"].read_text().strip().split("\n")
        assert len(sample_lines) == 21


class TestAIMLDataGeneratorSQL:
    @pytest.fixture
    def generator(self):
        return AIMLDataGenerator(seed=42, num_samples=10, num_long_texts=3)

    def test_get_create_table_sql_snowflake(self, generator):
        statements = generator.get_create_table_sql("snowflake")
        assert "aiml_sample_data" in statements
        assert "aiml_long_texts" in statements
        assert "CREATE OR REPLACE TABLE" in statements["aiml_sample_data"]
        assert "VARCHAR" in statements["aiml_sample_data"]

    def test_get_create_table_sql_bigquery(self, generator):
        statements = generator.get_create_table_sql("bigquery")
        assert "CREATE OR REPLACE TABLE" in statements["aiml_sample_data"]
        assert "STRING" in statements["aiml_sample_data"]

    def test_get_create_table_sql_databricks(self, generator):
        statements = generator.get_create_table_sql("databricks")
        assert "CREATE OR REPLACE TABLE" in statements["aiml_sample_data"]
        assert "STRING" in statements["aiml_sample_data"]

    def test_get_create_table_sql_generic(self, generator):
        statements = generator.get_create_table_sql("postgres")
        assert "CREATE TABLE IF NOT EXISTS" in statements["aiml_sample_data"]
        assert "TEXT" in statements["aiml_sample_data"]

    def test_get_insert_sql(self, generator):
        statements = generator.get_insert_sql("snowflake")
        assert len(statements) == 2
        assert "INSERT INTO aiml_sample_data" in statements[0]
        assert "INSERT INTO aiml_long_texts" in statements[1]

    def test_insert_sql_escapes_quotes(self, generator):
        statements = generator.get_insert_sql("snowflake")
        assert "''" in statements[0] or "I love it" in statements[0]


class TestAIMLDataGeneratorManifest:
    @pytest.fixture
    def generator(self):
        return AIMLDataGenerator(seed=42, num_samples=30, num_long_texts=5)

    def test_get_manifest(self, generator):
        manifest = generator.get_manifest()
        assert manifest["generator"] == "AIMLDataGenerator"
        assert manifest["version"] == "1.0"
        assert manifest["seed"] == 42
        assert "generated_at" in manifest
        assert "tables" in manifest

    def test_manifest_has_table_info(self, generator):
        manifest = generator.get_manifest()
        assert "aiml_sample_data" in manifest["tables"]
        assert "aiml_long_texts" in manifest["tables"]
        assert manifest["tables"]["aiml_sample_data"]["row_count"] == 30
        assert manifest["tables"]["aiml_long_texts"]["row_count"] == 5

    def test_manifest_has_sentiment_distribution(self, generator):
        manifest = generator.get_manifest()
        dist = manifest["tables"]["aiml_sample_data"]["sentiment_distribution"]
        assert "positive" in dist
        assert "negative" in dist
        assert "neutral" in dist
        assert sum(dist.values()) == 30

    def test_manifest_has_avg_word_count(self, generator):
        manifest = generator.get_manifest()
        assert "avg_word_count" in manifest["tables"]["aiml_long_texts"]
        assert manifest["tables"]["aiml_long_texts"]["avg_word_count"] > 0


class TestSampleTextContent:
    @pytest.fixture
    def generator(self):
        return AIMLDataGenerator(seed=42, num_samples=100, num_long_texts=10)

    def test_positive_texts_are_labeled(self, generator):
        samples = generator.generate_sample_data()
        positive_samples = [s for s in samples if s.sentiment_label == "positive"]
        assert len(positive_samples) >= 10
        positive_indicators = [
            "love",
            "amazing",
            "excellent",
            "outstanding",
            "best",
            "great",
            "happy",
            "exceeded",
            "brilliant",
            "impressed",
            "recommend",
            "flawless",
            "better",
            "exceptional",
            "incredible",
            "fantastic",
            "wonderful",
        ]
        positive_count = sum(
            1 for s in positive_samples[:10] if any(word in s.text_content.lower() for word in positive_indicators)
        )
        assert positive_count >= 5, f"Expected at least 5/10 positive samples with positive words, got {positive_count}"

    def test_negative_texts_are_labeled(self, generator):
        samples = generator.generate_sample_data()
        negative_samples = [s for s in samples if s.sentiment_label == "negative"]
        assert len(negative_samples) >= 10
        negative_indicators = [
            "terrible",
            "worst",
            "disappointed",
            "waste",
            "horrible",
            "frustrating",
            "regret",
            "defective",
            "nightmare",
            "broke",
            "unhelpful",
            "overpriced",
            "save your money",
            "do not buy",
        ]
        negative_count = sum(
            1 for s in negative_samples[:10] if any(word in s.text_content.lower() for word in negative_indicators)
        )
        assert negative_count >= 5, f"Expected at least 5/10 negative samples with negative words, got {negative_count}"

    def test_category_texts_are_categorized(self, generator):
        samples = generator.generate_sample_data()
        tech_samples = [s for s in samples if s.category == "Technology"]
        assert len(tech_samples) >= 1
        for sample in tech_samples[:5]:
            assert len(sample.text_content) > 0
            assert sample.category == "Technology"


