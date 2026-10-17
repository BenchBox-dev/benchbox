from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[2]

GENERATOR_MARKERS = (
    "scripts/generate_query_docs.py",
    "scripts/generate_landing_quickstarts.py --check",
    "scripts/generate_compat_docs.py --check",
    "scripts/validate_visualization_images.py",
)


@pytest.mark.skipif(shutil.which("make") is None, reason="make not installed")
@pytest.mark.parametrize(
    ("target", "build_marker"),
    [
        ("site-build", "npm --prefix website run build"),
        ("site-check", "npm --prefix website run check"),
        ("docs-build", "sphinx-build -b html"),
        ("docs-linkcheck", "sphinx-build -b linkcheck"),
    ],
)
def test_every_generator_runs_before_the_build(target: str, build_marker: str) -> None:
    result = subprocess.run(["make", "-n", target], cwd=REPO_ROOT, capture_output=True, text=True, check=True)
    plan = result.stdout
    build_at = plan.index(build_marker)
    for marker in GENERATOR_MARKERS:
        assert marker in plan[:build_at], f"{marker} must run before {build_marker} in {target}"


def test_sphinx_conf_has_no_generator_hook() -> None:
    conf = (REPO_ROOT / "docs" / "conf.py").read_text(encoding="utf-8")
    assert "config-inited" not in conf
    assert "generate_query_docs" not in conf


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


QUERY_DOC_GENERATORS = ("query-docs", "docs-generate", "generate_query_docs.py")
SPHINX_WORKFLOWS = (
    "docs.yml",
    "nightly-v2.yml",
    "publication-deploy.yml",
    "publication-preview-deploy.yml",
    "site-deploy.yml",
    "site-docs.yml",
)


def _sphinx_jobs() -> list[tuple[str, str, list[str]]]:
    import yaml

    jobs = []
    for name in SPHINX_WORKFLOWS:
        workflow = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"))
        for job_id, job in workflow["jobs"].items():
            runs = [step.get("run", "") for step in job.get("steps", [])]
            if any("sphinx-build" in run for run in runs):
                jobs.append((name, job_id, runs))
    return jobs


def test_every_workflow_sphinx_build_generates_query_docs_first() -> None:
    jobs = _sphinx_jobs()
    assert {name for name, _, _ in jobs} == set(SPHINX_WORKFLOWS)
    for name, job_id, runs in jobs:
        segments = "\n".join(runs).split("sphinx-build")[:-1]
        for index, segment in enumerate(segments):
            assert any(marker in segment for marker in QUERY_DOC_GENERATORS), f"{name}:{job_id} build {index}"
