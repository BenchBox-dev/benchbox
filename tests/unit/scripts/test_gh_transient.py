from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

SPEC = importlib.util.spec_from_file_location(
    "gh_transient_under_test", Path(__file__).resolve().parents[3] / "scripts" / "gh_transient.py"
)
assert SPEC is not None and SPEC.loader is not None
gt = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = gt
SPEC.loader.exec_module(gt)

WALL = 1_800_000_000.0


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def wall(self) -> float:
        return WALL + self.now

    def budget(self, seconds: float):
        return gt.Budget(seconds, clock=self.clock, sleep=self.sleep, wall=self.wall)


class Script:
    def __init__(self, *results, rate_limit=None) -> None:
        self.results = list(results)
        self.rate_limit = rate_limit
        self.calls: list[list[str]] = []

    def __call__(self, cmd):
        self.calls.append(list(cmd))
        if cmd == ["gh", "api", "rate_limit"]:
            return (0, json.dumps(self.rate_limit)) if self.rate_limit is not None else (1, "HTTP 500")
        result = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(result, Exception):
            raise result
        return result

    def reads(self) -> list[list[str]]:
        return [call for call in self.calls if call != ["gh", "api", "rate_limit"]]


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("HTTP 403: API rate limit exceeded", gt.RATE_LIMIT),
        ("HTTP 403: You have exceeded a secondary rate limit", gt.RATE_LIMIT),
        ("gh: HTTP 429: Too Many Requests", gt.RATE_LIMIT),
        ("HTTP 503: Egress is over the account limit", gt.SERVER_ERROR),
        ("ServerBusy: The server is busy", gt.SERVER_ERROR),
        ("GitHub returned HTTP 502", gt.SERVER_ERROR),
        ("HTTP 500: Internal Server Error", gt.SERVER_ERROR),
        ('Post "https://api.github.com/graphql": net/http: TLS handshake timeout', gt.NETWORK),
        ("dial tcp 140.82.1.6:443: i/o timeout", gt.NETWORK),
        ("Command ['gh'] timed out after 120 seconds", gt.NETWORK),
        ("cannot read tracker batch b: unparseable output (exit 2)", gt.TRACKER_READ),
    ],
)
def test_transient_failures_are_classified(text: str, kind: str) -> None:
    assert gt.classify(text).kind == kind


@pytest.mark.parametrize(
    "text",
    [
        "HTTP 403: Resource not accessible by integration",
        "HTTP 401: Bad credentials",
        "HTTP 404: Not Found",
        "HTTP 422: Validation Failed",
        "the core job ended failure",
        "",
    ],
)
def test_permanent_failures_are_not_transient(text: str) -> None:
    assert gt.classify(text) is None
    assert not gt.is_transient(text)


@pytest.mark.parametrize(
    ("cmd", "retryable"),
    [
        (["gh", "api", "repos/o/r/actions/runs"], True),
        (["gh", "api", "--paginate", "--jq", ".x", "repos/o/r/pulls"], True),
        (["gh", "api", "graphql", "-f", "query=query { viewer { login } }"], True),
        (["gh", "api", "graphql", "-f", "query=mutation { x }"], False),
        (["gh", "api", "graphql", "-F", "query=@q.graphql"], False),
        (["gh", "api", "graphql", "--input", "q.json"], False),
        (["gh", "api", "graphql", "-fquery=query { viewer { login } }"], True),
        (["gh", "api", "graphql", "-fquery=mutation { x }"], False),
        (["gh", "api", "graphql", "-f", "other=1"], False),
        (["gh", "api", "-XPOST", "repos/o/r/x"], False),
        (["gh", "api", "--method=POST", "repos/o/r/x"], False),
        (["gh", "api", "--field=a=b", "repos/o/r/x"], False),
        (["gh", "api", "-fbody=x", "repos/o/r/x"], False),
        (["gh", "api", "--input=body.json", "repos/o/r/x"], False),
        (["gh", "api", "--raw-field", "a=b", "repos/o/r/x"], False),
        (["gh", "api", "--method", "POST", "repos/o/r/actions/runs/1/cancel"], False),
        (["gh", "api", "-X", "PUT", "repos/o/r/x"], False),
        (["gh", "api", "-f", "body=x", "repos/o/r/issues/1/comments"], False),
        (["gh", "run", "download", "9", "-n", "x"], True),
        (["gh", "run", "view", "9"], True),
        (["gh", "pr", "view", "7"], True),
        (["gh", "pr", "merge", "7", "--squash"], False),
        (["gh", "workflow", "run", "x.yml"], False),
        (["git", "fetch"], False),
        (["uv", "run", "--", "tractable", "batch", "show", "b"], True),
    ],
)
def test_only_idempotent_reads_are_retried(cmd: list[str], retryable: bool) -> None:
    assert gt.retryable_read(cmd) is retryable


