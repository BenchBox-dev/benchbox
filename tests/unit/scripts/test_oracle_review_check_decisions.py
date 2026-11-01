from __future__ import annotations

import importlib
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "_project/scripts"))
gate = importlib.import_module("_project.scripts.oracle_review_check")
sys.modules["oracle_review_check"] = gate

pytestmark = [pytest.mark.unit, pytest.mark.fast]

HEAD = "a" * 40
RUN = "1" * 32
ORACLE = "benchbox-oracle[bot]"
HEAD_DATE = "2026-10-01T12:00:00Z"
AFTER_HEAD = "2026-10-01T13:00:00Z"
RETARGETED = "2026-10-01T14:00:00Z"
AFTER_RETARGET = "2026-10-01T15:00:00Z"
BEFORE_HEAD = "2026-10-01T11:00:00Z"
SOUNDNESS = ["benchbox/core/equivalence/checker.py"]
API = "https://api.github.com"


def _body(run_uuid: str = RUN) -> str:
    from _project.scripts.oracle_reviewers import protocol

    record = {
        "v": protocol.MARKER_VERSION,
        "cycle": 1,
        "round": 1,
        "kind": protocol.FIRST,
        "decision": protocol.SHIP,
        "head_sha": HEAD,
        "base_ref": "develop",
        "reviewer": "large",
        "tier": "large",
        "strikes_after": 0,
        "open_defects": [],
        "next_id": 1,
        "patch_digest": "d" * 32,
        "carried": False,
        "summary": "",
        "run_uuid": run_uuid,
    }
    return f"### oracle-verdict: success for `{HEAD}`\n\nDecision: **SHIP**.\n\n{protocol.encode_marker(record)}"


def _status(state: str = "success", created_at: str = AFTER_HEAD, **over: Any) -> dict[str, Any]:
    description = f"large: {state} [run {RUN}]" if state == "success" else f"large: {state}"
    status = {
        "id": 1,
        "context": "oracle-verdict",
        "state": state,
        "description": description,
        "login": ORACLE,
        "user_type": "Bot",
        "created_at": created_at,
    }
    return {**status, **over}


def _receipt(**over: Any) -> dict[str, Any]:
    review = {
        "id": 1,
        "login": ORACLE,
        "user_type": "Bot",
        "commit_id": HEAD,
        "state": "COMMENTED",
        "submitted_at": HEAD_DATE,
        "body": _body(),
        "last_edited_at": None,
        "includes_created_edit": False,
    }
    return {**review, **over}


def _standin(created_at: str = AFTER_HEAD) -> dict[str, Any]:
    return {
        "login": "joeharris76",
        "user_type": "User",
        "body": f"Stand-in oracle review: APPROVE {HEAD}",
        "created_at": created_at,
        "updated_at": created_at,
    }


def _decide(**kwargs: Any) -> tuple[int, str]:
    return gate.decide(
        HEAD,
        kwargs.pop("files", SOUNDNESS),
        kwargs.pop("statuses", [_status()]),
        gate.path_matcher(2, 2, *gate._review_rules()),
        kwargs.pop("base_date", None),
        kwargs.pop("comments", []),
        reviews=kwargs.pop("reviews", [_receipt()]),
    )


def test_a_receipt_posted_exactly_at_the_retarget_does_not_count() -> None:
    assert (
        _decide(
            statuses=[_status(created_at=AFTER_RETARGET)],
            reviews=[_receipt(submitted_at=RETARGETED)],
            base_date=RETARGETED,
        )[0]
        == gate.WAITING
    )
    assert (
        _decide(
            statuses=[_status(created_at=AFTER_RETARGET)],
            reviews=[_receipt(submitted_at=AFTER_RETARGET)],
            base_date=RETARGETED,
        )[0]
        == gate.PASS
    )


def test_a_verdict_posted_exactly_at_the_retarget_does_not_count() -> None:
    receipt = _receipt(submitted_at=AFTER_RETARGET)
    assert (
        _decide(statuses=[_status(created_at=RETARGETED)], reviews=[receipt], base_date=RETARGETED)[0] == gate.WAITING
    )
    assert (
        _decide(statuses=[_status(created_at=AFTER_RETARGET)], reviews=[receipt], base_date=RETARGETED)[0] == gate.PASS
    )


