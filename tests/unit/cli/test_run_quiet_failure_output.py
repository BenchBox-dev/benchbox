from __future__ import annotations

import pytest
from click.testing import CliRunner

from benchbox.cli.app import cli
from benchbox.utils import printing

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.fixture(autouse=True)
def fresh_quiet_sink(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(printing, "_SINK_CONSOLE", None)
    monkeypatch.setattr(printing, "_QUIET", False)


def test_a_failed_quiet_run_prints_its_suppressed_error_to_stderr(tmp_path) -> None:
    result = CliRunner().invoke(
        cli,
        [
            "run",
            "--platform",
            "duckdb",
            "--benchmark",
            "tpch",
            "--scale",
            "0.01",
            "--non-interactive",
            "--quiet",
            "--platform-option",
            "nonexistent_option=1",
            "--output",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 1
    assert "Unknown platform option 'nonexistent_option'" in result.output


def test_replay_prints_only_the_tail_of_what_quiet_mode_suppressed(capsys: pytest.CaptureFixture[str]) -> None:
    printing.set_quiet(True)
    printing.get_console().print("early line " * 10)
    printing.get_console().print("final error line")

    printing.replay_quiet_output(max_chars=40)

    err = capsys.readouterr().err
    assert "final error line" in err
    assert len(err) <= 41


def test_replay_prints_nothing_when_quiet_mode_suppressed_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    printing.replay_quiet_output()
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize(
    ("raised", "replayed"),
    [
        (SystemExit(1), True),
        (SystemExit(0), False),
        (SystemExit(None), False),
        (RuntimeError("boom"), True),
        (KeyboardInterrupt(), False),
    ],
)
def test_only_failing_exits_replay_the_suppressed_output(
    capsys: pytest.CaptureFixture[str], raised: BaseException, replayed: bool
) -> None:
    printing.set_quiet(True)
    printing.get_console().print("suppressed detail")

    with pytest.raises(type(raised)):
        with printing.replay_quiet_output_on_failure(True):
            raise raised

    assert ("suppressed detail" in capsys.readouterr().err) is replayed


def test_a_disabled_replay_guard_never_prints(capsys: pytest.CaptureFixture[str]) -> None:
    printing.set_quiet(True)
    printing.get_console().print("suppressed detail")

    with pytest.raises(SystemExit):
        with printing.replay_quiet_output_on_failure(False):
            raise SystemExit(1)

    assert capsys.readouterr().err == ""