def test_a_server_error_is_retried_with_backoff_then_returns_the_good_read() -> None:
    clock = Clock()
    run = Script((1, "HTTP 503: ServerBusy"), (1, "HTTP 502"), (0, "ok"))
    assert gt.RetryingRunner(run, clock.budget(600))(["gh", "api", "x"]) == (0, "ok")
    assert clock.sleeps == [5.0, 15.0]


def test_a_persistent_server_error_returns_a_failure_naming_the_cause_after_four_attempts() -> None:
    clock = Clock()
    run = Script((1, "HTTP 503: Egress is over the account limit"))
    code, out = gt.RetryingRunner(run, clock.budget(600))(["gh", "run", "download", "9"])
    assert code != 0
    assert out.startswith("transient GitHub server error did not clear after 4 attempt(s) over 65s")
    assert "Egress is over the account limit" in out
    assert len(run.reads()) == 4
    assert clock.sleeps == [5.0, 15.0, 45.0]


def test_the_wait_budget_is_never_extended() -> None:
    clock = Clock()
    run = Script((1, "HTTP 503"))
    code, out = gt.RetryingRunner(run, clock.budget(10))(["gh", "api", "x"])
    assert code != 0
    assert "cannot be waited out inside the wait budget" in out
    assert clock.now <= 10.0
    assert clock.sleeps == [5.0]


def test_a_permanent_error_is_returned_at_once() -> None:
    clock = Clock()
    run = Script((1, "HTTP 403: Resource not accessible by integration"))
    assert gt.RetryingRunner(run, clock.budget(600))(["gh", "api", "x"]) == (
        1,
        "HTTP 403: Resource not accessible by integration",
    )
    assert clock.sleeps == [] and len(run.reads()) == 1


def test_a_write_is_never_retried() -> None:
    clock = Clock()
    run = Script((1, "HTTP 503"))
    assert gt.RetryingRunner(run, clock.budget(600))(["gh", "pr", "merge", "7"]) == (1, "HTTP 503")
    assert len(run.calls) == 1 and clock.sleeps == []


def test_a_rate_limit_waits_for_the_reported_reset_inside_the_budget() -> None:
    clock = Clock()
    run = Script(
        (1, "HTTP 403: API rate limit exceeded"),
        (0, "ok"),
        rate_limit={"resources": {"core": {"remaining": 0, "reset": WALL + 180}}},
    )
    assert gt.RetryingRunner(run, clock.budget(600))(["gh", "api", "x"]) == (0, "ok")
    assert clock.sleeps == [181.0]


def test_a_rate_limit_reset_beyond_the_budget_names_the_reset_and_does_not_sleep() -> None:
    clock = Clock()
    run = Script(
        (1, "HTTP 403: API rate limit exceeded"),
        rate_limit={"resources": {"core": {"remaining": 0, "reset": WALL + 1800}}},
    )
    code, out = gt.RetryingRunner(run, clock.budget(600))(["gh", "api", "x"])
    assert code == 1
    assert "transient GitHub rate limit cannot be waited out" in out
    assert "resets at 2027-01-15T08:30:00Z (in 30.0 min)" in out
    assert clock.sleeps == []


