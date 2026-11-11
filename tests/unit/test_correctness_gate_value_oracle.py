from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchbox.core.expected_results.models import ValidationMode, ValidationResult
from benchbox.core.results.result_digest import compute_result_digest, digests_match

pytestmark = [pytest.mark.unit, pytest.mark.fast]


_BASE_ROWS = [
    ("A", "F", 37734107, 56586554400.73, 53758257134.87, 0.05, 25.52),
    ("N", "F", 991417, 1487504710.38, 1413082168.05, 0.05, 25.51),
    ("N", "O", 74476040, 111701729697.74, 106118230307.61, 0.05, 25.50),
    ("R", "F", 37719753, 56568041380.90, 53741292684.60, 0.05, 25.51),
]


def _perturb_aggregate(rows):
    mutated = [list(r) for r in rows]
    mutated[0][3] = mutated[0][3] * (1 + 1e-5)
    return [tuple(r) for r in mutated]


def _swap_two_columns(rows):
    return [(a, b, c, e, d, f, g) for (a, b, c, d, e, f, g) in rows]


def _change_rounding(rows):
    mutated = [list(r) for r in rows]
    mutated[2][4] = round(mutated[2][4])
    return [tuple(r) for r in mutated]


def _permute_a_column(rows):
    mutated = [list(r) for r in rows]
    mutated[0][6], mutated[1][6] = mutated[1][6], mutated[0][6]
    return [tuple(r) for r in mutated]


_MUTATIONS = {
    "perturbed_aggregate": _perturb_aggregate,
    "swapped_columns": _swap_two_columns,
    "changed_rounding": _change_rounding,
    "permuted_column": _permute_a_column,
}


@pytest.mark.parametrize("name", sorted(_MUTATIONS))
def test_digest_flips_on_cardinality_preserving_value_mutation(name):
    base_digest = compute_result_digest(_BASE_ROWS)
    mutated_rows = _MUTATIONS[name](_BASE_ROWS)

    assert len(mutated_rows) == len(_BASE_ROWS), f"{name} changed cardinality"

    mutated_digest = compute_result_digest(mutated_rows)
    assert mutated_digest != base_digest, f"{name} was NOT caught by the value digest"


def test_at_least_three_distinct_mutation_classes_are_caught():
    base_digest = compute_result_digest(_BASE_ROWS)
    caught = [
        name
        for name, mutate in _MUTATIONS.items()
        if compute_result_digest(mutate(_BASE_ROWS)) != base_digest and len(mutate(_BASE_ROWS)) == len(_BASE_ROWS)
    ]
    assert len(caught) >= 3, f"expected >=3 caught cardinality-preserving mutations, got {sorted(caught)}"


def test_column_swap_with_identical_columns_is_a_known_blind_spot():
    rows = [("x", 1.0, 1.0), ("y", 2.0, 2.0)]
    swapped = [(a, c, b) for (a, b, c) in rows]
    assert compute_result_digest(rows) == compute_result_digest(swapped)


_AVG_DISC = 0.05


def test_precision_floor_is_relative_below_floor_not_caught():
    base = compute_result_digest([(_AVG_DISC,)])
    just_below = compute_result_digest([(_AVG_DISC + 4e-8,)])
    assert just_below == base, "an error below the relative floor must NOT change the digest"


def test_precision_floor_is_relative_above_floor_caught():
    base = compute_result_digest([(_AVG_DISC,)])
    just_above = compute_result_digest([(_AVG_DISC + 6e-8,)])
    assert just_above != base, "an error above the relative floor MUST change the digest"


def test_precision_floor_is_uniform_relative_across_magnitudes():
    revenue = 56586554400.73
    base = compute_result_digest([(revenue,)])
    assert compute_result_digest([(revenue + 0.02,)]) == base, "below the relative floor -> invisible"
    assert compute_result_digest([(revenue * (1 + 1e-5),)]) != base, "above the relative floor -> caught"


