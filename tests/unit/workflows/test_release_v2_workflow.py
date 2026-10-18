from __future__ import annotations

from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "release-v2.yml"
LEGACY = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "release.yml"


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_workflow_exists_and_is_dispatch_only_during_the_overlap():
    text = _text()
    assert "workflow_dispatch:" in text
    assert "push:" not in text
    assert "tags:" not in text


def test_workflow_admits_and_stages_without_rebuilding():
    text = _text()
    assert "release_artifact_consumer.py admit" in text
    assert "release_admitted_dist.py" in text and "--stage staged" in text
    assert "uv build" not in text
    assert "python -m build" not in text


def test_workflow_requires_an_annotated_tag_and_rechecks_it_after_approval():
    text = _text()
    assert "git cat-file -t" in text
    publish = text.split("publish:", 1)[1].split("github-release:", 1)[0]
    assert "release_tag_recheck.py" in publish
    assert publish.index("environment:") < publish.index("release_tag_recheck.py")


def test_workflow_routes_prereleases_to_test_pypi_only():
    text = _text()
    assert "target=test-pypi" in text
    assert "target == 'test-pypi'" in text
    assert "target == 'pypi'" in text


def test_workflow_covers_the_twelve_cell_install_matrix():
    text = _text()
    for token in ("ubuntu-latest", "macos-latest", "windows-latest", "'3.11'", "'3.12'", "'3.13'", "'3.14'"):
        assert token in text


def test_workflow_gates_on_drift_blockers_release_check_and_uat():
    text = _text()
    assert "ruleset_drift_check.py" in text
    assert "unset RELEASE_READINESS_OVERRIDE_SHA RELEASE_READINESS_OVERRIDE_REASON" in text
    assert "release_blocker_check.py" in text
    assert "make release-check" in text
    assert "release-gate-" in text
    assert "dist-digest.txt" in text
    assert "site-packages" in text
    assert "mv benchbox benchbox.checkout" in text


def test_workflow_attests_the_staged_distributions():
    text = _text()
    assert "attest-build-provenance" in text
    assert "attestations: write" in text
    assert "subject-path:" in text and "staged/*.whl" in text


def test_legacy_workflow_keeps_its_tag_trigger_during_the_overlap():
    text = LEGACY.read_text(encoding="utf-8")
    assert "tags: ['v*']" in text