def test_a_rate_limit_without_a_known_reset_waits_a_minute() -> None:
    clock = Clock()
    run = Script((1, "HTTP 429"), (0, "ok"))
    assert gt.RetryingRunner(run, clock.budget(600))(["gh", "api", "x"]) == (0, "ok")
    assert clock.sleeps == [60.0]


def test_a_retry_after_hint_in_the_message_is_honoured() -> None:
    clock = Clock()
    run = Script((1, "HTTP 403: secondary rate limit; Retry-After: 30"), (0, "ok"))
    assert gt.RetryingRunner(run, clock.budget(600))(["gh", "api", "x"]) == (0, "ok")
    assert clock.sleeps == [31.0]


def test_a_transient_exception_from_the_runner_is_retried_and_a_permanent_one_propagates() -> None:
    clock = Clock()
    run = Script(RuntimeError("cannot run the tracker read: Command timed out after 120 seconds"), (0, "ok"))
    assert gt.RetryingRunner(run, clock.budget(600))(["gh", "pr", "view", "7"]) == (0, "ok")
    with pytest.raises(RuntimeError, match="disk full"):
        gt.RetryingRunner(Script(RuntimeError("disk full")), clock.budget(600))(["gh", "pr", "view", "7"])


def test_an_unparseable_tracker_read_is_retried_and_a_json_error_envelope_is_not() -> None:
    clock = Clock()
    show = ["uv", "run", "--", "tractable", "batch", "show", "b"]
    run = Script((2, "Traceback: ..."), (0, json.dumps({"ok": True, "data": {}})))
    assert gt.RetryingRunner(run, clock.budget(600))(show)[0] == 0
    assert clock.sleeps == [5.0]
    envelope = json.dumps({"ok": False, "error": "no such batch"})
    run = Script((2, envelope))
    assert gt.RetryingRunner(run, clock.budget(600))(show) == (2, envelope)
    assert len(run.calls) == 1


def test_a_persistent_unparseable_tracker_read_fails_naming_the_cause() -> None:
    clock = Clock()
    code, out = gt.RetryingRunner(Script((2, "garbage")), clock.budget(600))(["tractable", "batch", "show", "b"])
    assert code == 2
    assert out.startswith("transient tracker read unparseable did not clear after 4 attempt(s)")


@pytest.mark.parametrize(
    ("text", "waits"),
    [
        ("transient GitHub server error did not clear after 4 attempt(s) over 65s: HTTP 503", True),
        (
            "transient GitHub rate limit cannot be waited out inside the wait budget after 1 attempt(s): HTTP 403: API rate limit exceeded",
            False,
        ),
        ("cannot read tracker batch b: unparseable output (exit 2)", True),
        ("HTTP 403: Resource not accessible", False),
    ],
)
def test_waiting_on_distinguishes_a_budget_that_cannot_cover_the_retry(text: str, waits: bool) -> None:
    assert gt.waiting_on(text) is waits


def test_explain_names_the_rate_limit_reset_for_a_pull_request_lookup_failure() -> None:
    run = Script(rate_limit={"resources": {"core": {"remaining": 0, "reset": WALL + 300}}})
    message = gt.explain(
        "the open pull request for this branch could not be read: HTTP 403: API rate limit exceeded", run, lambda: WALL
    )
    assert message is not None
    assert "resets at 2027-01-15T08:05:00Z (in 5.0 min)" in message
    assert "nothing was pushed" in message


def test_explain_ignores_failures_that_are_not_a_github_read_or_not_transient() -> None:
    assert gt.explain("model returned HTTP 503") is None
    assert gt.explain("the open pull request could not be read: HTTP 404: Not Found") is None


def test_the_check_flag_reports_transience_through_the_exit_code(capsys) -> None:
    assert gt.main(["--check", "HTTP 503: Egress is over the account limit"]) == 0
    assert gt.main(["--check", "no artifacts found"]) == 1
    assert capsys.readouterr().out == ""