def test_a_standin_posted_exactly_at_the_last_decisive_verdict_does_not_count() -> None:
    failing = _status("failure", created_at=RETARGETED)
    assert _decide(statuses=[failing], comments=[_standin(RETARGETED)])[0] == gate.WAITING
    assert _decide(statuses=[failing], comments=[_standin(AFTER_RETARGET)])[0] == gate.PASS


def test_a_standin_posted_exactly_at_the_retarget_does_not_count() -> None:
    assert _decide(statuses=[], comments=[_standin(RETARGETED)], base_date=RETARGETED)[0] == gate.WAITING
    assert _decide(statuses=[], comments=[_standin(AFTER_RETARGET)], base_date=RETARGETED)[0] == gate.PASS


@pytest.mark.parametrize("state", ["PENDING", "DISMISSED"])
def test_a_pending_or_dismissed_review_is_not_a_receipt(state: str) -> None:
    assert _decide(reviews=[_receipt(state=state)])[0] == gate.WAITING
    assert _decide(reviews=[_receipt(state="APPROVED")])[0] == gate.PASS


def test_the_receipt_with_the_higher_id_wins_a_same_second_tie() -> None:
    edited = _receipt(id=2, last_edited_at=AFTER_HEAD, includes_created_edit=True)
    assert _decide(reviews=[_receipt(id=1), edited])[0] == gate.WAITING
    assert _decide(reviews=[edited, _receipt(id=1)])[0] == gate.WAITING
    assert _decide(reviews=[_receipt(id=2), edited | {"id": 1}])[0] == gate.PASS


def test_a_receipt_with_an_id_beats_one_without_in_a_tie() -> None:
    edited = _receipt(id=1, last_edited_at=AFTER_HEAD, includes_created_edit=True)
    unnumbered = _receipt(id=None)
    assert _decide(reviews=[unnumbered, edited])[0] == gate.WAITING


def test_the_verdict_with_the_higher_id_wins_a_same_second_tie() -> None:
    assert _decide(statuses=[_status("failure", id=1), _status("success", id=None)])[0] == gate.WAITING


@pytest.mark.parametrize("missing", ["last_edited_at", "includes_created_edit"])
def test_a_receipt_missing_either_edit_field_does_not_count(missing: str) -> None:
    partial = {key: value for key, value in _receipt().items() if key != missing}
    assert _decide(reviews=[partial])[0] == gate.WAITING


def test_a_receipt_edited_after_posting_does_not_count() -> None:
    assert _decide(reviews=[_receipt(last_edited_at=AFTER_HEAD, includes_created_edit=False)])[0] == gate.WAITING


def test_the_newest_retarget_wins_across_timezone_offsets() -> None:
    events = [
        {"event": "base_ref_changed", "created_at": "2026-10-01T14:00:00Z"},
        {"event": "base_ref_changed", "created_at": "2026-10-01T09:00:00-07:00"},
    ]
    assert gate.latest_base_change(events) == "2026-10-01T09:00:00-07:00"


def test_a_gate_path_that_is_also_inert_needs_review_and_an_exempt_pattern_skips() -> None:
    assert gate.path_matcher(1, 1, (), ("gate/*",))(["gate/policy.md"]) is True
    assert gate.path_matcher(1, 1, ("zz_exempt/*",), ())(["zz_exempt/a.txt"]) is False
    assert gate.path_matcher(1, 1, ("zz_exempt/*",), ())(["zz_other/a.txt"]) is True


def test_the_review_rules_are_read_from_the_reviewer_policy_file(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Path] = []

    class Classifier:
        exempt_paths = ("e/*",)
        gate_paths = ("g/*",)

    class Policy:
        classifier = Classifier()

    def load(path: Path) -> Policy:
        seen.append(path)
        return Policy()

    monkeypatch.setattr("oracle_reviewers.policy.load_policy", load)
    assert gate._review_rules() == (("e/*",), ("g/*",))
    assert seen == [Path(".github/oracle-reviewers.yml")]


