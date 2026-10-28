from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from _project.scripts.oracle_reviewers import omp_bump, omp_bump_gate, omp_candidate, omp_pin

from .conftest import POLICY_PATH, REPO_ROOT
from .test_omp_candidate import FIXTURES, _chain_from, _fake, _good_runs

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ANSWER = json.loads((FIXTURES / "build_api_latest.json").read_text(encoding="utf-8"))
POLICY = POLICY_PATH.read_text(encoding="utf-8")
HOST = omp_pin.current_platform()
PREVIOUS_SCRIPT = (
    "#!{python}\n"
    "import sys\n"
    "print('omp/18.12.1' if sys.argv[1:] == ['--version'] else 'FLAGS\\n      --old-flag=<value>   removed later')\n"
)


def _answers(mutate: Callable[[str, dict[str, Any]], None] | None = None) -> Callable[[str], Any]:
    served: dict[str, dict[str, Any]] = {}
    for target, (api_target, _) in omp_bump.TARGETS.items():
        answer = copy.deepcopy(ANSWER)
        if mutate is not None:
            mutate(target, answer)
        served[f"{omp_bump.BUILD_HOST}/api/products/omp/latest/{api_target}?channel=stable"] = answer
    return served.__getitem__


def _published() -> dict[str, dict[str, str]]:
    files = {item["name"]: item for item in ANSWER["build"]["files"]}
    return {target: files[name] for target, (_, name) in omp_bump.TARGETS.items()}


def test_the_captured_build_host_answer_becomes_a_release_with_published_urls_and_hashes() -> None:
    release = omp_bump.fetch_release(_answers())
    published = _published()
    assert release.version == "18.13.2"
    assert release.build_id == ANSWER["build"]["id"]
    assert set(release.builds) == set(omp_bump.TARGETS)
    for target, build in release.builds.items():
        assert build.url == omp_bump.BUILD_HOST + published[target]["url"]
        assert build.sha256 == published[target]["sha256"]


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda target, answer: answer["build"].update(channel="canary"), "not a stable build"),
        (lambda target, answer: answer["build"].update(version="latest"), "valid version"),
        (lambda target, answer: answer["build"].update(files=[]), "lists no omp-"),
        (
            lambda target, answer: answer["build"].update(
                id="20260101-000000-abcdef0" if target == "linux-arm64" else answer["build"]["id"]
            ),
            "does not belong to build",
        ),
        (
            lambda target, answer: [
                item.update(sha256="ABC") for item in answer["build"]["files"] if item["name"] == "omp-linux-x64"
            ],
            "no valid sha256",
        ),
        (
            lambda target, answer: [
                item.update(url="/d/omp/elsewhere/omp-linux-x64")
                for item in answer["build"]["files"]
                if item["name"] == "omp-linux-x64"
            ],
            "does not belong to build",
        ),
    ],
)
def test_a_build_host_answer_that_could_not_be_trusted_is_refused(mutate, expected: str) -> None:
    with pytest.raises(omp_bump.BumpError, match=expected):
        omp_bump.fetch_release(_answers(mutate))


def test_a_build_host_that_names_a_different_latest_build_per_platform_is_refused() -> None:
    def split(target: str, answer: dict[str, Any]) -> None:
        if target == "darwin-arm64":
            answer["build"]["version"] = "18.13.3"

    with pytest.raises(omp_bump.BumpError, match="disagree on the latest build"):
        omp_bump.fetch_release(_answers(split))


def test_rewriting_the_pin_from_the_committed_pin_reproduces_the_committed_policy_byte_for_byte() -> None:
    assert omp_bump.replace_pin(POLICY, omp_pin.pin_from_text(POLICY)) == POLICY


