# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import importlib
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from benchbox.utils.clock import elapsed_seconds, mono_time

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class AIOperationType(Enum):
    EMBEDDING_SINGLE = "embedding_single"
    EMBEDDING_BATCH = "embedding_batch"
    EMBEDDING_LARGE_DIMENSION = "embedding_large_dimension"

    SENTIMENT_SINGLE = "sentiment_single"
    SENTIMENT_BATCH = "sentiment_batch"
    CLASSIFY_PRIORITY = "classify_priority"
    CLASSIFY_SEGMENT = "classify_segment"
    ENTITY_EXTRACTION = "entity_extraction"

    COSINE_SIMILARITY = "cosine_similarity"
    EUCLIDEAN_DISTANCE = "euclidean_distance"
    TOP_K_SIMILARITY = "top_k_similarity"


SKIP_FOR_DATAFRAME = [
    "generative_complete_simple",
    "generative_complete_customer_profile",
    "generative_question_answer",
    "generative_sql_generation",
    "transform_summarize_short",
    "transform_summarize_long",
    "transform_translate_comment",
    "transform_grammar_fix",
]


@dataclass
class AIModelCapabilities:
    has_sentence_transformers: bool = False
    has_textblob: bool = False
    has_spacy: bool = False
    has_numpy: bool = False
    has_torch: bool = False
    missing_packages: list[str] = field(default_factory=list)

    @classmethod
    def detect(cls) -> AIModelCapabilities:
        caps = cls()
        missing = []

        try:
            importlib.import_module("numpy")

            caps.has_numpy = True
        except ImportError:
            missing.append("numpy: pip install numpy")

        try:
            importlib.import_module("torch")

            caps.has_torch = True
        except ImportError:
            missing.append("torch: pip install torch")

        try:
            importlib.import_module("sentence_transformers")

            caps.has_sentence_transformers = True
        except ImportError:
            missing.append("sentence-transformers: pip install sentence-transformers")

        try:
            importlib.import_module("textblob")

            caps.has_textblob = True
        except ImportError:
            missing.append("textblob: pip install textblob")

        try:
            importlib.import_module("spacy")

            caps.has_spacy = True
        except ImportError:
            missing.append("spacy: pip install spacy && python -m spacy download en_core_web_sm")

        caps.missing_packages = missing
        return caps

    def can_run_embeddings(self) -> bool:
        return self.has_sentence_transformers and self.has_torch

    def can_run_sentiment(self) -> bool:
        return self.has_textblob

    def can_run_classification(self) -> bool:
        return self.has_textblob

    def can_run_entity_extraction(self) -> bool:
        return self.has_spacy or self.has_textblob

    def get_missing_for_operation(self, operation: AIOperationType) -> list[str]:
        missing = []

        if operation in (
            AIOperationType.EMBEDDING_SINGLE,
            AIOperationType.EMBEDDING_BATCH,
            AIOperationType.EMBEDDING_LARGE_DIMENSION,
        ):
            if not self.has_sentence_transformers:
                missing.append("sentence-transformers: pip install sentence-transformers")
            if not self.has_torch:
                missing.append("torch: pip install torch")

        elif operation in (AIOperationType.SENTIMENT_SINGLE, AIOperationType.SENTIMENT_BATCH) or operation in (
            AIOperationType.CLASSIFY_PRIORITY,
            AIOperationType.CLASSIFY_SEGMENT,
        ):
            if not self.has_textblob:
                missing.append("textblob: pip install textblob")

        elif operation == AIOperationType.ENTITY_EXTRACTION:
            if not self.has_spacy and not self.has_textblob:
                missing.append("spacy: pip install spacy && python -m spacy download en_core_web_sm")

        elif operation in (
            AIOperationType.COSINE_SIMILARITY,
            AIOperationType.EUCLIDEAN_DISTANCE,
            AIOperationType.TOP_K_SIMILARITY,
        ):
            if not self.has_numpy:
                missing.append("numpy: pip install numpy")

        return missing


