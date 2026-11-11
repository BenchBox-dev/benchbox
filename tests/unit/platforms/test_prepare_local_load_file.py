from __future__ import annotations

import gzip
import subprocess
import tempfile
from pathlib import Path

import pytest

from benchbox.platforms.base.data_loading import CsvDialect, prepare_local_load_file

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _dialect(
    delimiter: str = "|",
    has_header: bool = False,
    null_marker: str | None = "",
    normalize_booleans: bool = False,
    quote: str | None = None,
) -> CsvDialect:
    return CsvDialect(
        delimiter=delimiter,
        has_header=has_header,
        null_marker=null_marker,
        normalize_booleans=normalize_booleans,
        quote=quote,
    )


def _write_plain(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_gzip(path: Path, lines: list[str]) -> None:
    content = ("\n".join(lines) + "\n").encode()
    with gzip.open(path, "wb") as f:
        f.write(content)


def _write_zstd(path: Path, lines: list[str]) -> None:

    content = ("\n".join(lines) + "\n").encode()
    with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as tmp:
        tmp.write(content)
        tmp_name = tmp.name
    subprocess.run(["zstd", "-f", tmp_name, "-o", str(path)], check=True, capture_output=True)
    Path(tmp_name).unlink(missing_ok=True)


def test_uncompressed_passthrough_yields_original_path(tmp_path: Path) -> None:

    data_file = tmp_path / "lineitem.csv"
    _write_plain(data_file, ["1,a,b", "2,c,d"])

    with prepare_local_load_file(data_file, dialect=_dialect(delimiter=","), strip_trailing_delim=False) as result:
        assert result == data_file


def test_passthrough_no_temp_file_created(tmp_path: Path) -> None:

    data_file = tmp_path / "lineitem.csv"
    _write_plain(data_file, ["1,2,3"])
    before = set(tmp_path.iterdir())

    with prepare_local_load_file(data_file, dialect=_dialect(delimiter=","), strip_trailing_delim=False):
        assert set(tmp_path.iterdir()) == before


def test_gzip_decompresses_correctly(tmp_path: Path) -> None:

    data_file = tmp_path / "lineitem.tbl.gz"
    _write_gzip(data_file, ["1|a|b|", "2|c|d|"])

    with prepare_local_load_file(data_file, dialect=_dialect(), strip_trailing_delim=False) as result:
        content = result.read_text(encoding="utf-8")

    assert "1|a|b|" in content
    assert "2|c|d|" in content


@pytest.mark.slow
def test_zstd_decompresses_correctly(tmp_path: Path) -> None:

    pytest.importorskip("subprocess")
    data_file = tmp_path / "lineitem.tbl.zst"
    _write_zstd(data_file, ["1|a|b|", "2|c|d|"])

    with prepare_local_load_file(data_file, dialect=_dialect(), strip_trailing_delim=False) as result:
        content = result.read_text(encoding="utf-8")

    assert "1|a|b|" in content
    assert "2|c|d|" in content


def test_trailing_pipe_stripped_when_requested(tmp_path: Path) -> None:

    data_file = tmp_path / "lineitem.tbl"
    _write_plain(data_file, ["1|a|b|", "2|c|d|"])

    with prepare_local_load_file(data_file, dialect=_dialect(), strip_trailing_delim=True) as result:
        lines = result.read_text(encoding="utf-8").splitlines()

    assert lines == ["1|a|b", "2|c|d"]


def test_trailing_pipe_not_stripped_when_not_requested(tmp_path: Path) -> None:

    data_file = tmp_path / "lineitem.tbl"
    _write_plain(data_file, ["1|a|b|", "2|c|d|"])

    with prepare_local_load_file(data_file, dialect=_dialect(), strip_trailing_delim=False) as result:
        lines = result.read_text(encoding="utf-8").splitlines()

    assert lines == ["1|a|b|", "2|c|d|"]


def test_trailing_strip_with_no_trailing_delim_is_safe(tmp_path: Path) -> None:

    data_file = tmp_path / "data.dat"
    _write_plain(data_file, ["1|a|b", "2|c|"])

    with prepare_local_load_file(data_file, dialect=_dialect(), strip_trailing_delim=True) as result:
        lines = result.read_text(encoding="utf-8").splitlines()

    assert lines[0] == "1|a|b"
    assert lines[1] == "2|c"


def test_boolean_normalize_rewrites_true_false(tmp_path: Path) -> None:

    data_file = tmp_path / "dbo_dimaccount.csv"
    _write_plain(data_file, ["1,True,some text", "2,False,other"])

    with prepare_local_load_file(
        data_file, dialect=_dialect(delimiter=",", normalize_booleans=True), strip_trailing_delim=False
    ) as result:
        lines = result.read_text(encoding="utf-8").splitlines()

    assert lines == ["1,1,some text", "2,0,other"]


def test_boolean_normalize_off_preserves_true_false(tmp_path: Path) -> None:

    data_file = tmp_path / "dbo_dimaccount.csv"
    _write_plain(data_file, ["1,True,some text"])

    with prepare_local_load_file(
        data_file, dialect=_dialect(delimiter=",", normalize_booleans=False), strip_trailing_delim=False
    ) as result:
        content = result.read_text(encoding="utf-8")

    assert "True" in content


def test_boolean_normalize_does_not_affect_partial_matches(tmp_path: Path) -> None:

    data_file = tmp_path / "data.csv"
    _write_plain(data_file, ["TrueValue,FalseAlarm,True,False"])

    with prepare_local_load_file(
        data_file, dialect=_dialect(delimiter=",", normalize_booleans=True), strip_trailing_delim=False
    ) as result:
        lines = result.read_text(encoding="utf-8").splitlines()

    assert lines == ["TrueValue,FalseAlarm,1,0"]


def test_temp_file_cleaned_up_after_context_exit(tmp_path: Path) -> None:

    data_file = tmp_path / "lineitem.tbl"
    _write_plain(data_file, ["1|a|b|"])

    tmp_file_path: Path | None = None
    with prepare_local_load_file(data_file, dialect=_dialect(), strip_trailing_delim=True) as result:
        tmp_file_path = result
        assert result != data_file
        assert result.exists()

    assert tmp_file_path is not None
    assert not tmp_file_path.exists()


def test_temp_file_cleaned_up_after_exception(tmp_path: Path) -> None:

    data_file = tmp_path / "lineitem.tbl"
    _write_plain(data_file, ["1|a|b|"])

    tmp_file_path: Path | None = None
    with pytest.raises(RuntimeError):
        with prepare_local_load_file(data_file, dialect=_dialect(), strip_trailing_delim=True) as result:
            tmp_file_path = result
            raise RuntimeError("simulated load failure")

    assert tmp_file_path is not None
    assert not tmp_file_path.exists()