def test_annotate_receipt_ignores_reviews_before_the_retarget(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(gate, "fetch_review_edit", lambda token, node_id: calls.append(node_id) or (None, False))
    reviews = [_receipt(node_id="node_1")]
    gate.annotate_receipt("t", reviews, HEAD, RETARGETED)
    assert calls == []
    gate.annotate_receipt("t", reviews, HEAD, BEFORE_HEAD)
    assert calls == ["node_1"]


def test_annotate_receipt_records_what_the_edit_check_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, str]] = []

    def edit(token: str, node_id: str) -> tuple[str | None, bool]:
        seen.append((token, node_id))
        return AFTER_HEAD, False

    monkeypatch.setattr(gate, "fetch_review_edit", edit)
    reviews = [_receipt(node_id="node_1")]
    gate.annotate_receipt("tok", reviews, HEAD)
    assert seen == [("tok", "node_1")]
    assert reviews[0]["last_edited_at"] == AFTER_HEAD and reviews[0]["includes_created_edit"] is False
    assert _decide(reviews=reviews)[0] == gate.WAITING

    monkeypatch.setattr(gate, "fetch_review_edit", lambda token, node_id: (None, True))
    reviews = [_receipt(node_id="node_1")]
    gate.annotate_receipt("tok", reviews, HEAD)
    assert reviews[0]["last_edited_at"] is None and reviews[0]["includes_created_edit"] is True


@pytest.mark.parametrize("node_id", [None, ""])
def test_a_receipt_that_cannot_be_checked_is_recorded_as_edited(node_id: str | None) -> None:
    reviews = [_receipt(node_id=node_id)]
    gate.annotate_receipt("t", reviews, HEAD)
    assert reviews[0]["last_edited_at"] is not None
    assert reviews[0]["includes_created_edit"] is True


