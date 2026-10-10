from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

pub = importlib.import_module("benchbox.cli.commands.publish")

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _fake_result(compliance_class: str | None = None) -> SimpleNamespace:
    ns = SimpleNamespace(
        benchmark_name="tpcds",
        platform="duckdb",
        scale_factor=0.1,
        total_queries=99,
        duration_seconds=30.0,
    )
    if compliance_class is not None:
        ns.compliance_class = compliance_class
    return ns


def _partial_query_failure_result() -> SimpleNamespace:
    result = _fake_result("official")
    result.total_queries = 2
    result.successful_queries = 1
    result.failed_queries = 1
    result.validation_status = "PARTIAL"
    return result


def test_publish_run_refuses_unofficial_subscale_without_label(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "tpcds_subscale.json"
    src.write_text('{"schema_version": "2.0", "export": {"anonymized": true}}', encoding="utf-8")

    monkeypatch.setattr(
        pub,
        "load_result_file",
        lambda *_a, **_k: (_fake_result("unofficial_subscale"), {}),
    )

    result = CliRunner().invoke(pub.publish_run, [str(src)])

    assert result.exit_code == 1
    assert "refused" in result.output.lower()
    assert "unofficial-research" in result.output


def test_publish_run_refuses_unofficial_nonstandard_without_label(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    src = tmp_path / "tpcds_nonstandard.json"
    src.write_text('{"schema_version": "2.0", "export": {"anonymized": true}}', encoding="utf-8")

    monkeypatch.setattr(
        pub,
        "load_result_file",
        lambda *_a, **_k: (_fake_result("unofficial_nonstandard"), {}),
    )

    result = CliRunner().invoke(pub.publish_run, [str(src)])

    assert result.exit_code == 1
    assert "refused" in result.output.lower()


def test_publish_run_allows_unofficial_with_correct_label(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "tpcds_subscale.json"
    src.write_text('{"schema_version": "2.0", "export": {"anonymized": true}}', encoding="utf-8")

    monkeypatch.setattr(
        pub,
        "load_result_file",
        lambda *_a, **_k: (_fake_result("unofficial_subscale"), {}),
    )

    monkeypatch.setattr(
        pub,
        "BundlePublisher",
        lambda **_kw: SimpleNamespace(
            publish=lambda *_a, **_k: SimpleNamespace(
                success=True, errors=[], reference="file:///test/ref", record=None
            )
        ),
    )
    monkeypatch.setattr(pub, "PublicationStore", lambda: SimpleNamespace())

    result = CliRunner().invoke(pub.publish_run, [str(src), "--label", "unofficial-research"])

    assert "refused" not in result.output.lower()
    assert result.exit_code == 0


def test_publish_run_label_check_is_case_insensitive(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "tpcds_subscale.json"
    src.write_text('{"schema_version": "2.0", "export": {"anonymized": true}}', encoding="utf-8")

    monkeypatch.setattr(
        pub,
        "load_result_file",
        lambda *_a, **_k: (_fake_result("unofficial_subscale"), {}),
    )
    monkeypatch.setattr(
        pub,
        "BundlePublisher",
        lambda **_kw: SimpleNamespace(
            publish=lambda *_a, **_k: SimpleNamespace(
                success=True, errors=[], reference="file:///test/ref", record=None
            )
        ),
    )
    monkeypatch.setattr(pub, "PublicationStore", lambda: SimpleNamespace())

    result = CliRunner().invoke(pub.publish_run, [str(src), "--label", "unofficial-research"])

    assert "refused" not in result.output.lower()
    assert result.exit_code == 0


def test_publish_run_refuses_partial_query_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "tpch_partial.json"
    src.write_text('{"schema_version": "2.0", "export": {"anonymized": true}}', encoding="utf-8")

    monkeypatch.setattr(pub, "load_result_file", lambda *_a, **_k: (_partial_query_failure_result(), {}))
    monkeypatch.setattr(
        pub,
        "BundlePublisher",
        lambda **_kw: (_ for _ in ()).throw(AssertionError("partial results must not publish")),
    )

    result = CliRunner().invoke(pub.publish_run, [str(src)])

    assert result.exit_code == 1
    assert "not a clean pass" in result.output
    assert "1 failed query" in result.output


def test_publish_bundle_refuses_unofficial_without_label(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "tpcds_subscale.json"
    src.write_text('{"schema_version": "2.0", "export": {"anonymized": true}}', encoding="utf-8")

    monkeypatch.setattr(
        pub,
        "load_result_file",
        lambda *_a, **_k: (_fake_result("unofficial_subscale"), {}),
    )

    result = pub.publish_bundle(src, label="maintainer-run")

    assert result is None


def test_publish_bundle_refuses_unofficial_nonstandard_without_label(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    src = tmp_path / "tpcds_nonstandard.json"
    src.write_text('{"schema_version": "2.0", "export": {"anonymized": true}}', encoding="utf-8")

    monkeypatch.setattr(
        pub,
        "load_result_file",
        lambda *_a, **_k: (_fake_result("unofficial_nonstandard"), {}),
    )

    result = pub.publish_bundle(src, label="maintainer-run")

    assert result is None


def test_publish_bundle_allows_unofficial_with_correct_label(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "tpcds_subscale.json"
    src.write_text('{"schema_version": "2.0", "export": {"anonymized": true}}', encoding="utf-8")

    monkeypatch.setattr(
        pub,
        "load_result_file",
        lambda *_a, **_k: (_fake_result("unofficial_subscale"), {}),
    )
    monkeypatch.setattr(
        pub,
        "BundlePublisher",
        lambda **_kw: SimpleNamespace(
            publish=lambda *_a, **_k: SimpleNamespace(success=True, errors=[], reference="file:///ref")
        ),
    )
    monkeypatch.setattr(pub, "PublicationStore", lambda: SimpleNamespace())

    result = pub.publish_bundle(src, label="unofficial-research")

    assert result == "file:///ref"


def test_publish_bundle_official_result_not_blocked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "tpcds_official.json"
    src.write_text('{"schema_version": "2.0", "export": {"anonymized": true}}', encoding="utf-8")

    monkeypatch.setattr(
        pub,
        "load_result_file",
        lambda *_a, **_k: (_fake_result("official"), {}),
    )
    monkeypatch.setattr(
        pub,
        "BundlePublisher",
        lambda **_kw: SimpleNamespace(
            publish=lambda *_a, **_k: SimpleNamespace(success=True, errors=[], reference="file:///ref")
        ),
    )
    monkeypatch.setattr(pub, "PublicationStore", lambda: SimpleNamespace())

    result = pub.publish_bundle(src, label="maintainer-run")

    assert result == "file:///ref"


def test_publish_bundle_refuses_partial_query_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "tpch_partial.json"
    src.write_text('{"schema_version": "2.0", "export": {"anonymized": true}}', encoding="utf-8")

    monkeypatch.setattr(pub, "load_result_file", lambda *_a, **_k: (_partial_query_failure_result(), {}))
    monkeypatch.setattr(
        pub,
        "BundlePublisher",
        lambda **_kw: (_ for _ in ()).throw(AssertionError("partial results must not publish")),
    )

    result = pub.publish_bundle(src, label="maintainer-run")

    assert result is None
