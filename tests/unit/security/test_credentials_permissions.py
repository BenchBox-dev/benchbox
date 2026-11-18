import logging
import os
import stat
from pathlib import Path

import pytest
import yaml

from benchbox.security.credentials import CredentialManager, CredentialStatus, require_private_mode

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
    pytest.mark.skipif(os.name != "posix", reason="POSIX file modes"),
]


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _saved_manager(path: Path) -> CredentialManager:
    manager = CredentialManager(credentials_path=path)
    manager.set_platform_credentials("snowflake", {"password": "hunter2"}, CredentialStatus.VALID)
    return manager


@pytest.mark.parametrize("umask", [0o000, 0o022])
def test_directory_and_file_are_private_from_creation(tmp_path: Path, umask: int) -> None:
    target = tmp_path / ".benchbox" / "credentials.yaml"
    previous = os.umask(umask)
    try:
        _saved_manager(target).save_credentials()
    finally:
        os.umask(previous)

    assert _mode(target.parent) == 0o700
    assert _mode(target) == 0o600


def test_file_is_never_visible_at_a_wider_mode(tmp_path: Path) -> None:
    target = tmp_path / "credentials.yaml"
    seen: list[int] = []

    class Recording(CredentialManager):
        def _install(self, temporary: str) -> None:
            seen.append(_mode(Path(temporary)))
            super()._install(temporary)

    manager = Recording(credentials_path=target)
    manager.set_platform_credentials("snowflake", {"password": "hunter2"}, CredentialStatus.VALID)
    previous = os.umask(0o000)
    try:
        manager.save_credentials()
    finally:
        os.umask(previous)

    assert seen == [0o600]
    assert _mode(target) == 0o600


def test_wider_existing_file_is_tightened_with_one_warning(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    target = tmp_path / "credentials.yaml"
    target.write_text("snowflake:\n  password: hunter2\n", encoding="utf-8")
    target.chmod(0o644)

    manager = CredentialManager(credentials_path=target)
    with caplog.at_level(logging.WARNING, logger="benchbox.security.credentials"):
        manager.save_credentials()

    assert _mode(target) == 0o600
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "0644" in warnings[0].getMessage()


def test_private_existing_file_saves_without_warning(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    target = tmp_path / "credentials.yaml"
    manager = _saved_manager(target)
    manager.save_credentials()

    with caplog.at_level(logging.WARNING, logger="benchbox.security.credentials"):
        manager.save_credentials()

    assert caplog.records == []


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o666])
def test_a_wider_mode_is_an_error(tmp_path: Path, mode: int) -> None:
    with pytest.raises(PermissionError, match=f"{mode:04o}"):
        require_private_mode(mode, tmp_path)


@pytest.mark.parametrize("mode", [0o600, 0o400, 0o700])
def test_an_owner_only_mode_is_accepted(tmp_path: Path, mode: int) -> None:
    require_private_mode(mode, tmp_path)


def test_temporary_file_is_created_private_under_a_permissive_umask(tmp_path: Path) -> None:
    manager = CredentialManager(credentials_path=tmp_path / "credentials.yaml")
    previous = os.umask(0o000)
    try:
        descriptor, name = manager._create_private_temporary(tmp_path)
    finally:
        os.umask(previous)
    os.close(descriptor)

    assert _mode(Path(name)) == 0o600


def test_write_failure_propagates_and_removes_the_temporary_file(tmp_path: Path) -> None:
    target = tmp_path / "credentials.yaml"
    manager = _saved_manager(target)
    target.mkdir()
    (target / "occupied").write_text("x", encoding="utf-8")

    with pytest.raises(OSError):
        manager.save_credentials()

    assert [path.name for path in tmp_path.iterdir()] == ["credentials.yaml"]


def test_environment_references_survive_a_save(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "credentials.yaml"
    target.write_text("snowflake:\n  username: me\n  password: ${SNOWFLAKE_PASSWORD}\n", encoding="utf-8")
    monkeypatch.setenv("SNOWFLAKE_PASSWORD", "from-environment")

    manager = CredentialManager(credentials_path=target)
    assert manager.get_platform_credentials("snowflake")["password"] == "from-environment"
    manager.update_validation_status("snowflake", CredentialStatus.VALID)
    manager.save_credentials()

    saved = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert saved["snowflake"]["password"] == "${SNOWFLAKE_PASSWORD}"
    assert "from-environment" not in target.read_text(encoding="utf-8")


def test_changed_value_replaces_its_reference(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "credentials.yaml"
    target.write_text("snowflake:\n  password: ${SNOWFLAKE_PASSWORD}\n", encoding="utf-8")
    monkeypatch.setenv("SNOWFLAKE_PASSWORD", "from-environment")

    manager = CredentialManager(credentials_path=target)
    manager.credentials["snowflake"]["password"] = "typed-in"
    manager.save_credentials()

    assert yaml.safe_load(target.read_text(encoding="utf-8"))["snowflake"]["password"] == "typed-in"
