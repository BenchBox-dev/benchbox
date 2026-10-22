from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.fast]

WORKFLOWS = Path(__file__).resolve().parents[3] / ".github" / "workflows"
EVIDENCE_PREFIXES = ("t2-medium-", "release-medium-", "t2-coverage-map-")
UPLOAD_ACTION = "actions/upload-artifact@"


def _evidence_uploads() -> list[tuple[str, str, dict]]:
    uploads = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job_name, job in (workflow.get("jobs") or {}).items():
            for step in job.get("steps") or []:
                with_block = step.get("with") or {}
                name = str(with_block.get("name", ""))
                if str(step.get("uses", "")).startswith(UPLOAD_ACTION) and name.startswith(EVIDENCE_PREFIXES):
                    uploads.append((path.name, f"{job_name}: {name}", with_block))
    return uploads


def test_the_evidence_uploads_are_found() -> None:
    assert {filename for filename, _, _ in _evidence_uploads()} == {
        "ci.yml",
        "test.yml",
        "trunk-coverage.yml",
        "trunk.yml",
    }


@pytest.mark.parametrize(("filename", "upload", "with_block"), _evidence_uploads())
def test_a_rerun_replaces_the_evidence_it_supersedes(filename: str, upload: str, with_block: dict) -> None:
    assert with_block.get("overwrite") is True, (
        f"{filename} {upload}: a rerun attempt would leave an older artifact of the same name beside the new one, "
        "and the download keeps whichever lands last"
    )