@dataclass
class DataFrameAICapabilities:
    platform_name: str
    model_caps: AIModelCapabilities = field(default_factory=AIModelCapabilities.detect)
    supports_udf: bool = True
    supports_batch_inference: bool = True
    notes: str = ""

    def supports_operation(self, operation: AIOperationType) -> bool:
        if operation in (
            AIOperationType.EMBEDDING_SINGLE,
            AIOperationType.EMBEDDING_BATCH,
            AIOperationType.EMBEDDING_LARGE_DIMENSION,
        ):
            return self.model_caps.can_run_embeddings()

        if operation in (AIOperationType.SENTIMENT_SINGLE, AIOperationType.SENTIMENT_BATCH):
            return self.model_caps.can_run_sentiment()

        if operation in (AIOperationType.CLASSIFY_PRIORITY, AIOperationType.CLASSIFY_SEGMENT):
            return self.model_caps.can_run_classification()

        if operation == AIOperationType.ENTITY_EXTRACTION:
            return self.model_caps.can_run_entity_extraction()

        if operation in (
            AIOperationType.COSINE_SIMILARITY,
            AIOperationType.EUCLIDEAN_DISTANCE,
            AIOperationType.TOP_K_SIMILARITY,
        ):
            return self.model_caps.has_numpy

        return False

    def get_unsupported_operations(self) -> list[AIOperationType]:
        return [op for op in AIOperationType if not self.supports_operation(op)]


@dataclass
class DataFrameAIResult:
    operation_type: AIOperationType
    success: bool
    start_time: float
    end_time: float
    duration_ms: float
    rows_processed: int
    model_name: str | None = None
    model_load_time_ms: float | None = None
    inference_time_ms: float | None = None
    error_message: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def failure(
        cls,
        operation_type: AIOperationType,
        error_message: str,
        start_time: float | None = None,
    ) -> DataFrameAIResult:
        now = time.time()
        return cls(
            operation_type=operation_type,
            success=False,
            start_time=start_time or now,
            end_time=now,
            duration_ms=0.0 if start_time is None else (now - start_time) * 1000,
            rows_processed=0,
            error_message=error_message,
        )


@lru_cache(maxsize=4)
def _get_sentence_transformer_model(model_name: str) -> Any:
    from sentence_transformers import SentenceTransformer

    logger.info(f"Loading sentence-transformers model: {model_name}")
    return SentenceTransformer(model_name)


@lru_cache(maxsize=1)
def _get_spacy_model(model_name: str = "en_core_web_sm") -> Any:
    import spacy

    logger.info(f"Loading spaCy model: {model_name}")
    return spacy.load(model_name)


def generate_embedding(
    text: str,
    model_name: str = "all-MiniLM-L6-v2",
) -> list[float]:
    model = _get_sentence_transformer_model(model_name)
    embedding = model.encode(text, convert_to_numpy=True)
    return embedding.tolist()


def generate_embeddings_batch(
    texts: list[str],
    model_name: str = "all-MiniLM-L6-v2",
    batch_size: int = 32,
) -> list[list[float]]:
    model = _get_sentence_transformer_model(model_name)
    embeddings = model.encode(texts, convert_to_numpy=True, batch_size=batch_size)
    return [emb.tolist() for emb in embeddings]


def analyze_sentiment(text: str) -> float:
    from textblob import TextBlob

    blob = TextBlob(str(text) if text else "")
    return blob.sentiment.polarity


def analyze_sentiment_batch(texts: list[str]) -> list[float]:
    return [analyze_sentiment(text) for text in texts]


def classify_priority(text: str, labels: list[str] | None = None) -> str:
    if labels is None:
        labels = ["urgent", "normal", "low priority"]

    text_lower = str(text).lower() if text else ""

    urgent_keywords = ["urgent", "asap", "immediately", "critical", "emergency", "rush"]
    low_keywords = ["later", "whenever", "no rush", "low priority", "optional"]

    for keyword in urgent_keywords:
        if keyword in text_lower:
            return labels[0]

    for keyword in low_keywords:
        if keyword in text_lower:
            return labels[2]

    return labels[1]