def test_a_bump_rewrites_only_the_omp_section() -> None:
    release = omp_bump.fetch_release(_answers())
    bumped = omp_bump.replace_pin(POLICY, release.pin())
    assert omp_pin.pin_from_text(bumped) == release.pin()
    before, after = POLICY.splitlines(), bumped.splitlines()
    changed = [line for line in after if line not in before]
    assert changed and all("18.13.2" in line or "build.stencil.so" in line or "sha256:" in line for line in changed)
    assert [line for line in after if not line.startswith(("omp:", "  ", "    ", "      "))] == [
        line for line in before if not line.startswith(("omp:", "  ", "    ", "      "))
    ]
    assert omp_bump.replace_pin(bumped, omp_pin.pin_from_text(POLICY)) == POLICY


def test_a_policy_without_exactly_one_omp_section_is_refused() -> None:
    release = omp_bump.fetch_release(_answers())
    with pytest.raises(omp_bump.BumpError, match="exactly one omp section"):
        omp_bump.replace_pin("version: 2\n", release.pin())


def _content_fetch(contents: dict[str, bytes]) -> Callable[[str, Path], None]:
    def fetch(url: str, destination: Path) -> None:
        destination.write_bytes(contents[url])

    return fetch


def test_two_downloads_that_agree_with_the_published_hash_are_accepted(tmp_path: Path) -> None:
    release = omp_bump.fetch_release(_answers())
    digests = {build.url: build.sha256 for build in release.builds.values()}
    served = {url: f"build for {url}".encode() for url in digests}
    stubbed = omp_bump.Release(
        release.version,
        release.build_id,
        {t: omp_pin.Build(b.url, hashlib.sha256(served[b.url]).hexdigest()) for t, b in release.builds.items()},
    )
    paths, problems = omp_bump.verify_downloads(stubbed, tmp_path, _content_fetch(served))
    assert problems == [] and set(paths) == set(omp_bump.TARGETS)
    assert all(path.is_file() for path in paths.values())


def test_a_download_that_hashes_differently_from_the_published_hash_is_reported(tmp_path: Path) -> None:
    release = omp_bump.fetch_release(_answers())
    served = {build.url: b"tampered" for build in release.builds.values()}
    _, problems = omp_bump.verify_downloads(release, tmp_path, _content_fetch(served))
    assert len(problems) == len(omp_bump.TARGETS)
    assert all("the build host publishes" in problem for problem in problems)


def test_two_downloads_that_differ_from_each_other_are_reported(tmp_path: Path) -> None:
    release = omp_bump.fetch_release(_answers())
    counter: dict[str, int] = {}

    def flaky(url: str, destination: Path) -> None:
        counter[url] = counter.get(url, 0) + 1
        destination.write_bytes(f"{url} #{counter[url]}".encode())

    _, problems = omp_bump.verify_downloads(release, tmp_path, flaky)
    assert len(problems) == len(omp_bump.TARGETS)
    assert all("two downloads hash differently" in problem for problem in problems)


class Scenario:
    def __init__(self, tmp_path: Path, candidate_version: str = "18.13.2") -> None:
        (tmp_path / "candidate").mkdir()
        candidate, _ = _fake(tmp_path / "candidate", lambda data: data.update(version=f"omp/{candidate_version}"))
        previous = tmp_path / "previous-omp"
        previous.write_text(PREVIOUS_SCRIPT.format(python=sys.executable), encoding="utf-8")
        self.candidate_bytes = candidate.read_bytes()
        self.previous_bytes = previous.read_bytes()
        self.candidate_digest = hashlib.sha256(self.candidate_bytes).hexdigest()
        self.previous_pin = omp_pin.Pin(
            "18.12.1",
            {
                target: omp_pin.Build(
                    f"https://downloads.example.invalid/{target}/old", hashlib.sha256(self.previous_bytes).hexdigest()
                )
                for target in omp_bump.TARGETS
            },
        )
        self.policy = omp_bump.replace_pin(POLICY, self.previous_pin)
        self.cache = tmp_path / "cache"

    def answers(self) -> Callable[[str], Any]:
        def pin_hashes(target: str, answer: dict[str, Any]) -> None:
            for item in answer["build"]["files"]:
                if item["name"] in {name for _, name in omp_bump.TARGETS.values()}:
                    item["sha256"] = self.candidate_digest

        return _answers(pin_hashes)

    def fetch(self, url: str, destination: Path) -> None:
        old = url.startswith("https://downloads.example.invalid/")
        destination.write_bytes(self.previous_bytes if old else self.candidate_bytes)

    def prepare(self, runs: dict[str, Any] | None = None, **kwargs: Any) -> omp_bump.Outcome:
        return omp_bump.prepare(
            self.policy,
            getter=self.answers(),
            fetch=self.fetch,
            chain_factory=lambda *_: _chain_from(runs if runs is not None else _good_runs()),
            cache_root=self.cache,
            **kwargs,
        )


