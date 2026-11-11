from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

WORKFLOW = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "todo-state-validate.yml"


def test_workflow_validates_the_git_state_without_database_credentials() -> None:
    text = WORKFLOW.read_text()

    assert "tractable validate" in text
    assert "--config .tractable/config.json" in text
    assert "uv sync --project _project/scripts --locked" in text
    for forbidden in (
        "TODO_DB_URL",
        "TODO_DB_AUTH_TOKEN",
        "TODO_DB_RO_AUTH_TOKEN",
        "libsql://",
        "turso",
        "todo-db export",
        "restore.sqlite",
    ):
        assert forbidden not in text