def classify_segment(text: str, segments: list[str] | None = None) -> str:
    if segments is None:
        segments = ["AUTOMOBILE", "BUILDING", "FURNITURE", "HOUSEHOLD", "MACHINERY"]

    text_lower = str(text).lower() if text else ""

    segment_keywords = {
        "AUTOMOBILE": ["car", "vehicle", "auto", "motor", "drive", "engine"],
        "BUILDING": ["construct", "build", "house", "structure", "architect"],
        "FURNITURE": ["chair", "table", "desk", "bed", "sofa", "wood"],
        "HOUSEHOLD": ["home", "kitchen", "clean", "domestic", "family"],
        "MACHINERY": ["machine", "equipment", "industrial", "factory", "tool"],
    }

    for segment, keywords in segment_keywords.items():
        if segment in segments:
            for keyword in keywords:
                if keyword in text_lower:
                    return segment

    return segments[0]


def extract_entities(text: str, entity_types: list[str] | None = None) -> dict[str, list[str]]:
    if entity_types is None:
        entity_types = ["feature", "material", "color", "size"]

    result: dict[str, list[str]] = {etype: [] for etype in entity_types}

    text_str = str(text) if text else ""
    if not text_str:
        return result

    try:
        _extract_entities_spacy(text_str, entity_types, result)
    except Exception as e:
        logger.debug(f"spaCy extraction failed, using regex fallback: {e}")
        _extract_entities_regex(text_str, entity_types, result)

    return result


def _extract_entities_spacy(text_str: str, entity_types: list[str], result: dict[str, list[str]]) -> None:
    nlp = _get_spacy_model()
    doc = nlp(text_str)

    for ent in doc.ents:
        if ent.label_ in ("PRODUCT", "ORG") and "feature" in entity_types:
            result["feature"].append(ent.text)
        elif ent.label_ == "MATERIAL" and "material" in entity_types:
            result["material"].append(ent.text)

    for token in doc:
        if token.pos_ == "ADJ":
            text_lower = token.text.lower()
            if any(c in text_lower for c in ["red", "blue", "green", "black", "white", "yellow"]):
                if "color" in entity_types:
                    result["color"].append(token.text)
            elif any(s in text_lower for s in ["small", "medium", "large", "tiny", "huge"]):
                if "size" in entity_types:
                    result["size"].append(token.text)


def _extract_entities_regex(text_str: str, entity_types: list[str], result: dict[str, list[str]]) -> None:
    import re

    _regex_patterns: dict[str, str] = {
        "color": r"\b(red|blue|green|black|white|yellow|brown|gray|grey)\b",
        "size": r"\b(small|medium|large|tiny|huge|xl|xxl)\b",
        "material": r"\b(steel|iron|copper|brass|aluminum|plastic|wood|leather|cotton)\b",
    }

    for etype, pattern in _regex_patterns.items():
        if etype in entity_types:
            result[etype] = list(set(re.findall(pattern, text_str, re.IGNORECASE)))


def cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
    import numpy as np

    v1 = np.array(vec1)
    v2 = np.array(vec2)

    dot_product = np.dot(v1, v2)
    norm1 = np.linalg.norm(v1)
    norm2 = np.linalg.norm(v2)

    if norm1 == 0 or norm2 == 0:
        return 0.0

    return float(dot_product / (norm1 * norm2))


def euclidean_distance(vec1: list[float], vec2: list[float]) -> float:
    import numpy as np

    v1 = np.array(vec1)
    v2 = np.array(vec2)

    return float(np.linalg.norm(v1 - v2))


def top_k_similarity(
    query_vec: list[float],
    vectors: list[list[float]],
    k: int = 10,
) -> list[tuple[int, float]]:
    import numpy as np

    query = np.array(query_vec)
    query_norm = np.linalg.norm(query)

    if query_norm == 0:
        return []

    similarities = []
    for i, vec in enumerate(vectors):
        v = np.array(vec)
        v_norm = np.linalg.norm(v)
        if v_norm > 0:
            sim = float(np.dot(query, v) / (query_norm * v_norm))
            similarities.append((i, sim))

    similarities.sort(key=lambda x: x[1], reverse=True)
    return similarities[:k]


