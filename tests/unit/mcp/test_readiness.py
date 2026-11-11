from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from benchbox.mcp.readiness import (
    AUTOMATED_GATES,
    CONFORMANCE_REVISION,
    CURRENT_PROTOCOL_VERSION,
    EXTERNAL_GATES,
    INSPECTOR_INTEGRITY,
    INSPECTOR_REVISION,
    INSPECTOR_VERSION,
    verify_publication_evidence,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _write_evidence(path: Path, *, generated_at: datetime, external_passed: bool = True) -> dict[str, str]:
    path.write_text(
        json.dumps(
            {
                "source_revision": "a" * 40,
                "generated_at": generated_at.isoformat(),
                "protocol_version": CURRENT_PROTOCOL_VERSION,
                "conformance_revision": CONFORMANCE_REVISION,
                "inspector_version": INSPECTOR_VERSION,
                "inspector_revision": INSPECTOR_REVISION,
                "inspector_integrity": INSPECTOR_INTEGRITY,
                "automated": dict.fromkeys(AUTOMATED_GATES, True),
                "external": dict.fromkeys(EXTERNAL_GATES, external_passed),
            }
        ),
        encoding="utf-8",
    )
    return {
        "BENCHBOX_BUILD_SHA": "a" * 40,
        "BENCHBOX_MCP_READINESS_SHA256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def test_current_complete_evidence_passes(tmp_path: Path) -> None:
    path = tmp_path / "readiness.json"
    env = _write_evidence(path, generated_at=datetime.now(timezone.utc))
    assert verify_publication_evidence(path, env).source_revision == "a" * 40


def test_failed_external_gate_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "readiness.json"
    env = _write_evidence(path, generated_at=datetime.now(timezone.utc), external_passed=False)
    with pytest.raises(ValueError, match="external gates"):
        verify_publication_evidence(path, env)


def test_stale_evidence_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "readiness.json"
    env = _write_evidence(path, generated_at=datetime.now(timezone.utc) - timedelta(days=8))
    with pytest.raises(ValueError, match="stale"):
        verify_publication_evidence(path, env)


def test_digest_or_revision_mismatch_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "readiness.json"
    env = _write_evidence(path, generated_at=datetime.now(timezone.utc))
    with pytest.raises(ValueError, match="digest mismatch"):
        verify_publication_evidence(path, {**env, "BENCHBOX_MCP_READINESS_SHA256": "0" * 64})
    with pytest.raises(ValueError, match="BENCHBOX_BUILD_SHA"):
        verify_publication_evidence(path, {**env, "BENCHBOX_BUILD_SHA": "b" * 40})