def test_digest_couples_value_with_numeric_type():
    int_digest = compute_result_digest([(37734107,)])
    float_digest = compute_result_digest([(37734107.0,)])
    decimal_digest = compute_result_digest([(Decimal("37734107"),)])

    assert int_digest != float_digest, "int and float of equal value must (today) differ -- value+type coupling"
    assert int_digest != decimal_digest, "int and Decimal of equal value must (today) differ -- value+type coupling"
    assert float_digest == compute_result_digest([(Decimal("37734107.00"),)])


_GATE_ROWS = {"1": 4, "6": 1, "3": 10}


def _make_fake_validator(stored_digests):

    class _FakeRegistry:
        def get_expected_result(self, benchmark_type, query_id, scale_factor, stream_id):
            if stream_id is not None and int(stream_id) > 0:
                return None
            qid = str(query_id)
            if qid not in stored_digests:
                return None
            return SimpleNamespace(
                value_digest=stored_digests[qid], expected_row_count=_GATE_ROWS.get(qid), scale_factor=1.0
            )

    class _FakeValidator:
        def __init__(self):
            self.registry = _FakeRegistry()

        def validate_query_result(self, benchmark_type, query_id, actual_row_count, scale_factor, stream_id):
            expected = _GATE_ROWS.get(str(query_id))
            if expected is None:
                return ValidationResult(
                    is_valid=True,
                    query_id=str(query_id),
                    expected_row_count=None,
                    actual_row_count=actual_row_count,
                    validation_mode=ValidationMode.SKIP,
                )
            return ValidationResult(
                is_valid=expected == actual_row_count,
                query_id=str(query_id),
                expected_row_count=expected,
                actual_row_count=actual_row_count,
                validation_mode=ValidationMode.EXACT,
            )

    return _FakeValidator


def _gate_payload(digests):
    return {
        "queries": [
            {"id": qid, "status": "SUCCESS", "rows": rows, "stream": 0, "digest": digests.get(qid)}
            for qid, rows in _GATE_ROWS.items()
        ]
    }


def _stored_digests():
    sources = {
        "1": _BASE_ROWS,
        "6": [(123456.789,)],
        "3": [(i, float(i) * 1.5, f"k{i}") for i in range(10)],
    }
    return {qid: compute_result_digest(rows) for qid, rows in sources.items()}, sources


def test_gate_goes_red_on_value_mutation_with_unchanged_cardinality(monkeypatch):
    import tests.integration.test_local_platform_benchmark_matrix as matrix

    stored, sources = _stored_digests()
    monkeypatch.setattr(matrix, "QueryValidator", _make_fake_validator(stored))
    monkeypatch.setenv("BENCHBOX_STRICT_EXPECTED_RESULTS", "1")

    correct_emitted = dict(stored)
    matrix._validate_against_expected_results(
        _gate_payload(correct_emitted), "tpch", 1.0, expected_query_ids=set(_GATE_ROWS)
    )

    mutated_rows = _perturb_aggregate(sources["1"])
    assert len(mutated_rows) == len(sources["1"])
    mutated_emitted = dict(stored)
    mutated_emitted["1"] = compute_result_digest(mutated_rows)

    with pytest.raises(AssertionError, match="VALUE DIGEST mismatch"):
        matrix._validate_against_expected_results(
            _gate_payload(mutated_emitted), "tpch", 1.0, expected_query_ids=set(_GATE_ROWS)
        )


def test_gate_goes_red_when_digest_missing_under_strict(monkeypatch):
    import tests.integration.test_local_platform_benchmark_matrix as matrix

    stored, _ = _stored_digests()
    monkeypatch.setattr(matrix, "QueryValidator", _make_fake_validator(stored))
    monkeypatch.setenv("BENCHBOX_STRICT_EXPECTED_RESULTS", "1")

    emitted = dict(stored)
    emitted["3"] = None

    with pytest.raises(AssertionError, match="strict value-digest"):
        matrix._validate_against_expected_results(
            _gate_payload(emitted), "tpch", 1.0, expected_query_ids=set(_GATE_ROWS)
        )


def test_digests_match_never_silently_matches_missing():
    assert digests_match("abc", "abc") is True
    assert digests_match("abc", None) is False
    assert digests_match(None, "abc") is False
    assert digests_match(None, None) is False