class DataFrameAIOperationsManager:
    def __init__(self, platform_name: str) -> None:
        self.platform_name = platform_name.lower()
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

        self._model_caps = AIModelCapabilities.detect()
        self._capabilities = self._build_capabilities()

    def _build_capabilities(self) -> DataFrameAICapabilities:
        notes = []

        if "polars" in self.platform_name:
            notes.append("Uses map_elements for row-level operations.")
        elif "pandas" in self.platform_name:
            notes.append("Uses apply for row-level operations.")
        elif "pyspark" in self.platform_name:
            notes.append("Uses Pandas UDFs for distributed inference.")

        if not self._model_caps.can_run_embeddings():
            notes.append("Embedding operations unavailable - install sentence-transformers.")
        if not self._model_caps.can_run_sentiment():
            notes.append("Sentiment analysis unavailable - install textblob.")
        if not self._model_caps.can_run_entity_extraction():
            notes.append("Entity extraction unavailable - install spacy.")

        return DataFrameAICapabilities(
            platform_name=self.platform_name,
            model_caps=self._model_caps,
            supports_udf=True,
            supports_batch_inference="pyspark" not in self.platform_name,
            notes=" ".join(notes),
        )

    def get_capabilities(self) -> DataFrameAICapabilities:
        return self._capabilities

    def supports_operation(self, operation: AIOperationType) -> bool:
        return self._capabilities.supports_operation(operation)

    def get_unsupported_message(self, operation: AIOperationType) -> str:
        missing = self._model_caps.get_missing_for_operation(operation)

        if not missing:
            return f"Operation {operation.value} is not supported on {self.platform_name}."

        msg = [
            f"Operation {operation.value} requires missing dependencies.",
            "",
            "Install missing packages:",
        ]
        for pkg in missing:
            msg.append(f"  {pkg}")

        return "\n".join(msg)

    def execute_embedding_single(
        self,
        texts: list[str],
        model_name: str = "all-MiniLM-L6-v2",
    ) -> DataFrameAIResult:
        start_time = time.time()
        operation = AIOperationType.EMBEDDING_SINGLE

        if not self.supports_operation(operation):
            return DataFrameAIResult.failure(
                operation,
                self.get_unsupported_message(operation),
                start_time,
            )

        try:
            model_load_start = mono_time()
            _ = _get_sentence_transformer_model(model_name)
            model_load_time = (elapsed_seconds(model_load_start)) * 1000

            inference_start = mono_time()
            embeddings = [generate_embedding(text, model_name) for text in texts]
            inference_time = (elapsed_seconds(inference_start)) * 1000

            end_time = time.time()
            return DataFrameAIResult(
                operation_type=operation,
                success=True,
                start_time=start_time,
                end_time=end_time,
                duration_ms=(end_time - start_time) * 1000,
                rows_processed=len(texts),
                model_name=model_name,
                model_load_time_ms=model_load_time,
                inference_time_ms=inference_time,
                metrics={
                    "embedding_dimension": len(embeddings[0]) if embeddings else 0,
                    "embeddings": embeddings,
                },
            )

        except Exception as e:
            self.logger.error(f"Embedding single failed: {e}")
            return DataFrameAIResult.failure(operation, str(e), start_time)

    def execute_embedding_batch(
        self,
        texts: list[str],
        model_name: str = "all-MiniLM-L6-v2",
        batch_size: int = 32,
    ) -> DataFrameAIResult:
        start_time = time.time()
        operation = AIOperationType.EMBEDDING_BATCH

        if not self.supports_operation(operation):
            return DataFrameAIResult.failure(
                operation,
                self.get_unsupported_message(operation),
                start_time,
            )

        try:
            model_load_start = mono_time()
            _ = _get_sentence_transformer_model(model_name)
            model_load_time = (elapsed_seconds(model_load_start)) * 1000

            inference_start = mono_time()
            embeddings = generate_embeddings_batch(texts, model_name, batch_size)
            inference_time = (elapsed_seconds(inference_start)) * 1000

            end_time = time.time()
            return DataFrameAIResult(
                operation_type=operation,
                success=True,
                start_time=start_time,
                end_time=end_time,
                duration_ms=(end_time - start_time) * 1000,
                rows_processed=len(texts),
                model_name=model_name,
                model_load_time_ms=model_load_time,
                inference_time_ms=inference_time,
                metrics={
                    "embedding_dimension": len(embeddings[0]) if embeddings else 0,
                    "batch_size": batch_size,
                    "embeddings": embeddings,
                },
            )

        except Exception as e:
            self.logger.error(f"Embedding batch failed: {e}")
            return DataFrameAIResult.failure(operation, str(e), start_time)

    def execute_sentiment_single(self, texts: list[str]) -> DataFrameAIResult:
        start_time = time.time()
        operation = AIOperationType.SENTIMENT_SINGLE

        if not self.supports_operation(operation):
            return DataFrameAIResult.failure(
                operation,
                self.get_unsupported_message(operation),
                start_time,
            )

        try:
            inference_start = mono_time()
            scores = [analyze_sentiment(text) for text in texts]
            inference_time = (elapsed_seconds(inference_start)) * 1000

            end_time = time.time()
            return DataFrameAIResult(
                operation_type=operation,
                success=True,
                start_time=start_time,
                end_time=end_time,
                duration_ms=(end_time - start_time) * 1000,
                rows_processed=len(texts),
                model_name="textblob",
                inference_time_ms=inference_time,
                metrics={"sentiment_scores": scores},
            )

        except Exception as e:
            self.logger.error(f"Sentiment single failed: {e}")
            return DataFrameAIResult.failure(operation, str(e), start_time)

    def execute_sentiment_batch(self, texts: list[str]) -> DataFrameAIResult:
        start_time = time.time()
        operation = AIOperationType.SENTIMENT_BATCH

        if not self.supports_operation(operation):
            return DataFrameAIResult.failure(
                operation,
                self.get_unsupported_message(operation),
                start_time,
            )

        try:
            inference_start = mono_time()
            scores = analyze_sentiment_batch(texts)
            inference_time = (elapsed_seconds(inference_start)) * 1000

            end_time = time.time()
            return DataFrameAIResult(
                operation_type=operation,
                success=True,
                start_time=start_time,
                end_time=end_time,
                duration_ms=(end_time - start_time) * 1000,
                rows_processed=len(texts),
                model_name="textblob",
                inference_time_ms=inference_time,
                metrics={"sentiment_scores": scores},
            )

        except Exception as e:
            self.logger.error(f"Sentiment batch failed: {e}")
            return DataFrameAIResult.failure(operation, str(e), start_time)

    def execute_classification(
        self,
        texts: list[str],
        labels: list[str],
        operation: AIOperationType,
    ) -> DataFrameAIResult:
        start_time = time.time()

        if not self.supports_operation(operation):
            return DataFrameAIResult.failure(
                operation,
                self.get_unsupported_message(operation),
                start_time,
            )

        try:
            inference_start = mono_time()

            if operation == AIOperationType.CLASSIFY_PRIORITY:
                predictions = [classify_priority(text, labels) for text in texts]
            else:
                predictions = [classify_segment(text, labels) for text in texts]

            inference_time = (elapsed_seconds(inference_start)) * 1000

            end_time = time.time()
            return DataFrameAIResult(
                operation_type=operation,
                success=True,
                start_time=start_time,
                end_time=end_time,
                duration_ms=(end_time - start_time) * 1000,
                rows_processed=len(texts),
                model_name="rule-based",
                inference_time_ms=inference_time,
                metrics={
                    "predictions": predictions,
                    "labels": labels,
                },
            )

        except Exception as e:
            self.logger.error(f"Classification failed: {e}")
            return DataFrameAIResult.failure(operation, str(e), start_time)

    def execute_entity_extraction(
        self,
        texts: list[str],
        entity_types: list[str] | None = None,
    ) -> DataFrameAIResult:
        start_time = time.time()
        operation = AIOperationType.ENTITY_EXTRACTION

        if not self.supports_operation(operation):
            return DataFrameAIResult.failure(
                operation,
                self.get_unsupported_message(operation),
                start_time,
            )

        try:
            inference_start = mono_time()
            extractions = [extract_entities(text, entity_types) for text in texts]
            inference_time = (elapsed_seconds(inference_start)) * 1000

            end_time = time.time()
            return DataFrameAIResult(
                operation_type=operation,
                success=True,
                start_time=start_time,
                end_time=end_time,
                duration_ms=(end_time - start_time) * 1000,
                rows_processed=len(texts),
                model_name="spacy" if self._model_caps.has_spacy else "regex",
                inference_time_ms=inference_time,
                metrics={
                    "extractions": extractions,
                    "entity_types": entity_types or ["feature", "material", "color", "size"],
                },
            )

        except Exception as e:
            self.logger.error(f"Entity extraction failed: {e}")
            return DataFrameAIResult.failure(operation, str(e), start_time)