def test_a_failed_edit_check_is_recorded_as_edited(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(token: str, node_id: str) -> tuple[str | None, bool]:
        raise gate.CheckError("down")

    monkeypatch.setattr(gate, "fetch_review_edit", broken)
    reviews = [_receipt(node_id="node_1")]
    gate.annotate_receipt("t", reviews, HEAD)
    assert reviews[0]["last_edited_at"] is not None
    assert reviews[0]["includes_created_edit"] is True


class Network:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.rest: dict[str, Any] = {}
        self.graphql: Any = lambda body: {}
        self.error: Exception | None = None

    def request(self, token: str, url: str, body: dict[str, Any] | None = None) -> Any:
        self.requests.append({"url": url, "body": body, "token": token})
        if self.error is not None:
            raise self.error
        assert url.startswith(API), url
        route = url[len(API) :]
        if route == "/graphql":
            return self.graphql(body)
        path, _, query = route.partition("?")
        assert path in self.rest, route
        payload = self.rest[path]
        if isinstance(payload, dict) and "pages" in payload:
            payload = payload["pages"].get(int(query.split("&page=")[1]), [])
        return payload

    def paths(self) -> list[str]:
        return [request["url"][len(API) :] for request in self.requests]


@pytest.fixture
def net(monkeypatch: pytest.MonkeyPatch) -> Network:
    fake = Network()
    monkeypatch.setattr(gate, "_request", fake.request)
    return fake


def _authorized(fake: Network, token: str = "tok") -> None:
    assert fake.requests
    for request in fake.requests:
        assert request["token"] == token


class _Loopback:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.status = 200
        self.payload: bytes = b"{}"
        self.root = ""
        self.stop: Any = lambda: None


@pytest.fixture
def loopback(monkeypatch: pytest.MonkeyPatch) -> Any:
    state = _Loopback()

    class Handler(BaseHTTPRequestHandler):
        def _serve(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            state.requests.append(
                {
                    "path": self.path,
                    "method": self.command,
                    "headers": {key.lower(): value for key, value in self.headers.items()},
                    "body": json.loads(raw.decode("utf-8")) if raw else None,
                }
            )
            self.send_response(state.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(state.payload)))
            self.end_headers()
            self.wfile.write(state.payload)

        def do_GET(self) -> None:
            self._serve()

        def do_POST(self) -> None:
            self._serve()

        def log_message(self, format: str, *args: Any) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.root = f"http://127.0.0.1:{server.server_address[1]}"
    monkeypatch.setattr(gate, "API_ROOT", state.root)
    stopped: list[bool] = []

    def stop() -> None:
        if not stopped:
            stopped.append(True)
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    state.stop = stop
    try:
        yield state
    finally:
        stop()


def test_requests_carry_the_token_and_the_github_api_headers(loopback: Any) -> None:
    loopback.payload = b'{"ok": true}'
    assert gate._request("tok", f"{gate.API_ROOT}/x") == {"ok": True}
    request = loopback.requests[0]
    assert request["method"] == "GET"
    assert request["path"] == "/x"
    assert request["headers"]["authorization"] == "Bearer tok"
    assert request["headers"]["accept"] == "application/vnd.github+json"
    assert request["headers"]["x-github-api-version"] == "2022-11-28"
    assert request["headers"]["user-agent"] == "benchbox-oracle-review-check"
    assert "content-type" not in request["headers"]
    assert request["body"] is None


def test_a_posted_body_is_sent_as_utf8_json(loopback: Any) -> None:
    loopback.payload = json.dumps({"echo": "é"}).encode("utf-8")
    assert gate._request("tok", f"{gate.API_ROOT}/graphql", {"q": "é"}) == {"echo": "é"}
    request = loopback.requests[0]
    assert request["method"] == "POST"
    assert request["headers"]["content-type"] == "application/json"
    assert request["body"] == {"q": "é"}
    assert request["headers"]["authorization"] == "Bearer tok"


def test_an_http_error_status_becomes_a_check_error_naming_the_status(loopback: Any) -> None:
    loopback.status = 502
    with pytest.raises(gate.CheckError, match="HTTP 502"):
        gate._request("tok", f"{gate.API_ROOT}/x")


def test_an_unreachable_server_becomes_a_check_error(loopback: Any) -> None:
    loopback.stop()
    with pytest.raises(gate.CheckError, match="failed"):
        gate._request("tok", f"{loopback.root}/x")


def test_a_non_json_response_becomes_a_check_error(loopback: Any) -> None:
    loopback.payload = b"not json"
    with pytest.raises(gate.CheckError, match="failed"):
        gate._request("tok", f"{gate.API_ROOT}/x")


def test_pagination_follows_full_pages_and_stops_on_the_first_short_one(net: Network) -> None:
    full = [{"n": i} for i in range(100)]
    net.rest["/items"] = {"pages": {1: full, 2: full, 3: [{"n": 1}]}}
    assert len(gate._paginate("tok", "/items")) == 201
    assert net.paths() == [
        "/items?per_page=100&page=1",
        "/items?per_page=100&page=2",
        "/items?per_page=100&page=3",
    ]
    _authorized(net)


def test_pagination_stops_after_a_page_of_exactly_one_hundred_when_the_next_is_empty(net: Network) -> None:
    net.rest["/items"] = {"pages": {1: [{"n": i} for i in range(100)]}}
    assert len(gate._paginate("tok", "/items")) == 100
    assert net.paths() == ["/items?per_page=100&page=1", "/items?per_page=100&page=2"]


def test_pagination_returns_a_single_short_page_in_one_request(net: Network) -> None:
    net.rest["/items"] = {"pages": {1: [{"n": 1}, {"n": 2}]}}
    assert gate._paginate("tok", "/items") == [{"n": 1}, {"n": 2}]
    assert net.paths() == ["/items?per_page=100&page=1"]


def test_fetch_pull_and_files_read_this_pull_request(net: Network) -> None:
    net.rest["/repos/o/r/pulls/7"] = {"head": {"sha": HEAD}}
    net.rest["/repos/o/r/pulls/7/files"] = {"pages": {1: [{"filename": "a.py"}]}}
    assert gate.fetch_pull("tok", "o/r", 7) == {"head": {"sha": HEAD}}
    assert gate.fetch_files("tok", "o/r", 7) == [{"filename": "a.py"}]
    assert net.paths() == ["/repos/o/r/pulls/7", "/repos/o/r/pulls/7/files?per_page=100&page=1"]
    _authorized(net)


def test_fetch_statuses_comments_and_timeline_read_this_pull_request(net: Network) -> None:
    net.rest[f"/repos/o/r/commits/{HEAD}/statuses"] = {"pages": {1: []}}
    net.rest["/repos/o/r/issues/7/comments"] = {"pages": {1: [{"user": None, "body": "x", "created_at": AFTER_HEAD}]}}
    net.rest["/repos/o/r/issues/7/timeline"] = {"pages": {1: [{"event": "base_ref_changed", "created_at": RETARGETED}]}}
    assert gate.fetch_statuses("tok", "o/r", HEAD) == []
    assert gate.fetch_comments("tok", "o/r", 7) == [
        {"login": None, "user_type": None, "body": "x", "created_at": AFTER_HEAD, "updated_at": None}
    ]
    assert gate.fetch_base_change_date("tok", "o/r", 7) == RETARGETED
    _authorized(net)


def test_fetch_reviews_keeps_every_field_the_gate_decides_on(net: Network) -> None:
    net.rest["/repos/o/r/pulls/7/reviews"] = {
        "pages": {
            1: [
                {
                    "id": 11,
                    "node_id": "N1",
                    "user": {"login": ORACLE, "type": "Bot"},
                    "commit_id": HEAD,
                    "state": "COMMENTED",
                    "submitted_at": HEAD_DATE,
                    "body": "b",
                    "html_url": "ignored",
                },
                {"id": 12, "user": None},
            ]
        }
    }
    assert gate.fetch_reviews("tok", "o/r", 7) == [
        {
            "login": ORACLE,
            "user_type": "Bot",
            "commit_id": HEAD,
            "state": "COMMENTED",
            "submitted_at": HEAD_DATE,
            "body": "b",
            "node_id": "N1",
            "id": 11,
        },
        {
            "login": None,
            "user_type": None,
            "commit_id": None,
            "state": None,
            "submitted_at": None,
            "body": None,
            "node_id": None,
            "id": 12,
        },
    ]
    _authorized(net)


def test_a_review_edit_lookup_sends_the_node_and_returns_both_flags(net: Network) -> None:
    net.graphql = lambda body: {"data": {"node": {"lastEditedAt": AFTER_HEAD, "includesCreatedEdit": True}}}
    assert gate.fetch_review_edit("tok", "N9") == (AFTER_HEAD, True)
    request = net.requests[0]
    assert request["url"] == f"{API}/graphql"
    assert request["body"]["variables"] == {"id": "N9"}
    assert "PullRequestReview" in request["body"]["query"]
    _authorized(net)


def test_a_review_edit_lookup_with_graphql_errors_fails_even_when_data_is_present(net: Network) -> None:
    net.graphql = lambda body: {
        "errors": [{"message": "x"}],
        "data": {"node": {"lastEditedAt": None, "includesCreatedEdit": False}},
    }
    with pytest.raises(gate.CheckError):
        gate.fetch_review_edit("tok", "N9")


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"data": {}},
        {"data": {"node": {"lastEditedAt": None}}},
        {"data": {"node": {"includesCreatedEdit": False}}},
    ],
    ids=["no-data", "no-node", "no-includes", "no-last-edited"],
)
def test_a_review_edit_lookup_missing_any_field_fails_closed(net: Network, payload: dict[str, Any]) -> None:
    net.graphql = lambda body: payload
    with pytest.raises(gate.CheckError):
        gate.fetch_review_edit("tok", "N9")


