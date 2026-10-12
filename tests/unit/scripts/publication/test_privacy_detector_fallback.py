from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_privacy_detector_unavailable_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins
    import importlib
    import sys

    from scripts.publication import check_artifact_privacy as privacy_mod

    real_import = builtins.__import__

    def _blocked_import(name, *args, **kwargs):
        if name == "benchbox.validation.bundle" or name.startswith("benchbox.validation.bundle."):
            raise ImportError("blocked for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "benchbox.validation.bundle", raising=False)
    monkeypatch.setattr(builtins, "__import__", _blocked_import)
    try:
        importlib.reload(privacy_mod)
        findings = privacy_mod.unanonymized_tuning_findings({"platform": {"tuning": {}}})

        assert len(findings) > 0
        assert any("benchbox.validation.bundle" in finding for finding in findings)
    finally:
        monkeypatch.undo()
        importlib.reload(privacy_mod)