def test_a_newer_release_that_passes_the_suite_becomes_a_pin_edit_and_an_evidenced_body(tmp_path: Path) -> None:
    scenario = Scenario(tmp_path)
    outcome = scenario.prepare()
    assert outcome.state == omp_bump.READY, outcome.detail
    assert omp_pin.pin_from_text(outcome.policy_text).version == "18.13.2"
    assert omp_pin.pin_from_text(outcome.policy_text).builds["linux-x64"].sha256 == scenario.candidate_digest
    assert outcome.policy_text.replace("18.13.2", "18.12.1").count("omp:") == 1
    body = outcome.body
    assert "from 18.12.1 to 18.13.2" in body
    assert "downloaded twice over TLS" in body
    for target in omp_bump.TARGETS:
        assert f"| {target} | `{scenario.candidate_digest}` |" in body
    assert "`--version` on " in body and "omp/18.13.2" in body
    assert "result: partial" in body
    assert "-      --old-flag=<value>   removed later" in body
    assert "make omp-candidate-check VERSION=18.13.2" in body
    assert "the oracle reviewing this pull request runs the previous build and reads only the YAML" in body


def test_a_release_older_than_the_pin_opens_nothing(tmp_path: Path) -> None:
    scenario = Scenario(tmp_path)
    scenario.policy = omp_bump.replace_pin(POLICY, omp_pin.Pin("19.0.0", omp_pin.pin_from_text(POLICY).builds))
    outcome = scenario.prepare()
    assert outcome.state == omp_bump.CURRENT and outcome.policy_text == ""


def test_the_pin_that_already_matches_the_latest_release_opens_nothing(tmp_path: Path) -> None:
    scenario = Scenario(tmp_path)
    scenario.policy = omp_bump.replace_pin(POLICY, omp_bump.fetch_release(scenario.answers()).pin())
    outcome = scenario.prepare()
    assert outcome.state == omp_bump.CURRENT and outcome.policy_text == ""


def test_downloads_that_disagree_with_the_build_host_stop_the_bump_before_any_probe_runs(tmp_path: Path) -> None:
    scenario = Scenario(tmp_path)
    started: list[bool] = []

    def factory(*_: Any) -> Any:
        started.append(True)
        return _chain_from(_good_runs())

    def tampered(url: str, destination: Path) -> None:
        destination.write_bytes(b"tampered" if "stencil" in url else scenario.previous_bytes)

    outcome = omp_bump.prepare(
        scenario.policy, getter=scenario.answers(), fetch=tampered, chain_factory=factory, cache_root=scenario.cache
    )
    assert outcome.state == omp_bump.RED and "the build host publishes" in outcome.detail
    assert started == [] and outcome.policy_text == ""


def test_a_candidate_that_fails_the_suite_opens_no_pull_request(tmp_path: Path) -> None:
    scenario = Scenario(tmp_path, candidate_version="18.13.9")
    outcome = scenario.prepare()
    assert outcome.state == omp_bump.RED
    assert "FAIL version-format" in outcome.detail and "reports 18.13.9, expected 18.13.2" in outcome.detail
    assert outcome.policy_text == "" and outcome.body == ""


def test_a_live_contract_breach_in_the_suite_opens_no_pull_request(tmp_path: Path) -> None:
    scenario = Scenario(tmp_path)
    runs = _good_runs()
    runs["review"] = replace(runs["review"], canary_hits=("/canary",))
    outcome = scenario.prepare(runs)
    assert outcome.state == omp_bump.RED and "FAIL live-overlay-behaviour" in outcome.detail


