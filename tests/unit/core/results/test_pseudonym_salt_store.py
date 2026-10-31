from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from benchbox.core.results import anonymization
from benchbox.core.results.anonymization import (
    PUBLIC_PSEUDONYM_SALT_DOCS_URL,
    PUBLIC_PSEUDONYM_SALT_ENV,
    AnonymizationConfig,
    AnonymizationManager,
    MissingPublicPseudonymSaltError,
    ensure_public_pseudonym_salt,
    public_pseudonym_salt_path,
    require_public_pseudonym_salt,
    resolve_public_pseudonym_salt,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture(autouse=True)
def _no_env_salt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(PUBLIC_PSEUDONYM_SALT_ENV, raising=False)


@pytest.fixture(autouse=True)
def _no_read_retry_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(anonymization, "_SALT_READ_DELAY_SECONDS", 0)


def test_salt_path_lives_under_home_benchbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert public_pseudonym_salt_path() == tmp_path / ".benchbox" / "submission_salt"


def test_ensure_generates_64_hex_and_persists() -> None:
    value, created = ensure_public_pseudonym_salt()
    path = public_pseudonym_salt_path()
    assert created is True
    assert re.fullmatch(r"[0-9a-f]{64}", value)
    assert path.read_text(encoding="utf-8").strip() == value


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits")
def test_generated_file_and_directory_are_private() -> None:
    ensure_public_pseudonym_salt()
    path = public_pseudonym_salt_path()
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700


def test_second_call_reuses_the_stored_salt() -> None:
    first, first_created = ensure_public_pseudonym_salt()
    second, second_created = ensure_public_pseudonym_salt()
    assert (first_created, second_created) == (True, False)
    assert second == first
    assert require_public_pseudonym_salt() == first


def test_no_temporary_files_remain_after_generation() -> None:
    ensure_public_pseudonym_salt()
    assert [p.name for p in public_pseudonym_salt_path().parent.iterdir()] == ["submission_salt"]


def test_precedence_explicit_over_env_over_file_over_generated() -> None:
    stored, _ = ensure_public_pseudonym_salt()
    environ = {PUBLIC_PSEUDONYM_SALT_ENV: "from-env"}
    assert ensure_public_pseudonym_salt(explicit=" from-arg ", environ=environ) == ("from-arg", False)
    assert ensure_public_pseudonym_salt(environ=environ) == ("from-env", False)
    assert ensure_public_pseudonym_salt(environ={}) == (stored, False)
    assert require_public_pseudonym_salt(explicit="from-arg", environ=environ) == "from-arg"
    assert require_public_pseudonym_salt(environ=environ) == "from-env"
    assert require_public_pseudonym_salt(environ={}) == stored


def test_env_override_creates_no_file() -> None:
    assert ensure_public_pseudonym_salt(environ={PUBLIC_PSEUDONYM_SALT_ENV: "override"}) == ("override", False)
    assert not public_pseudonym_salt_path().exists()
    assert not public_pseudonym_salt_path().parent.exists()


def test_existing_file_accepts_any_non_empty_content_after_strip() -> None:
    path = public_pseudonym_salt_path()
    path.parent.mkdir(parents=True)
    path.write_text("  my own salt value \n", encoding="utf-8")
    assert ensure_public_pseudonym_salt() == ("my own salt value", False)
    assert require_public_pseudonym_salt() == "my own salt value"


def test_require_without_env_or_file_raises_naming_path_override_and_docs_and_creates_nothing() -> None:
    with pytest.raises(MissingPublicPseudonymSaltError) as raised:
        require_public_pseudonym_salt(environ={})
    message = str(raised.value)
    assert str(public_pseudonym_salt_path()) in message
    assert PUBLIC_PSEUDONYM_SALT_ENV in message
    assert PUBLIC_PSEUDONYM_SALT_DOCS_URL in message
    assert "https://benchbox.dev/docs/contributing-results.html" in message
    assert not public_pseudonym_salt_path().exists()


def test_resolve_ignores_a_valid_salt_file() -> None:
    ensure_public_pseudonym_salt()
    assert resolve_public_pseudonym_salt(environ={}) is None
    assert AnonymizationConfig.from_public_environ(environ={}).machine_id_salt is None


def test_corrupt_salt_file_does_not_break_soft_resolution() -> None:
    path = public_pseudonym_salt_path()
    path.mkdir(parents=True)
    assert resolve_public_pseudonym_salt(environ={}) is None
    assert AnonymizationConfig.from_public_environ(environ={}).machine_id_salt is None


def test_empty_file_raises_with_delete_instruction_and_is_never_regenerated() -> None:
    path = public_pseudonym_salt_path()
    path.parent.mkdir(parents=True)
    path.write_text("  \n", encoding="utf-8")
    for call in (ensure_public_pseudonym_salt, require_public_pseudonym_salt):
        with pytest.raises(MissingPublicPseudonymSaltError) as raised:
            call()
        assert str(path) in str(raised.value)
        assert "empty" in str(raised.value)
        assert "delete" in str(raised.value)
        assert PUBLIC_PSEUDONYM_SALT_ENV in str(raised.value)
    assert path.read_text(encoding="utf-8") == "  \n"


def test_empty_file_is_reread_a_bounded_number_of_times(monkeypatch: pytest.MonkeyPatch) -> None:
    path = public_pseudonym_salt_path()
    path.parent.mkdir(parents=True)
    path.write_text("", encoding="utf-8")
    sleeps: list[float] = []
    monkeypatch.setattr(anonymization.time, "sleep", sleeps.append)
    with pytest.raises(MissingPublicPseudonymSaltError):
        ensure_public_pseudonym_salt()
    assert len(sleeps) == anonymization._SALT_READ_ATTEMPTS - 1


def test_empty_file_that_fills_in_during_reread_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    path = public_pseudonym_salt_path()
    path.parent.mkdir(parents=True)
    path.write_text("", encoding="utf-8")
    monkeypatch.setattr(anonymization.time, "sleep", lambda _seconds: path.write_text("late-winner", encoding="utf-8"))
    assert ensure_public_pseudonym_salt() == ("late-winner", False)


def test_unreadable_salt_path_raises_naming_path_and_never_regenerates() -> None:
    path = public_pseudonym_salt_path()
    path.mkdir(parents=True)
    with pytest.raises(MissingPublicPseudonymSaltError) as raised:
        ensure_public_pseudonym_salt()
    assert str(path) in str(raised.value)
    assert PUBLIC_PSEUDONYM_SALT_ENV in str(raised.value)
    assert path.is_dir()


def test_undecodable_salt_file_error_does_not_echo_its_bytes() -> None:
    path = public_pseudonym_salt_path()
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\xff\xfe-secret-bytes")
    with pytest.raises(MissingPublicPseudonymSaltError) as raised:
        ensure_public_pseudonym_salt()
    assert "secret-bytes" not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__ is True


def test_link_failure_falls_back_to_exclusive_create(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_links(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("hard links unsupported")

    monkeypatch.setattr(anonymization.os, "link", no_links)
    value, created = ensure_public_pseudonym_salt()
    path = public_pseudonym_salt_path()
    assert created is True
    assert re.fullmatch(r"[0-9a-f]{64}", value)
    assert path.read_text(encoding="utf-8").strip() == value
    assert [p.name for p in path.parent.iterdir()] == ["submission_salt"]
    assert ensure_public_pseudonym_salt() == (value, False)


def test_lost_race_returns_the_winner_without_overwriting(monkeypatch: pytest.MonkeyPatch) -> None:
    path = public_pseudonym_salt_path()
    real_link = os.link

    def winner_first(src: str, dst: str) -> None:
        Path(dst).write_text("winner-salt", encoding="utf-8")
        real_link(src, dst)

    monkeypatch.setattr(anonymization.os, "link", winner_first)
    assert ensure_public_pseudonym_salt() == ("winner-salt", False)
    assert path.read_text(encoding="utf-8") == "winner-salt"
    assert [p.name for p in path.parent.iterdir()] == ["submission_salt"]


def test_unwritable_directory_raises_naming_path_and_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    blocker = tmp_path / "blocked-home"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("HOME", str(blocker))
    monkeypatch.setenv("USERPROFILE", str(blocker))
    with pytest.raises(MissingPublicPseudonymSaltError) as raised:
        ensure_public_pseudonym_salt()
    assert str(public_pseudonym_salt_path()) in str(raised.value)
    assert PUBLIC_PSEUDONYM_SALT_ENV in str(raised.value)


_RACE_SCRIPT = (
    "import hashlib\n"
    "from benchbox.core.results.anonymization import ensure_public_pseudonym_salt\n"
    "value, created = ensure_public_pseudonym_salt()\n"
    "print(hashlib.sha256(value.encode()).hexdigest(), int(created))\n"
)


def test_concurrent_generation_converges_on_one_value() -> None:
    environment = {key: value for key, value in os.environ.items() if key != PUBLIC_PSEUDONYM_SALT_ENV}
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", _RACE_SCRIPT],
            cwd=REPO_ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(6)
    ]
    outputs = [process.communicate(timeout=120) for process in processes]
    assert all(process.returncode == 0 for process in processes), [err for _, err in outputs]
    rows = [out.split() for out, _ in outputs]
    stored = public_pseudonym_salt_path().read_text(encoding="utf-8").strip()
    assert {row[0] for row in rows} == {hashlib.sha256(stored.encode()).hexdigest()}
    assert sum(int(row[1]) for row in rows) == 1


def test_config_repr_hides_the_salt() -> None:
    config = AnonymizationConfig(machine_id_salt="repr-secret-salt")
    assert "repr-secret-salt" not in repr(config)


def test_pipeline_and_backfill_managers_ignore_env_and_file(monkeypatch: pytest.MonkeyPatch) -> None:
    ensure_public_pseudonym_salt()
    monkeypatch.setenv(PUBLIC_PSEUDONYM_SALT_ENV, "ignored-by-public-pipeline")
    default_manager = AnonymizationManager()
    assert default_manager.config.machine_id_salt is None
    unsalted = AnonymizationManager(AnonymizationConfig())._hash_public_identifier("host.example.invalid", "host")
    assert default_manager._hash_public_identifier("host.example.invalid", "host") == unsalted


@pytest.mark.parametrize(
    "relative_path",
    [
        "_project/scripts/explorer_pipeline/pipeline.py",
        "_project/scripts/results_explorer_cpu_attestation_backfill.py",
        ".github/workflows/sync-results-data-to-published.yml",
    ],
)
def test_public_pipeline_sources_stay_unsalted(relative_path: str) -> None:
    text = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
    assert "AnonymizationManager()" in text
    for forbidden in (
        "ensure_public_pseudonym_salt",
        "require_public_pseudonym_salt",
        "resolve_public_pseudonym_salt",
        "from_public_environ",
        "submission_salt",
        PUBLIC_PSEUDONYM_SALT_ENV,
    ):
        assert forbidden not in text
