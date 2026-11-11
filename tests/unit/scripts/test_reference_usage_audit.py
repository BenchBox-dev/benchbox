from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_script():
    name = "reference_usage_audit"
    path = REPO_ROOT / "_project" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


reference_usage_audit = _load_script()


def _write_transcript(transcripts_dir: Path, record: dict[str, object]) -> Path:
    transcript = transcripts_dir / "session.jsonl"
    transcript.write_text(json.dumps(record) + "\n", encoding="utf-8")
    return transcript


def test_collect_hits_scans_standard_text_blocks(tmp_path: Path) -> None:
    transcripts_dir = tmp_path / "transcripts"
    transcripts_dir.mkdir()
    _write_transcript(
        transcripts_dir,
        {
            "timestamp": "2026-05-05T12:00:00Z",
            "message": {
                "content": [
                    {
                        "type": "text",
                        "text": "Please inspect docs/references/compare.md before pruning.",
                    }
                ]
            },
        },
    )

    hits, parse_errors = reference_usage_audit.collect_hits(
        transcripts_dir,
        ("docs/references/compare.md",),
        since=None,
        exclude_session=None,
    )

    assert parse_errors == 0
    assert [(hit.reference, hit.category, hit.line) for hit in hits] == [
        ("docs/references/compare.md", "message_content", 1)
    ]


def test_collect_hits_preserves_tool_result_content_classification(tmp_path: Path) -> None:
    transcripts_dir = tmp_path / "transcripts"
    transcripts_dir.mkdir()
    _write_transcript(
        transcripts_dir,
        {
            "timestamp": "2026-05-05T12:00:00Z",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "content": "Found references/testing-patterns.md in the result.",
                    }
                ]
            },
        },
    )

    hits, parse_errors = reference_usage_audit.collect_hits(
        transcripts_dir,
        ("references/testing-patterns.md",),
        since=None,
        exclude_session=None,
    )

    assert parse_errors == 0
    assert [(hit.reference, hit.category, hit.line) for hit in hits] == [
        ("references/testing-patterns.md", "tool_result_content", 1)
    ]


