from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from benchbox.cli.commands.run import LOAD_FAILURE_MARKER, _emit_load_failure_marker

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_emit_load_failure_marker_is_canonical_json(capsys):
    _emit_load_failure_marker(
        SimpleNamespace(
            validation_details={
                "load_failure": {
                    "table": "lineitem",
                    "rows_attempted": 65536,
                    "result_json": None,
                }
            }
        )
    )

    line = capsys.readouterr().out.strip()
    assert line.startswith(LOAD_FAILURE_MARKER)
    payload = json.loads(line.removeprefix(LOAD_FAILURE_MARKER))
    assert payload == {"result_json": None, "rows_attempted": 65536, "table": "lineitem"}


def test_emit_load_failure_marker_ignores_non_load_failures(capsys):
    _emit_load_failure_marker(SimpleNamespace(validation_details={"error": "query failed"}))
    assert capsys.readouterr().out == ""


