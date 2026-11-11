import pytest

from benchbox.utils.printing import get_console, get_quiet_console, quiet_console, set_quiet

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_quiet_console_respects_quiet_flag() -> None:
    set_quiet(False)
    console = get_console()
    with console.capture() as capture:
        quiet_console.print("loud")
    assert "loud" in capture.get()

    set_quiet(True)
    sink_console = get_console()
    assert sink_console is get_quiet_console()
    assert sink_console is get_console(quiet=True)
    assert sink_console is not get_console(quiet=False)
    quiet_console.print("silent")

    set_quiet(False)