def test_sf1_value_digest_is_not_reused_at_other_scales():
    import tests.integration.test_local_platform_benchmark_matrix as matrix

    class _ScaleIndependentRegistry:
        def get_expected_result(self, benchmark_type, query_id, scale_factor, stream_id):
            return SimpleNamespace(value_digest="sf1digest", expected_row_count=4, scale_factor=1.0)

    validator = SimpleNamespace(registry=_ScaleIndependentRegistry())

    assert matrix._stored_value_digest(validator, "tpch", "1", 1.0, 0) == "sf1digest"
    assert matrix._stored_value_digest(validator, "tpch", "1", 0.01, 0) is None
    assert matrix._stored_value_digest(validator, "tpch", "1", 10.0, 0) is None


@pytest.fixture
def digest_reference_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from benchbox.core.expected_results import loader

    path = tmp_path / "digest-reference.json"
    monkeypatch.setattr(loader, "_TPCH_VALUE_DIGEST_REFERENCE_PATH", path)
    return path


@pytest.mark.parametrize("seed", [0, -1, 98741, None])
def test_digest_snapshot_seed_comes_from_metadata(digest_reference_path: Path, seed: int) -> None:
    from benchbox.core.expected_results.loader import load_tpch_value_digest_seed

    digest_reference_path.write_text(
        json.dumps({"benchmark": "tpch", "scale_factor": 1.0, "reference_seed": seed}), encoding="utf-8"
    )
    assert load_tpch_value_digest_seed() == seed


@pytest.mark.parametrize("seed", [True, False, "17", 1.5, {}, []])
def test_digest_snapshot_rejects_noninteger_seed(digest_reference_path: Path, seed) -> None:
    from benchbox.core.expected_results.loader import load_tpch_value_digest_seed

    digest_reference_path.write_text(
        json.dumps({"benchmark": "tpch", "scale_factor": 1.0, "reference_seed": seed}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="reference_seed must be an integer or null"):
        load_tpch_value_digest_seed()


@pytest.mark.parametrize(
    "payload",
    [
        {"benchmark": "tpch", "scale_factor": 1.0},
        {"benchmark": "tpcds", "scale_factor": 1.0, "reference_seed": 31},
        {"benchmark": "tpch", "scale_factor": 0.1, "reference_seed": 31},
        {"benchmark": "tpch", "scale_factor": True, "reference_seed": 31},
        [],
    ],
)
def test_digest_snapshot_rejects_missing_seed_or_wrong_identity(digest_reference_path: Path, payload) -> None:
    from benchbox.core.expected_results.loader import load_tpch_value_digest_seed

    digest_reference_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        load_tpch_value_digest_seed()


def test_digest_snapshot_missing_or_unreadable_metadata_fails_closed(digest_reference_path: Path) -> None:
    from benchbox.core.expected_results.loader import load_tpch_value_digest_seed

    with pytest.raises(FileNotFoundError):
        load_tpch_value_digest_seed()
    digest_reference_path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError):
        load_tpch_value_digest_seed()


