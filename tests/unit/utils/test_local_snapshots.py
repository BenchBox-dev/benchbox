from __future__ import annotations

import pytest

from benchbox.utils import local_snapshots as ls
from benchbox.utils.resource_limits import EXTERNAL_TIMEOUT_SECONDS

pytestmark = [pytest.mark.unit, pytest.mark.fast]

LISTING = (
    "Snapshots for volume group containing disk /:\n"
    "com.apple.TimeMachine.2026-11-20-140045.local\n"
    "com.apple.TimeMachine.2026-11-22-085225.local\n"
    "com_haystacksoftware_arqagent_abc123\n"
)


def describe(runner, *, platform="darwin", which=lambda name: f"/usr/bin/{name}"):
    return ls.describe_local_snapshots(runner=runner, which=which, platform=platform)


def test_snapshots_present_are_counted_and_named():
    calls = []

    def runner(argv, timeout):
        calls.append((list(argv), timeout))
        return LISTING

    line = describe(runner)
    assert calls == [(["/usr/bin/tmutil", "listlocalsnapshots", "/"], EXTERNAL_TIMEOUT_SECONDS)]
    assert "3" in line
    assert "2026-11-20-140045" in line and "2026-11-22-085225" in line
    assert "com_haystacksoftware_arqagent_abc123 (third-party)" in line
    assert "delete" not in line.lower().replace("deleted data", "")


def test_no_snapshots_says_none():
    assert describe(lambda argv, timeout: "Snapshots for volume group containing disk /:\n").endswith(": none")


def test_missing_tool_omits_the_line():
    assert describe(lambda argv, timeout: pytest.fail("must not run"), which=lambda name: None) is None


def test_timeout_or_failure_is_unmeasured():
    assert describe(lambda argv, timeout: None) == "local snapshots on /: unmeasured"


def test_raising_runner_is_unmeasured():
    def runner(argv, timeout):
        raise RuntimeError("boom")

    assert describe(runner) == "local snapshots on /: unmeasured"


@pytest.mark.parametrize("platform", ["linux", "win32"])
def test_non_macos_omits_the_line(platform):
    assert describe(lambda argv, timeout: pytest.fail("must not run"), platform=platform) is None


def test_query_timeout_is_within_the_five_second_limit():
    assert EXTERNAL_TIMEOUT_SECONDS <= 5.0
