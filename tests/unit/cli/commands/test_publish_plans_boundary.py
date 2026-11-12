from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

import benchbox.cli.commands.publish as publish_mod
from benchbox.cli.commands.publish import publish_bundle
from benchbox.core.results.exporter import ResultExporter
from benchbox.validation.bundle import find_public_path_leaks
from tests.fixtures.result_dict_fixtures import make_benchmark_results

pytestmark = [pytest.mark.unit, pytest.mark.fast]

LEAK_TEXT = "Scan /Users/synthetic-user/data/lineitem.parquet"
LEAK_RAW = "EXPLAIN reading /home/synthetic-user/warehouse/orders.parquet"
LEAK_ERROR = "failed opening D:\\synthetic\\data\\part.tbl"
SAFE_ERROR = "relation main.part does not exist"


def _plans_payload() -> dict:
    return {
        "queries": {
            "Q1": {
                "plan": {
                    "query_id": "Q1",
                    "platform": "duckdb",
                    "plan_text": LEAK_TEXT,
                    "raw_explain_output": None,
                    "logical_root": None,
                    "plan_fingerprint": "f" * 16,
                },
                "plan_fingerprint": "f" * 16,
            },
            "Q2": {"plan": LEAK_RAW},
        },
        "errors": {"Q3": SAFE_ERROR},
    }


def _source(tmp_path: Path, plans: dict | None, test_type: str | None = None) -> Path:
    result = make_benchmark_results(
        benchmark_name="TPC-H",
        platform="duckdb",
        execution_id="boundary-exec",
        timestamp=datetime(2026, 10, 9, 12, 0, 0),
        duration_seconds=3.0,
        total_queries=1,
        successful_queries=1,
        query_results=[
            {
                "query_id": "Q1",
                "execution_time": 1.0,
                "status": "SUCCESS",
                "rows_returned": 4,
                **({"test_type": test_type, "stream_id": 0} if test_type else {}),
            }
        ],
        validation_status="PASSED",
        validation_details={},
    )
    source = ResultExporter(output_dir=tmp_path / "local", anonymize=True).export_result(result, ["json"])["json"]
    if plans is not None:
        (source.parent / (source.stem + ".plans.json")).write_text(json.dumps(plans), encoding="utf-8")
    return source


@pytest.fixture(autouse=True)
def _salt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BENCHBOX_MACHINE_ID_SALT", "boundary-test-salt")


def test_legacy_plans_companion_is_sanitized_before_publication(tmp_path: Path) -> None:
    source = _source(tmp_path, _plans_payload())
    original = (source.parent / (source.stem + ".plans.json")).read_bytes()
    target = tmp_path / "published"

    assert publish_bundle(source, target=str(target), label="local", quiet=True) is not None

    published = target / (source.stem + ".plans.json")
    text = published.read_text(encoding="utf-8")
    assert "synthetic" not in text
    payload = json.loads(text)
    assert find_public_path_leaks(payload) == []
    assert payload["queries"]["Q1"]["plan_fingerprint"] == "f" * 16
    assert payload["queries"]["Q1"]["plan"]["plan_fingerprint"] == "f" * 16
    assert set(payload["queries"]) == {"Q1", "Q2"}
    assert set(payload["errors"]) == {"Q3"}
    assert (source.parent / (source.stem + ".plans.json")).read_bytes() == original


def test_safe_plans_companion_passes_through_byte_identical(tmp_path: Path) -> None:
    safe = {
        "queries": {"Q1": {"plan": {"query_id": "Q1", "plan_text": "Scan main.lineitem https://example.org/x"}}},
        "errors": {},
    }
    source = _source(tmp_path, safe)
    target = tmp_path / "published"

    assert publish_bundle(source, target=str(target), label="local", quiet=True) is not None

    name = source.stem + ".plans.json"
    assert (target / name).read_bytes() == (source.parent / name).read_bytes()


def test_missing_companion_publishes_main_only(tmp_path: Path) -> None:
    source = _source(tmp_path, None)
    target = tmp_path / "published"

    assert publish_bundle(source, target=str(target), label="local", quiet=True) is not None
    assert [p.name for p in target.glob("*.json")] == [source.name]


def test_residual_leak_publishes_nothing_and_diagnostic_is_redacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source(tmp_path, _plans_payload())
    monkeypatch.setattr(publish_mod, "anonymize_plans_payload", lambda payload, roots=(): payload)
    printed: list[str] = []
    monkeypatch.setattr(publish_mod.console, "print", lambda *a, **_k: printed.append(str(a[0])))
    target = tmp_path / "published"

    assert publish_bundle(source, target=str(target), label="local", quiet=True) is None

    assert not target.exists() or list(target.iterdir()) == []
    output = "\n".join(printed)
    assert ".plans.json" in output
    assert "queries.Q1.plan.plan_text" in output
    assert "synthetic" not in output


