from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from .fake_github import OWNER_TOKEN
from .harness import CHECKER, GATEWAY_TOKEN, JEV_KEY, POLICY, REPO_ROOT, Harness, Step, git

pytestmark = [pytest.mark.integration, pytest.mark.medium]

UNROUTABLE_GATEWAY = "http://localhost:1"


class GatewayProxy:
    def __init__(self) -> None:
        self.requests: list[str] = []
        requests = self.requests

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                requests.append(self.path)
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args: Any) -> None:
                return None

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def _cloud_review(harness: Harness, branch: str, proxy: str | None) -> Step:
    checkout = harness.root / f"cloud-{branch.replace('/', '-')}"
    git(harness.root, "clone", "--quiet", str(harness.repo), str(checkout))
    git(checkout, "switch", "--quiet", branch)
    git(checkout, "branch", "--quiet", "-f", "develop", "origin/develop")
    if proxy:
        git_dir = Path(git(checkout, "rev-parse", "--absolute-git-dir").strip())
        (git_dir / "benchbox-cloud-gateway.status").write_text(
            f"gateway=reachable\ngateway_detail=ok\ngateway_proxy={proxy}\nomp=installed\n", encoding="utf-8"
        )
    env = {
        **harness.base_env(),
        "PYTHONPATH": str(REPO_ROOT),
        "ORACLE_GATEWAY_URL": UNROUTABLE_GATEWAY,
        "ORACLE_CLOUD_GATEWAY_TOKEN": GATEWAY_TOKEN,
        "GH_TOKEN": OWNER_TOKEN,
        "GH_ENTERPRISE_TOKEN": OWNER_TOKEN,
        "TYPESAFE_BASE_URL": harness.jev.url,
        "TYPESAFE_API_KEY": JEV_KEY,
    }
    argv = [sys.executable, "-m", "_project.scripts.oracle_reviewers.cli", "local-review", "--policy", POLICY]
    return harness.run("cloud-review", [*argv, "--base-ref", "develop"], env, cwd=checkout)


def _script_clean(harness: Harness) -> None:
    harness.branch("feat/cloud", {CHECKER: "def checker(a, b):\n    return a == b and a is not None\n"}, "reject None")
    harness.script_omp([{"decision": "SHIP", "summary": "No defects.", "files_examined": [CHECKER]}])


def test_a_cloud_review_reaches_a_tailnet_only_gateway_through_the_recorded_proxy(harness: Harness) -> None:
    _script_clean(harness)
    proxy = GatewayProxy()
    try:
        review = _cloud_review(harness, "feat/cloud", proxy.url)
    finally:
        proxy.close()

    assert review.code == 0, review.stdout + review.stderr
    assert "Local oracle review: no Critical or High findings; pr-open may proceed." in review.stdout
    assert proxy.requests == [f"{UNROUTABLE_GATEWAY}/healthz"]
    assert harness.omp_calls() >= 1
    assert UNROUTABLE_GATEWAY not in review.stdout + review.stderr and GATEWAY_TOKEN not in json.dumps(
        harness.local_captures()
    )


def test_a_cloud_review_without_the_recorded_proxy_refuses_and_says_how_to_start_it(harness: Harness) -> None:
    _script_clean(harness)

    review = _cloud_review(harness, "feat/cloud", None)

    assert review.code == 2, review.stdout + review.stderr
    assert "unreachable" in review.stderr and "make cloud-gateway" in review.stderr
    assert harness.omp_calls() == 0