def test_the_head_commit_is_read_from_graphql(net: Network) -> None:
    net.graphql = lambda body: {"data": {"repository": {"pullRequest": {"headRefOid": HEAD}}}}
    assert gate.fetch_head_oid("tok", "o/r", 7) == HEAD
    assert net.requests[0]["body"]["variables"] == {"owner": "o", "name": "r", "number": 7}
    net.graphql = lambda body: {
        "errors": [{"message": "x"}],
        "data": {"repository": {"pullRequest": {"headRefOid": HEAD}}},
    }
    with pytest.raises(gate.CheckError):
        gate.fetch_head_oid("tok", "o/r", 7)


def test_the_arguments_default_to_the_oracle_and_require_a_repository_and_pull_request() -> None:
    args = gate._parse_args(["--repo", "o/r", "--pr", "7"])
    assert (args.repo, args.pr, args.signal) == ("o/r", 7, "oracle")
    for argv in (["--pr", "7"], ["--repo", "o/r"], ["--repo", "o/r", "--pr", "x"]):
        with pytest.raises(SystemExit) as exit_info:
            gate._parse_args(argv)
        assert exit_info.value.code == 2


def _wire(
    net: Network, *, files: list[str], statuses: list[dict[str, Any]], reviews: list[dict[str, Any]], **extra: Any
) -> None:
    net.rest["/repos/o/r/pulls/7"] = {"head": {"sha": HEAD}, "changed_files": len(files)}
    net.rest["/repos/o/r/pulls/7/files"] = {"pages": {1: [{"filename": name} for name in files]}}
    net.rest[f"/repos/o/r/commits/{HEAD}/statuses"] = {
        "pages": {
            1: [
                {
                    "id": item["id"],
                    "context": item["context"],
                    "state": item["state"],
                    "description": item["description"],
                    "creator": {"login": item["login"], "type": item["user_type"]},
                    "created_at": item["created_at"],
                }
                for item in statuses
            ]
        }
    }
    net.rest["/repos/o/r/pulls/7/reviews"] = {
        "pages": {
            1: [
                {
                    "id": item["id"],
                    "node_id": item.get("node_id", "N1"),
                    "user": {"login": item["login"], "type": item["user_type"]},
                    "commit_id": item["commit_id"],
                    "state": item["state"],
                    "submitted_at": item["submitted_at"],
                    "body": item["body"],
                }
                for item in reviews
            ]
        }
    }
    net.rest["/repos/o/r/issues/7/comments"] = {"pages": {1: extra.get("comments", [])}}
    net.rest["/repos/o/r/issues/7/timeline"] = {"pages": {1: extra.get("timeline", [])}}

    net.graphql = lambda body: {"data": {"node": {"lastEditedAt": None, "includesCreatedEdit": False}}}