def test_the_evidence_line_the_check_prints_is_the_line_pr_arm_accepts(tmp_path: Path) -> None:
    scenario = Scenario(tmp_path)
    outcome = scenario.prepare()
    assert omp_candidate.EVIDENCE_PREFIX not in outcome.body
    binary, _ = _fake(tmp_path)
    digest = omp_pin.sha256_of(binary)
    pin = omp_pin.Pin("18.12.1", {HOST: omp_pin.Build("https://downloads.example.invalid/omp", digest)})
    report = omp_candidate.check_candidate(
        pin,
        fetch=lambda url, destination: destination.write_bytes(binary.read_bytes()),
        chain_factory=lambda *_: _chain_from(_good_runs()),
    )
    assert report.evidence_line is None
    line = replace(report, head="b" * 40).evidence_line
    assert line is not None
    match = omp_bump_gate.EVIDENCE_LINE.fullmatch(line)
    assert match is not None
    assert (match["version"], match["platform"], match["sha256"], match["head"]) == ("18.12.1", HOST, digest, "b" * 40)
    assert line.endswith("result=releasable")


def test_the_check_binds_the_line_to_a_clean_head_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    def git(*args: str) -> None:
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@e", *args], cwd=tmp_path, check=True)

    git("init", "-q")
    (tmp_path / "f").write_text("x", encoding="utf-8")
    git("add", "f")
    git("commit", "-q", "-m", "c")
    monkeypatch.chdir(tmp_path)
    assert (
        omp_candidate.clean_head()
        == subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, encoding="utf-8", check=True
        ).stdout.strip()
    )
    (tmp_path / "f").write_text("y", encoding="utf-8")
    assert omp_candidate.clean_head() == ""


def test_a_partial_or_red_report_prints_no_evidence_line(tmp_path: Path) -> None:
    binary, _ = _fake(tmp_path)
    digest = omp_pin.sha256_of(binary)
    pin = omp_pin.Pin(
        "18.12.1",
        {
            HOST: omp_pin.Build("https://downloads.example.invalid/omp", digest),
            "linux-arm64": omp_pin.Build("https://downloads.example.invalid/x", digest),
        },
    )
    fetch = lambda url, destination: destination.write_bytes(binary.read_bytes())
    partial = omp_candidate.check_candidate(
        pin, targets=[HOST], fetch=fetch, chain_factory=lambda *_: _chain_from(_good_runs())
    )
    assert partial.evidence_line is None and omp_candidate.EVIDENCE_PREFIX not in partial.render()
    green = omp_candidate.check_candidate(pin, fetch=fetch, chain_factory=lambda *_: _chain_from(_good_runs()))
    assert green.releasable and green.evidence_line is None
    assert (
        replace(green, head="b" * 40).evidence_line
        == f"{omp_candidate.EVIDENCE_PREFIX} version=18.12.1 platform={HOST} sha256={digest} pin={omp_pin.pin_digest(pin)} head={'b' * 40} result=releasable"
    )


def test_help_diff_refuses_to_execute_a_download_that_does_not_hash_to_the_published_build(tmp_path: Path) -> None:
    binary, _ = _fake(tmp_path)
    before = binary.stat().st_mode
    with pytest.raises(omp_bump.BumpError, match="does not hash to the published build"):
        omp_bump.help_diff(binary, binary, tmp_path, "0" * 64)
    assert binary.stat().st_mode == before


def test_help_diff_makes_a_hash_verified_download_executable(tmp_path: Path) -> None:
    binary, _ = _fake(tmp_path)
    candidate = tmp_path / "download"
    candidate.write_bytes(binary.read_bytes())
    candidate.chmod(0o644)
    omp_bump.help_diff(binary, candidate, tmp_path, omp_pin.sha256_of(candidate))
    assert candidate.stat().st_mode & 0o111
