from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    SUCCESS = 0
    ERROR = 1
    USAGE = 2
    REGRESSION = 3
    NOT_COMPARABLE = 4


RESERVED_BY_HOST_LOCK = range(75, 80)

EXIT_CODE_MEANINGS: dict[ExitCode, str] = {
    ExitCode.SUCCESS: "success",
    ExitCode.ERROR: "error: the command failed or an input could not be read",
    ExitCode.USAGE: "usage error: invalid options or arguments",
    ExitCode.REGRESSION: "compare: a regression exceeded the --fail-on-regression threshold",
    ExitCode.NOT_COMPARABLE: "compare: the results cannot be compared (different variants or timing modes)",
}


def exit_code_help(codes: tuple[ExitCode, ...]) -> str:
    return "\n".join(f"    {int(code)}  {EXIT_CODE_MEANINGS[code]}" for code in codes)
