from __future__ import annotations

import pytest

from _project.scripts.explorer_pipeline.transformer import (
    CLOSED_CPU_FAMILIES,
    normalize_cpu_family,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_closed_cpu_families_contains_expected_members() -> None:
    expected = {
        "apple_silicon",
        "graviton",
        "intel_xeon",
        "intel_core",
        "amd_epyc",
        "amd_ryzen",
        "ampere_altra",
        "arm_neoverse",
        "unknown",
    }
    assert expected == CLOSED_CPU_FAMILIES


@pytest.mark.parametrize(
    ("raw_model", "expected_family"),
    [
        ("Apple M1", "apple_silicon"),
        ("Apple M2 Max", "apple_silicon"),
        ("Apple M3 Pro", "apple_silicon"),
        ("Apple M4", "apple_silicon"),
        ("Apple A15 Bionic", "apple_silicon"),
        ("AWS Graviton3", "graviton"),
        ("Graviton2 Processor", "graviton"),
        ("Intel(R) Xeon(R) Platinum 8259CL CPU @ 2.50GHz", "intel_xeon"),
        ("Intel Xeon E5-2686 v4", "intel_xeon"),
        ("Intel(R) Core(TM) i7-9750H CPU @ 2.60GHz", "intel_core"),
        ("Intel Core i9-13900K", "intel_core"),
        ("AMD EPYC 7B12", "amd_epyc"),
        ("AMD EPYC 9654 96-Core Processor", "amd_epyc"),
        ("AMD Ryzen 9 5950X 16-Core Processor", "amd_ryzen"),
        ("AMD Ryzen Threadripper 3990X", "amd_ryzen"),
        ("Ampere(R) Altra(R) Processor", "ampere_altra"),
        ("Ampere Altra Max", "ampere_altra"),
        ("Neoverse-N1", "arm_neoverse"),
        ("Neoverse-V2", "arm_neoverse"),
    ],
)
def test_normalize_cpu_family_known_patterns(raw_model: str, expected_family: str) -> None:
    actual = normalize_cpu_family(raw_model)
    assert actual == expected_family
    assert actual in CLOSED_CPU_FAMILIES


def test_normalize_cpu_family_unmatched_maps_to_explicit_unknown() -> None:
    unmatched_models = [
        "QuantumCore 9000",
        "RISC-V Generic Processor",
        "Cyrix Instead 6x86",
        "Transmeta Crusoe",
        "Custom ASIC Compute Block",
    ]
    for model in unmatched_models:
        family = normalize_cpu_family(model)
        assert family == "unknown"
        assert family in CLOSED_CPU_FAMILIES


def test_normalize_cpu_family_absent_or_empty_maps_to_none() -> None:
    assert normalize_cpu_family(None) is None
    assert normalize_cpu_family("") is None
    assert normalize_cpu_family("   ") is None


def test_transformer_cleans_empty_cpu_model() -> None:
    from _project.scripts.explorer_pipeline.transformer import BundleTransformer

    raw_env_empty = {"cpu_model": ""}
    raw_env_spaces = {"cpu_model": "   "}
    raw_env_valid = {"cpu_model": "Apple M1"}

    for env in (raw_env_empty, raw_env_spaces):
        cleaned_cpu = env.get("cpu_model", "").strip() or None
        assert cleaned_cpu is None
        assert normalize_cpu_family(cleaned_cpu) is None

    cleaned_valid = raw_env_valid["cpu_model"].strip() or None
    assert cleaned_valid == "Apple M1"
    assert normalize_cpu_family(cleaned_valid) == "apple_silicon"


