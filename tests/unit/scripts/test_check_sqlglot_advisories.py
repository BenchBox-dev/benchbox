from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from scripts import check_sqlglot_advisories as advisories
from scripts.check_sqlglot_advisories import check, evaluate, main, query_osv, upstream_version

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[3]
PIN = {"version": "30.24.0.13"}


def make_root(tmp_path: Path, version: str = "30.24.0.13") -> Path:
    pin = tmp_path / "benchbox" / "_vendor" / "sqlglot.json"
    pin.parent.mkdir(parents=True)
    pin.write_text(json.dumps({"version": version}), encoding="utf-8")
    return tmp_path


def test_upstream_version_drops_the_fork_revision() -> None:
    assert upstream_version(PIN) == "30.24.0"


@pytest.mark.parametrize("version", ["30.24.0", "30.24", "x.y.z.1", None, "30.24.0.13.1"])
def test_unparseable_version_is_rejected(version) -> None:
    with pytest.raises(ValueError, match="fork revision"):
        upstream_version({"version": version})


def test_committed_pin_maps_to_its_wheel_and_upstream_version() -> None:
    pin = json.loads((REPO_ROOT / "benchbox/_vendor/sqlglot.json").read_text(encoding="utf-8"))
    version = upstream_version(pin)
    assert pin["version"].startswith(f"{version}.")
    assert pin["wheel"] == f"benchbox_sqlglot-{pin['version']}-py3-none-any.whl"


def test_clean_version_passes(tmp_path: Path) -> None:
    assert check(make_root(tmp_path), lambda version: []) == []


def test_matching_advisory_fails_naming_id_aliases_and_version(tmp_path: Path) -> None:
    seen: list[str] = []

    def querier(version: str):
        seen.append(version)
        return [{"id": "GHSA-xxxx", "aliases": ["CVE-2099-1"], "summary": "parser DoS"}]

    (problem,) = check(make_root(tmp_path), querier)
    assert seen == ["30.24.0"]
    assert "GHSA-xxxx" in problem and "CVE-2099-1" in problem and "30.24.0" in problem and "parser DoS" in problem


def test_each_advisory_is_reported() -> None:
    assert len(evaluate(PIN, [{"id": "A"}, {"id": "B"}])) == 2


def test_unreachable_database_fails(tmp_path: Path) -> None:
    def querier(version: str):
        raise RuntimeError("cannot query OSV advisories: offline")

    (problem,) = check(make_root(tmp_path), querier)
    assert "offline" in problem


def test_malformed_pin_version_fails(tmp_path: Path) -> None:
    (problem,) = check(make_root(tmp_path, "30.24.0"), lambda version: [])
    assert "fork revision" in problem


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_query_osv_posts_package_and_version_and_follows_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    bodies: list[dict] = []
    pages = [{"vulns": [{"id": "A"}], "next_page_token": "t"}, {"vulns": [{"id": "B"}]}]

    def fake_urlopen(request, timeout):
        bodies.append(json.loads(request.data))
        return FakeResponse(json.dumps(pages[len(bodies) - 1]).encode())

    monkeypatch.setattr(advisories.urllib.request, "urlopen", fake_urlopen)
    assert [v["id"] for v in query_osv("30.24.0")] == ["A", "B"]
    assert bodies[0] == {"package": {"name": "sqlglot", "ecosystem": "PyPI"}, "version": "30.24.0"}
    assert bodies[1]["page_token"] == "t"


def test_query_osv_wraps_network_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request, timeout):
        raise advisories.urllib.error.URLError("down")

    monkeypatch.setattr(advisories.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(RuntimeError, match="cannot query OSV"):
        query_osv("30.24.0")


def test_main_exit_codes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    root = make_root(tmp_path)
    monkeypatch.setattr(advisories, "query_osv", lambda version: [])
    assert main(["--root", str(root)]) == 0
    monkeypatch.setattr(advisories, "query_osv", lambda version: [{"id": "GHSA-xxxx"}])
    assert main(["--root", str(root)]) == 1
    assert "GHSA-xxxx" in capsys.readouterr().err


def test_main_default_root_reads_committed_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr(advisories, "query_osv", lambda version: seen.append(version) or [])
    assert main([]) == 0
    assert len(seen[0].split(".")) == 3