def get_dataframe_ai_manager(platform_name: str) -> DataFrameAIOperationsManager | None:
    platform_lower = platform_name.lower()

    df_platforms = ("polars-df", "polars", "pandas-df", "pandas", "pyspark-df", "pyspark", "datafusion-df")
    if not any(p in platform_lower for p in df_platforms):
        logger.debug(f"Platform {platform_name} is not a DataFrame platform")
        return None

    try:
        return DataFrameAIOperationsManager(platform_name)
    except Exception as e:
        logger.warning(f"Failed to create AI operations manager for {platform_name}: {e}")
        return None


def get_skip_for_dataframe() -> list[str]:
    return SKIP_FOR_DATAFRAME.copy()


def validate_ai_primitives_dataframe_platform(platform_name: str) -> tuple[bool, str]:
    platform_lower = platform_name.lower()

    df_platforms = ("polars-df", "polars", "pandas-df", "pandas", "pyspark-df", "pyspark", "datafusion-df")
    if not any(p in platform_lower for p in df_platforms):
        return False, (
            f"AI Primitives DataFrame benchmark requires a DataFrame platform.\n"
            f"Platform '{platform_name}' is not a DataFrame platform.\n"
            f"\n"
            f"Supported platforms:\n"
            f"  - polars-df\n"
            f"  - pandas-df\n"
            f"  - pyspark-df\n"
            f"\n"
            f"Example:\n"
            f"  benchbox run --platform polars-df --benchmark ai_primitives --mode dataframe\n"
        )

    caps = AIModelCapabilities.detect()

    if not caps.can_run_embeddings() and not caps.can_run_sentiment():
        return False, (
            "AI Primitives DataFrame benchmark requires ML dependencies.\n"
            "No supported ML libraries are installed.\n"
            "\n"
            "Install at least one of:\n"
            "  - For embeddings: pip install sentence-transformers torch\n"
            "  - For sentiment: pip install textblob\n"
            "  - For entities: pip install spacy && python -m spacy download en_core_web_sm\n"
        )

    return True, ""


__all__ = [
    "AIOperationType",
    "AIModelCapabilities",
    "DataFrameAICapabilities",
    "DataFrameAIResult",
    "DataFrameAIOperationsManager",
    "SKIP_FOR_DATAFRAME",
    "get_dataframe_ai_manager",
    "get_skip_for_dataframe",
    "validate_ai_primitives_dataframe_platform",
    "generate_embedding",
    "generate_embeddings_batch",
    "analyze_sentiment",
    "analyze_sentiment_batch",
    "classify_priority",
    "classify_segment",
    "extract_entities",
    "cosine_similarity",
    "euclidean_distance",
    "top_k_similarity",
]
