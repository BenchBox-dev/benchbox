import subprocess
import sys
from pathlib import Path

import pytest

from benchbox.core.tpcds.c_tools import DSQGenBinary, TPCDSError

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _fake_completed(stdout: str = "SELECT 1\n"):
    class _CP:
        def __init__(self):
            self.returncode = 0
            self.stdout = stdout
            self.stderr = ""

    return _CP()


def test_dsqgen_uses_main_directory_for_variants(tmp_path, monkeypatch):
    templates_dir = tmp_path / "query_templates"
    variants_dir = tmp_path / "query_variants"
    templates_dir.mkdir(parents=True)
    variants_dir.mkdir(parents=True)

    (templates_dir / "templates.lst").write_text("\n")
    (templates_dir / "ansi.tpl").write_text("-- dialect stub\n")

    (templates_dir / "query1.tpl").write_text("-- base query 1\nSELECT 1;\n")
    (variants_dir / "query1a.tpl").write_text("-- variant query 1a\nSELECT 1;\n")

    dsq = DSQGenBinary()
    dsq.templates_dir = templates_dir

    captured_cmd: list[str] = []

    def fake_run(
        cmd,
        cwd=None,
        env=None,
        capture_output=None,
        text=None,
        timeout=None,
        check=None,
        stdout=None,
        stderr=None,
    ):
        nonlocal captured_cmd
        captured_cmd = list(cmd)
        return _fake_completed(stdout="SELECT 1;\n")

    monkeypatch.setattr("subprocess.run", fake_run)

    sql = dsq.generate("1a", seed=22, scale_factor=1.0, dialect="ansi")

    _opt = "/" if sys.platform == "win32" else "-"

    assert f"{_opt}DIRECTORY" in captured_cmd
    dir_idx = captured_cmd.index(f"{_opt}DIRECTORY") + 1
    tmpl_dir = Path(captured_cmd[dir_idx])
    assert tmpl_dir.name == "q"

    assert f"{_opt}TEMPLATE" in captured_cmd
    tpl_idx = captured_cmd.index(f"{_opt}TEMPLATE") + 1
    tpl_arg = captured_cmd[tpl_idx]
    assert tpl_arg == "../query_variants/query1a.tpl"

    assert "select" in sql.lower()


def test_validate_query_id_accepts_variant_in_query_variants(tmp_path):
    templates_dir = tmp_path / "query_templates"
    variants_dir = tmp_path / "query_variants"
    templates_dir.mkdir(parents=True)
    variants_dir.mkdir(parents=True)

    (templates_dir / "templates.lst").write_text("\n")
    (templates_dir / "ansi.tpl").write_text("-- dialect\n")
    (templates_dir / "query14.tpl").write_text("-- base 14\n")
    (variants_dir / "query14a.tpl").write_text("-- variant 14a\n")

    dsq = DSQGenBinary()
    dsq.templates_dir = templates_dir

    assert dsq.validate_query_id("14a") is True


_CITIES_OVERRUN_STDERR = (
    "Runtime ERROR: Distribution over-run/under-run\n"
    "Check distribution definitions and usage for cities.\n"
    "index = -1, length=1000."
)


def _single_query_binary(tmp_path):
    templates_dir = tmp_path / "query_templates"
    templates_dir.mkdir(parents=True)
    (templates_dir / "templates.lst").write_text("\n")
    (templates_dir / "netezza.tpl").write_text("-- dialect stub\n")
    (templates_dir / "query46.tpl").write_text("-- base query 46\nSELECT 1;\n")
    dsq = DSQGenBinary()
    dsq.templates_dir = templates_dir
    return dsq


def _completed(returncode: int, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(args=["dsqgen"], returncode=returncode, stdout=stdout, stderr=stderr)


def test_windows_cities_overrun_is_retried_for_a_single_query(tmp_path, monkeypatch):
    dsq = _single_query_binary(tmp_path)
    results = [_completed(1, stderr=_CITIES_OVERRUN_STDERR), _completed(0, stdout="SELECT 46;\n")]

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: results.pop(0))

    sql = dsq.generate(46, seed=1, scale_factor=1.0, dialect="netezza")

    assert "select 46" in sql.lower()
    assert not results


def test_non_windows_cities_overrun_is_not_retried_for_a_single_query(tmp_path, monkeypatch):
    dsq = _single_query_binary(tmp_path)
    calls = 0

    def overrun(*args, **kwargs):
        nonlocal calls
        calls += 1
        return _completed(1, stderr=_CITIES_OVERRUN_STDERR)

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(subprocess, "run", overrun)

    with pytest.raises(TPCDSError, match="dsqgen failed for query 46"):
        dsq.generate(46, seed=1, scale_factor=1.0, dialect="netezza")

    assert calls == 1


def test_persistent_windows_cities_overrun_for_a_single_query_stops_at_the_cap(tmp_path, monkeypatch):
    import benchbox.core.tpcds.c_tools as c_tools

    dsq = _single_query_binary(tmp_path)
    calls = 0

    def overrun(*args, **kwargs):
        nonlocal calls
        calls += 1
        return _completed(1, stderr=_CITIES_OVERRUN_STDERR)

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(c_tools, "_WINDOWS_DSQGEN_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(subprocess, "run", overrun)

    with pytest.raises(TPCDSError, match="Distribution over-run/under-run"):
        dsq.generate(46, seed=1, scale_factor=1.0, dialect="netezza")

    assert calls == 3