def test_errors_only_leak_is_redacted_before_publication(tmp_path: Path) -> None:
    plans = _plans_payload()
    plans["errors"]["Q3"] = LEAK_ERROR
    source = _source(tmp_path, plans)
    target = tmp_path / "published"

    assert publish_bundle(source, target=str(target), label="local", quiet=True) is not None

    published = json.loads((target / (source.stem + ".plans.json")).read_text(encoding="utf-8"))
    assert "synthetic" not in json.dumps(published)
    assert "<redacted>" in published["errors"]["Q3"]
    assert find_public_path_leaks(published) == []


def test_tuning_companion_with_path_leak_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _source(tmp_path, {"queries": {}, "errors": {}})
    (source.parent / (source.stem + ".tuning.json")).write_text(
        json.dumps({"notes": "/Users/synthetic-user/tuning.sql"}), encoding="utf-8"
    )
    printed: list[str] = []
    monkeypatch.setattr(publish_mod.console, "print", lambda *a, **_k: printed.append(str(a[0])))
    target = tmp_path / "published"

    assert publish_bundle(source, target=str(target), label="local", quiet=True) is None

    assert not target.exists() or list(target.iterdir()) == []
    assert ".tuning.json" in "\n".join(printed)
    assert "synthetic" not in "\n".join(printed)


def test_override_companion_with_path_leak_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _source(tmp_path, {"queries": {}, "errors": {}})
    (source.parent / (source.stem + ".override.json")).write_text(
        json.dumps({"reason": "see /Users/synthetic-user/notes.md"}), encoding="utf-8"
    )
    printed: list[str] = []
    monkeypatch.setattr(publish_mod.console, "print", lambda *a, **_k: printed.append(str(a[0])))
    target = tmp_path / "published"

    assert publish_bundle(source, target=str(target), label="local", quiet=True) is None

    assert not target.exists() or list(target.iterdir()) == []
    assert "synthetic" not in "\n".join(printed)


def test_source_directory_path_in_redacted_output_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _source(tmp_path, {"queries": {}, "errors": {}})
    leaked = {"export": {"anonymized": True}, "note": f"{source.parent.resolve()}/run"}
    redacted = tmp_path / "redacted.json"
    redacted.write_text(json.dumps(leaked), encoding="utf-8")
    monkeypatch.setattr(publish_mod.console, "print", lambda *a, **_k: None)

    assert not publish_mod._redacted_output_is_clean(
        redacted, roots=publish_mod._local_roots(source), require_anonymized_marker=True
    )


def _keyed_plans_payload() -> dict:
    variants = [
        {"plan_fingerprint": "a" * 16, "iterations": [0]},
        {"plan_fingerprint": "b" * 16, "iterations": [1]},
    ]
    entry = {
        "plan": {
            "query_id": "Q1",
            "platform": "duckdb",
            "plan_text": LEAK_TEXT,
            "raw_explain_output": LEAK_RAW,
            "logical_root": None,
            "plan_fingerprint": "a" * 16,
        },
        "plan_fingerprint": "a" * 16,
        "fingerprint_varied": True,
        "fingerprint_variants": variants,
    }
    return {
        "plans_captured": 1,
        "capture_failures": 0,
        "queries": {"Q1#0:power": entry},
    }


def test_stream_keyed_plans_companion_keeps_keys_and_variation_fields(tmp_path: Path) -> None:
    plans = _keyed_plans_payload()
    source = _source(tmp_path, plans, "power")
    target = tmp_path / "published"

    assert publish_bundle(source, target=str(target), label="local", quiet=True) is not None

    published = json.loads((target / (source.stem + ".plans.json")).read_text(encoding="utf-8"))
    assert set(published["queries"]) == {"Q1#0:power"}
    entry = published["queries"]["Q1#0:power"]
    assert entry["fingerprint_varied"] is True
    assert entry["fingerprint_variants"] == plans["queries"]["Q1#0:power"]["fingerprint_variants"]


def test_stream_keyed_plans_companion_is_still_sanitized(tmp_path: Path) -> None:
    source = _source(tmp_path, _keyed_plans_payload(), "power")
    target = tmp_path / "published"

    assert publish_bundle(source, target=str(target), label="local", quiet=True) is not None

    text = (target / (source.stem + ".plans.json")).read_text(encoding="utf-8")
    assert "synthetic" not in text
    entry = json.loads(text)["queries"]["Q1#0:power"]
    assert entry["plan"]["raw_explain_output"] is None
    assert find_public_path_leaks(json.loads(text)) == []


def test_stream_keyed_plans_companion_with_unremovable_leak_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source(tmp_path, _keyed_plans_payload(), "power")
    monkeypatch.setattr(publish_mod, "anonymize_plans_payload", lambda payload, roots=(): payload)
    target = tmp_path / "published"

    assert publish_bundle(source, target=str(target), label="local", quiet=True) is None
    assert not target.exists() or list(target.iterdir()) == []
