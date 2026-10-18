from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]

REPO_ROOT = Path(__file__).resolve().parents[2]

GENERATOR_MARKERS = (
    "scripts/generate_query_docs.py",
    "scripts/generate_landing_quickstarts.py",
    "scripts/generate_compat_docs.py --check",
)


@pytest.mark.skipif(shutil.which("make") is None, reason="make not installed")
def test_docs_generate_runs_every_generator() -> None:
    plan = subprocess.run(["make", "-n", "docs-generate"], cwd=REPO_ROOT, capture_output=True, text=True, check=True)
    for marker in GENERATOR_MARKERS:
        assert marker in plan.stdout


@pytest.mark.skipif(shutil.which("make") is None, reason="make not installed")
def test_docs_linkcheck_generates_query_docs_before_checking() -> None:
    plan = subprocess.run(["make", "-n", "docs-linkcheck"], cwd=REPO_ROOT, capture_output=True, text=True, check=True)
    assert plan.stdout.index("scripts/generate_query_docs.py") < plan.stdout.index(
        "scripts/check_doc_external_links.py"
    )


def test_compat_docs_check_fails_on_seeded_drift(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "generate_compat_docs", REPO_ROOT / "scripts" / "generate_compat_docs.py"
    )
    assert spec is not None and spec.loader is not None
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)

    target = tmp_path / "matrix.md"
    target.write_text("expected\n", encoding="utf-8")
    monkeypatch.setattr(gen, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(gen, "_load_all_rules", lambda: None)
    monkeypatch.setattr(gen, "_render_all", lambda: {target: "expected\n"})
    assert gen.main(["--check"]) == 0
    target.write_text("drifted\n", encoding="utf-8")
    assert gen.main(["--check"]) == 1


def test_nightly_linkcheck_generates_query_docs_first() -> None:
    import yaml

    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/nightly-v2.yml").read_text(encoding="utf-8"))
    runs = [step.get("run", "") for step in workflow["jobs"]["linkcheck"]["steps"]]
    check = next(index for index, run in enumerate(runs) if "check_doc_external_links.py" in run)
    assert any("query-docs" in run for run in runs[:check])


def test_site_inputs_bundle_runs_the_docs_generators() -> None:
    source = (REPO_ROOT / "scripts" / "site_inputs.py").read_text(encoding="utf-8")
    assert '"scripts/generate_query_docs.py"' in source
    assert '"scripts/generate_compat_docs.py"' in source