def _run(monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    return gate.main(["--repo", "o/r", "--pr", "7"])


def test_a_reviewed_pull_request_passes_and_every_call_uses_the_token_and_target(
    net: Network, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    _wire(net, files=SOUNDNESS, statuses=[_status()], reviews=[_receipt()])
    assert _run(monkeypatch) == gate.PASS
    assert capsys.readouterr().out == f"oracle-review: pass (oracle verdict for {HEAD})\n"
    _authorized(net)
    assert set(net.paths()) == {
        "/repos/o/r/pulls/7",
        "/repos/o/r/pulls/7/files?per_page=100&page=1",
        f"/repos/o/r/commits/{HEAD}/statuses?per_page=100&page=1",
        "/graphql",
        "/repos/o/r/issues/7/timeline?per_page=100&page=1",
        "/repos/o/r/issues/7/comments?per_page=100&page=1",
        "/repos/o/r/pulls/7/reviews?per_page=100&page=1",
    }
    assert len(net.requests) == 7
    bodies = [request["body"] for request in net.requests if request["body"] is not None]
    assert not any("reviewThreads" in body["query"] for body in bodies)
    assert [body["variables"] for body in bodies] == [{"id": "N1"}]


def test_only_exempt_paths_stop_before_any_status_is_read(
    net: Network, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    _wire(net, files=["NOTES.md"], statuses=[], reviews=[])
    assert _run(monkeypatch) == gate.PASS
    assert capsys.readouterr().out == "oracle-review: only exempt paths changed\n"
    assert net.paths() == ["/repos/o/r/pulls/7", "/repos/o/r/pulls/7/files?per_page=100&page=1"]


def test_the_reviewer_policy_gate_paths_keep_an_otherwise_exempt_change_under_review(
    net: Network, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    _wire(net, files=["_project/scripts/oracle_reviewers/templates/gate.md"], statuses=[], reviews=[])
    assert _run(monkeypatch) == gate.WAITING
    assert "waiting for the oracle's verdict" in capsys.readouterr().out


def test_a_retarget_voids_the_earlier_verdict_and_receipt_in_the_main_flow(
    net: Network, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    timeline = [{"event": "base_ref_changed", "created_at": RETARGETED}]
    _wire(net, files=SOUNDNESS, statuses=[_status()], reviews=[_receipt()], timeline=timeline)
    assert _run(monkeypatch) == gate.WAITING
    assert "waiting for the oracle's verdict" in capsys.readouterr().out
    assert [
        request["body"] for request in net.requests if request["body"] and "id" in request["body"].get("variables", {})
    ] == []


def test_a_retarget_after_the_receipt_keeps_a_later_verdict_and_receipt(
    net: Network, monkeypatch: pytest.MonkeyPatch
) -> None:
    timeline = [{"event": "base_ref_changed", "created_at": RETARGETED}]
    _wire(
        net,
        files=SOUNDNESS,
        statuses=[_status(created_at=AFTER_RETARGET)],
        reviews=[_receipt(submitted_at=AFTER_RETARGET)],
        timeline=timeline,
    )
    assert _run(monkeypatch) == gate.PASS


def test_a_standin_attestation_is_read_from_the_pull_request_comments(
    net: Network, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    comment = {
        "user": {"login": "joeharris76", "type": "User"},
        "body": f"Stand-in oracle review: APPROVE {HEAD}",
        "created_at": AFTER_HEAD,
        "updated_at": AFTER_HEAD,
    }
    _wire(net, files=SOUNDNESS, statuses=[], reviews=[], comments=[comment])
    assert _run(monkeypatch) == gate.PASS
    assert capsys.readouterr().out == f"oracle-review: pass (stand-in review attested by joeharris76 for {HEAD})\n"
    _authorized(net)


def test_a_missing_token_is_an_error(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert gate.main(["--repo", "o/r", "--pr", "7"]) == gate.ERROR
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "oracle-review: error: GITHUB_TOKEN is not set\n"


def test_a_repository_that_is_not_owner_slash_name_is_an_error(
    net: Network, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    for repo in ("o", "o/r/x"):
        assert gate.main(["--repo", repo, "--pr", "7"]) == gate.ERROR
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == "oracle-review: error: --repo must be OWNER/NAME\n"
    assert net.requests == []


def test_a_github_failure_is_reported_on_stderr_as_an_error(
    net: Network, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    net.error = gate.CheckError("GitHub returned HTTP 500 for /x")
    assert _run(monkeypatch) == gate.ERROR
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("oracle-review: error: ")
    assert "500" in captured.err


def test_the_command_line_defaults_to_the_process_arguments(
    net: Network, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    _wire(net, files=["NOTES.md"], statuses=[], reviews=[])
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setattr(sys, "argv", ["oracle_review_check.py", "--repo", "o/r", "--pr", "7"])
    assert gate.main() == gate.PASS
    assert capsys.readouterr().out == "oracle-review: only exempt paths changed\n"


def test_the_policy_exempt_paths_are_applied_to_the_pull_request_files(
    net: Network, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    monkeypatch.setattr(gate, "_review_rules", lambda: (("zz_exempt/*",), ()))
    _wire(net, files=["zz_exempt/a.txt"], statuses=[], reviews=[])
    assert _run(monkeypatch) == gate.PASS
    assert capsys.readouterr().out == "oracle-review: only exempt paths changed\n"


def _open_findings_status(description: str = "large: SHIP WITH FIXES, 2 defect(s) to fix") -> dict[str, Any]:
    return _status("failure", created_at=RETARGETED, description=description)


def test_a_standin_is_rejected_on_a_head_whose_latest_decisive_verdict_lists_open_findings() -> None:
    code, message = _decide(statuses=[_open_findings_status()], comments=[_standin(AFTER_RETARGET)])
    assert code == gate.WAITING
    assert "cannot waive" in message and "stand-in attestation" not in message


def test_a_carried_ship_with_fixes_verdict_also_rejects_a_standin() -> None:
    carried = _open_findings_status("carried from aaaaaaaaaaaa: SHIP WITH FIXES")
    assert _decide(statuses=[carried], comments=[_standin(AFTER_RETARGET)])[0] == gate.WAITING


@pytest.mark.parametrize(
    "description",
    [
        "large: DO NOT SHIP",
        "pending: too large for the oracle",
        "refused after 3 DO NOT SHIP decisions: open a new one",
    ],
)
def test_a_standin_still_covers_a_head_without_open_findings(description: str) -> None:
    status = _status("failure", created_at=RETARGETED, description=description)
    assert _decide(statuses=[status], comments=[_standin(AFTER_RETARGET)])[0] == gate.PASS


def test_a_standin_still_covers_absence() -> None:
    assert _decide(statuses=[], comments=[_standin(AFTER_RETARGET)])[0] == gate.PASS


def test_a_standin_still_covers_an_edit_hold_even_with_open_findings() -> None:
    hold = _status("pending", created_at=AFTER_RETARGET, description="an oracle review was edited")
    standin = _standin("2026-10-01T16:00:00Z")
    assert _decide(statuses=[_open_findings_status(), hold], comments=[standin])[0] == gate.PASS


def test_a_later_non_hold_pending_status_does_not_lift_the_open_findings_rejection() -> None:
    later = _status("pending", created_at=AFTER_RETARGET, description="pending: no reviewer available")
    standin = _standin("2026-10-01T16:00:00Z")
    assert _decide(statuses=[_open_findings_status(), later], comments=[standin])[0] == gate.WAITING


def test_an_escalated_finding_names_the_exact_owner_action() -> None:
    escalated = _open_findings_status("large: SHIP WITH FIXES, 2 defect(s) to fix; escalated D1,D3")
    code, message = _decide(statuses=[escalated], comments=[_standin(AFTER_RETARGET)])
    assert code == gate.WAITING
    assert "D1,D3" in message and "Oracle adjudication: WITHDRAW|MAINTAIN D<n> - <reason>" in message
