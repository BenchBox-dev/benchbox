from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchbox.core.results.anonymization import AnonymizationConfig, AnonymizationManager

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

REPO_ROOT = Path(__file__).resolve().parents[3]
RESULTS_DATA = REPO_ROOT / "results-data"

DROP_FIELD_KEYS = frozenset(
    {
        "machine_id",
        "working_dir",
        "driver_runtime_python_executable",
        "database_path",
        "data_path",
        "engine_host",
    }
)

FORBIDDEN_RECOVERABLE_TOKENS = ("path_1c98692f827e",)

_SAFE_PUBLIC_DICTIONARY = (
    "benchbox",
    "BenchBox",
    "duckdb",
    "sqlite",
    "polars",
    "datafusion",
    "localhost",
    "127.0.0.1",
    ":memory:",
    "main",
    "test",
    "default",
)


def _corpus_json_files() -> list[Path]:
    return sorted(RESULTS_DATA.rglob("*.json"))


def _walk_keys(value: object, *, found: set[str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            found.add(str(key))
            _walk_keys(child, found=found)
    elif isinstance(value, list):
        for item in value:
            _walk_keys(item, found=found)


def _corpus_blob() -> str:
    parts: list[str] = []
    for path in _corpus_json_files():
        parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def test_corpus_directory_is_present() -> None:
    assert RESULTS_DATA.is_dir(), f"missing corpus directory: {RESULTS_DATA}"
    assert _corpus_json_files(), "corpus scan found no JSON files - the gate would be vacuous"


def test_drop_field_keys_are_absent_from_corpus() -> None:
    offenders: list[str] = []
    unreadable: list[str] = []

    for path in _corpus_json_files():
        rel = path.relative_to(REPO_ROOT)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            unreadable.append(f"{rel}: {type(exc).__name__}")
            continue
        keys: set[str] = set()
        _walk_keys(payload, found=keys)
        present = sorted(keys & DROP_FIELD_KEYS)
        if present:
            offenders.append(f"{rel}: {', '.join(present)}")

    assert not unreadable, "corpus files could not be parsed (failing closed):\n" + "\n".join(unreadable)
    assert not offenders, f"{len(offenders)} corpus file(s) still publish drop-field keys:\n" + "\n".join(
        offenders[:20]
    )


def test_measured_recoverable_tokens_from_dropped_fields_are_absent() -> None:
    blob = _corpus_blob()
    remaining = [token for token in FORBIDDEN_RECOVERABLE_TOKENS if token in blob]
    assert not remaining, f"recoverable tokens still present after re-derive: {remaining}"


def test_safe_dictionary_does_not_recover_dropped_field_pseudonyms() -> None:
    manager = AnonymizationManager()
    blob = _corpus_blob()
    hits: list[str] = []

    for candidate in _SAFE_PUBLIC_DICTIONARY:
        for prefix in ("path", "machine", "host"):
            token = manager._hash_public_identifier(candidate, prefix)
            if token in blob:
                hits.append(token)

    unique_hits = list(dict.fromkeys(hits))
    assert not unique_hits, f"dictionary-recoverable dropped-field tokens remain: {unique_hits}"


def test_retained_field_residual_oracle_is_documented_policy_not_a_gate_failure() -> None:
    empty = AnonymizationManager()
    candidate = "benchbox"
    token = empty._hash_public_identifier(candidate, "database")
    assert token.startswith("database_")
    assert len(token) == len("database_") + 12
    assert empty._hash_public_identifier(candidate, "database") == token


def test_operator_salt_defeats_safe_dictionary_for_new_raw_values() -> None:
    salted = AnonymizationManager(AnonymizationConfig(machine_id_salt="deployment-private-test-salt"))
    empty = AnonymizationManager()
    for candidate in _SAFE_PUBLIC_DICTIONARY:
        for prefix in ("database", "endpoint", "path"):
            assert salted._hash_public_identifier(candidate, prefix) != empty._hash_public_identifier(candidate, prefix)


def test_publication_fixed_point_ignores_operator_salt() -> None:
    token = AnonymizationManager()._hash_public_identifier("benchbox", "database")
    salted = AnonymizationManager(AnonymizationConfig(machine_id_salt="deployment-private-test-salt"))
    assert salted._hash_public_identifier(token, "database") == token