@pytest.mark.parametrize(
    "benchmark_name,platform,scale,query_ids,strict,emit,seeded",
    [
        ("tpch", "duckdb", 1.0, "1,6", True, True, True),
        ("tpch", "duckdb", 1.0, "1,6", True, True, None),
        ("tpch", "duckdb", 1.0, "", True, True, False),
        ("tpch", "duckdb", 0.1, "1,6", True, True, False),
        ("tpcds", "duckdb", 1.0, "1,6", True, True, False),
        ("tpch", "datafusion", 1.0, "1,6", True, True, False),
        ("tpch", "duckdb", 1.0, "1,6", False, True, False),
        ("tpch", "duckdb", 1.0, "1,6", True, False, False),
    ],
)
def test_only_bounded_digest_gate_binds_snapshot_seed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    digest_reference_path: Path,
    benchmark_name: str,
    platform: str,
    scale: float,
    query_ids: str,
    strict: bool,
    emit: bool,
    seeded: bool | None,
) -> None:
    import tests.integration.test_local_platform_benchmark_matrix as matrix

    snapshot_seed = None if seeded is None else 31
    digest_reference_path.write_text(
        json.dumps(
            {"benchmark": "tpch", "scale_factor": 1.0, "reference_seed": snapshot_seed, "digests": {"1": "a", "6": "b"}}
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(matrix.CORRECTNESS_GATE_QUERY_IDS_ENV, query_ids)
    monkeypatch.setenv("BENCHBOX_STRICT_EXPECTED_RESULTS", str(int(strict)))
    monkeypatch.setenv("BENCHBOX_EMIT_RESULT_DIGEST", str(int(emit)))
    monkeypatch.setattr(matrix, "is_platform_available", lambda _: True)
    monkeypatch.setattr(matrix, "_local_sql_case_enabled", lambda *_: True)
    monkeypatch.setattr(matrix, "_select_scale", lambda *_: scale)
    command = []

    def capture_cli(args, **kwargs):
        command.extend(args)
        raise RuntimeError("command captured")

    monkeypatch.setattr(matrix, "run_cli_command", capture_cli)
    with pytest.raises(RuntimeError, match="command captured"):
        matrix.test_local_platform_benchmark_matrix(tmp_path, monkeypatch, platform, benchmark_name)
    assert ("--seed" in command) is bool(seeded)
    if seeded:
        assert command[command.index("--seed") + 1] == "31"


@pytest.mark.parametrize("query_ids,seed", [("1", True), ("11", 31)])
def test_digest_gate_rejects_bad_snapshot_before_cli(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    digest_reference_path: Path,
    query_ids: str,
    seed,
) -> None:
    import tests.integration.test_local_platform_benchmark_matrix as matrix

    digest_reference_path.write_text(
        json.dumps({"benchmark": "tpch", "scale_factor": 1.0, "reference_seed": seed, "digests": {"1": "a"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv(matrix.CORRECTNESS_GATE_QUERY_IDS_ENV, query_ids)
    monkeypatch.setenv("BENCHBOX_STRICT_EXPECTED_RESULTS", "1")
    monkeypatch.setenv("BENCHBOX_EMIT_RESULT_DIGEST", "1")
    monkeypatch.setattr(matrix, "is_platform_available", lambda _: True)
    monkeypatch.setattr(
        matrix, "run_cli_command", lambda *_args, **_kwargs: pytest.fail("invalid snapshot reached CLI")
    )
    with pytest.raises(ValueError):
        matrix.test_local_platform_benchmark_matrix(tmp_path, monkeypatch, "duckdb", "tpch")


@pytest.mark.skipif(
    not (
        Path(__file__).resolve().parents[2] / "_project" / "scripts" / "regenerate_correctness_gate_digests.py"
    ).exists(),
    reason="_project scripts are not in this checkout (release tree)",
)
@pytest.mark.parametrize("seed", [31, None, True])
def test_digest_regeneration_binds_validated_snapshot_seed(
    monkeypatch: pytest.MonkeyPatch, digest_reference_path: Path, seed: object
) -> None:
    import _project.scripts.regenerate_correctness_gate_digests as regeneration

    digest_reference_path.write_text(
        json.dumps({"benchmark": "tpch", "scale_factor": 1.0, "reference_seed": seed}), encoding="utf-8"
    )
    monkeypatch.setenv(regeneration.CORRECTNESS_GATE_QUERY_IDS_ENV, "1,6")
    captured = []

    def capture_gate(work_dir, query_ids, bound_seed):
        captured.append((query_ids, bound_seed))
        raise RuntimeError("gate captured")

    monkeypatch.setattr(regeneration, "_run_gate", capture_gate)
    if seed is None or type(seed) is int:
        with pytest.raises(RuntimeError, match="gate captured"):
            regeneration.main()
        assert captured == [(["1", "6"], seed)]
    else:
        with pytest.raises(ValueError, match="reference_seed must be an integer or null"):
            regeneration.main()
        assert not captured


