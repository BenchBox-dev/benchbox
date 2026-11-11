from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github/workflows/validate-submission.yml"


def test_corpus_trust_boundary():
    text = WORKFLOW.read_text()

    data = yaml.safe_load(text)
    on = data[True]
    assert "pull_request_target" in on
    assert "pull_request" not in on

    assert "github.event.pull_request.base.sha" in text

    assert "corpus_permit_rejections" in (REPO_ROOT / "scripts/validate_submission.py").read_text()

    assert (REPO_ROOT / "scripts/publication/validator_parity.py").is_file()


