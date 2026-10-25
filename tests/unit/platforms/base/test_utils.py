from __future__ import annotations

import sys

import pytest

from benchbox.platforms.base import is_non_interactive

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _FakeStdin:
    def __init__(self, is_tty: bool) -> None:
        self._is_tty = is_tty

    def isatty(self) -> bool:
        return self._is_tty


def _remove_pytest():
    return sys.modules.pop("pytest", None)


def _restore_pytest(module):
    if module is not None:
        sys.modules["pytest"] = module


def test_env_flag_forces_non_interactive(monkeypatch):
    saved_pytest = _remove_pytest()
    monkeypatch.setenv("BENCHBOX_NON_INTERACTIVE", "true")
    monkeypatch.setattr(sys, "stdin", _FakeStdin(is_tty=True), raising=False)

    try:
        assert is_non_interactive() is True
    finally:
        monkeypatch.delenv("BENCHBOX_NON_INTERACTIVE", raising=False)
        _restore_pytest(saved_pytest)


def test_tty_detection_without_env(monkeypatch):
    saved_pytest = _remove_pytest()
    monkeypatch.delenv("BENCHBOX_NON_INTERACTIVE", raising=False)
    monkeypatch.setattr(sys, "stdin", _FakeStdin(is_tty=False), raising=False)

    try:
        assert is_non_interactive() is True
    finally:
        _restore_pytest(saved_pytest)


def test_interactive_path(monkeypatch):
    saved_pytest = _remove_pytest()
    monkeypatch.delenv("BENCHBOX_NON_INTERACTIVE", raising=False)

    for ci_var in ["CI", "GITHUB_ACTIONS", "GITLAB_CI", "TRAVIS", "JENKINS_URL", "BUILDKITE", "CIRCLECI"]:
        monkeypatch.delenv(ci_var, raising=False)
    monkeypatch.setattr(sys, "stdin", _FakeStdin(is_tty=True), raising=False)

    try:
        assert is_non_interactive() is False
    finally:
        _restore_pytest(saved_pytest)
