from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

import pytest

from tests.integration._cli_e2e_utils import (
    cli_subprocess_environment,
    isolated_cli_home,
    run_cli_command,
    writes_pseudonym_salt,
)

pytestmark = [pytest.mark.integration, pytest.mark.fast]


def test_default_cli_home_is_an_isolated_temporary_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    with isolated_cli_home() as home:
        assert home.parent == tmp_path
        assert home != Path.home()
        environment = cli_subprocess_environment(home=home)
        assert Path(environment["HOME"]) == home
        assert Path(environment["USERPROFILE"]) == home
    assert not home.exists()


@pytest.mark.parametrize("command", ["export", "submit", "publish"])
def test_publication_commands_get_an_isolated_home(command: str) -> None:
    assert writes_pseudonym_salt([command, "--help"])


@pytest.mark.parametrize("command", ["run", "platforms", "results"])
def test_other_commands_keep_the_inherited_home(command: str, monkeypatch: pytest.MonkeyPatch) -> None:
    assert not writes_pseudonym_salt([command, "--help"])
    monkeypatch.setenv("HOME", "/inherited/home")
    assert cli_subprocess_environment(home=None)["HOME"] == "/inherited/home"


def test_explicit_cli_home_overrides_inherited_home(tmp_path: Path) -> None:
    environment = cli_subprocess_environment(home=tmp_path / "home", cwd=tmp_path, env={"EXTRA": "1"})
    assert environment["HOME"] == str(tmp_path / "home")
    assert environment["EXTRA"] == "1"


def test_anonymized_export_subprocess_writes_its_salt_only_under_the_given_home(tmp_path: Path) -> None:
    from tests.integration.test_export_cli import make_v2_result_data

    home = tmp_path / "home"
    home.mkdir()
    result_file = tmp_path / "result.json"
    result_file.write_text(json.dumps(make_v2_result_data()), encoding="utf-8")
    output_dir = tmp_path / "exports"

    result = run_cli_command(
        ["export", str(result_file), "--format", "json", "--output-dir", str(output_dir), "--force"],
        cwd=tmp_path,
        env={"BENCHBOX_MACHINE_ID_SALT": ""},
        home=home,
    )

    assert result.returncode == 0, result.stderr
    salt = (home / ".benchbox" / "submission_salt").read_text(encoding="utf-8").strip()
    assert re.fullmatch(r"[0-9a-f]{64}", salt)
    assert salt not in result.stdout + result.stderr
    assert salt not in "".join(path.read_text(encoding="utf-8") for path in output_dir.glob("*.json"))
