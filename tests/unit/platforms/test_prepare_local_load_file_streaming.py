from __future__ import annotations

import bz2
import gzip
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from benchbox.platforms.base import data_loading
from benchbox.platforms.base.data_loading import CsvDialect, prepare_local_load_file

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _dialect(delimiter: str = "|", normalize_booleans: bool = False) -> CsvDialect:
    return CsvDialect(
        delimiter=delimiter,
        has_header=False,
        null_marker="",
        normalize_booleans=normalize_booleans,
        quote=None,
    )


def _reference_rewrite(raw: bytes, delim: str, strip_trailing_delim: bool, normalize_booleans: bool) -> bytes:
    out: list[str] = []
    for line in _universal_lines(raw.decode("utf-8")):
        line = line.rstrip("\n").rstrip("\r")
        if not line:
            continue
        if strip_trailing_delim and line.endswith(delim):
            line = line[: -len(delim)]
        if normalize_booleans:
            fields = line.split(delim)
            fields = ["1" if f == "True" else "0" if f == "False" else f for f in fields]
            line = delim.join(fields)
        out.append(line + "\n")
    return "".join(out).encode("utf-8")


def _universal_lines(text: str) -> list[str]:
    return text.replace("\r\n", "\n").replace("\r", "\n").splitlines(keepends=True)


def _run(path: Path, **kwargs) -> bytes:
    with prepare_local_load_file(path, **kwargs) as load_path:
        return Path(load_path).read_bytes()


def _lineitem_like(rows: int, seed: int = 7) -> bytes:
    rng = random.Random(seed)
    lines = []
    for i in range(rows):
        lines.append(
            "|".join(
                [
                    str(i + 1),
                    str(rng.randint(1, 200000)),
                    str(rng.randint(1, 10000)),
                    str(rng.randint(1, 7)),
                    f"{rng.uniform(1, 50):.2f}",
                    f"{rng.uniform(900, 100000):.2f}",
                    f"{rng.uniform(0, 0.1):.2f}",
                    "1996-03-13",
                    "N",
                    "O",
                    "deliver in person é中",
                    "True" if i % 3 == 0 else "False",
                    "",
                ]
            )
            + "|"
        )
    return ("\n".join(lines) + "\n").encode("utf-8")


CASES = {
    "plain": b"1|a|b|\n2|c|d|\n",
    "no_trailing_newline": b"1|a|b|\n2|c|d|",
    "crlf": b"1|a|b|\r\n2|c|d|\r\n",
    "crlf_no_final": b"1|a|b|\r\n2|c|d|",
    "lone_cr": b"1|a|b|\r2|c|d|\r",
    "empty_lines": b"\n\n1|a|b|\n\n\n2|c|d|\n\n",
    "only_delim_line": b"|\n1|a|\n",
    "double_delim": b"1|a||\n2|b|||\n",
    "no_trailing_delim": b"1|a|b\n2|c|d\n",
    "multibyte": "1|é|中文|\n2|日本語|ñ|\n".encode(),
    "bool_fields": b"True|False|x|True\nFalse|True|TrueX|XFalse\nTrue\nFalse\n",
    "bool_with_trailing": b"1|True|False|\n2|False|True|\n",
    "bool_crlf": b"True|False\r\nFalse|True\r\n",
    "empty": b"",
    "blank_only": b"\n\r\n\n",
}


@pytest.mark.parametrize("chunk", [1, 2, 3, 5, 7, 16, 1 << 20])
@pytest.mark.parametrize("name", sorted(CASES))
@pytest.mark.parametrize("strip,booleans", [(False, False), (True, False), (False, True), (True, True)])
def test_matches_reference_across_chunk_sizes(
    tmp_path: Path, monkeypatch, name: str, chunk: int, strip: bool, booleans: bool
) -> None:
    monkeypatch.setattr(data_loading, "_REWRITE_CHUNK_BYTES", chunk)
    raw = CASES[name]
    if not (strip or booleans):
        data_file = tmp_path / "t.csv.gz"
        with gzip.open(data_file, "wb") as f:
            f.write(raw)
    else:
        data_file = tmp_path / "t.tbl"
        data_file.write_bytes(raw)
    expected = _reference_rewrite(raw, "|", strip, booleans)
    actual = _run(data_file, dialect=_dialect(normalize_booleans=booleans), strip_trailing_delim=strip)
    assert actual == expected


@pytest.mark.parametrize("chunk", [1, 2, 3, 4, 5, 64])
def test_multi_char_delimiter_matches_reference(tmp_path: Path, monkeypatch, chunk: int) -> None:
    monkeypatch.setattr(data_loading, "_REWRITE_CHUNK_BYTES", chunk)
    raw = b"1||True||x||\n2||False||y||\n\n3||True||"
    data_file = tmp_path / "t.tbl"
    data_file.write_bytes(raw)
    expected = _reference_rewrite(raw, "||", True, True)
    assert _run(data_file, dialect=_dialect("||", True), strip_trailing_delim=True) == expected


@pytest.mark.parametrize("compression", ["gz", "bz2", "zst"])
@pytest.mark.parametrize("chunk", [3, 1 << 20])
def test_compressed_inputs_match_reference(tmp_path: Path, monkeypatch, compression: str, chunk: int) -> None:
    if compression == "zst" and shutil.which("zstd") is None:
        pytest.skip("zstd CLI not available")
    monkeypatch.setattr(data_loading, "_REWRITE_CHUNK_BYTES", chunk)
    raw = CASES["bool_with_trailing"] + CASES["multibyte"] + CASES["crlf"]
    plain = tmp_path / "t.tbl"
    plain.write_bytes(raw)
    data_file = tmp_path / f"t.tbl.{compression}"
    if compression == "gz":
        data_file.write_bytes(gzip.compress(raw))
    elif compression == "bz2":
        data_file.write_bytes(bz2.compress(raw))
    else:
        subprocess.run(["zstd", "-q", "-f", str(plain), "-o", str(data_file)], check=True)
    expected = _reference_rewrite(raw, "|", True, True)
    assert _run(data_file, dialect=_dialect(normalize_booleans=True), strip_trailing_delim=True) == expected


def test_lineitem_like_row_count_and_checksum_parity(tmp_path: Path) -> None:
    raw = _lineitem_like(5000)
    data_file = tmp_path / "lineitem.tbl"
    data_file.write_bytes(raw)
    expected = _reference_rewrite(raw, "|", True, True)
    actual = _run(data_file, dialect=_dialect(normalize_booleans=True), strip_trailing_delim=True)
    assert actual.count(b"\n") == expected.count(b"\n") == 5000
    assert actual == expected
    assert b"True" not in actual and b"False" not in actual


def test_zstd_failure_surfaces_error_and_cleans_temp(tmp_path: Path) -> None:
    if shutil.which("zstd") is None:
        pytest.skip("zstd CLI not available")
    data_file = tmp_path / "bad.tbl.zst"
    data_file.write_bytes(b"not zstd data")
    with pytest.raises(subprocess.CalledProcessError):
        with prepare_local_load_file(data_file, dialect=_dialect(), strip_trailing_delim=True):
            pass
    assert [p.name for p in tmp_path.iterdir()] == ["bad.tbl.zst"]


def test_no_per_line_python_loop_in_rewrite(tmp_path: Path, monkeypatch) -> None:
    raw = _lineitem_like(2000)
    data_file = tmp_path / "lineitem.tbl"
    data_file.write_bytes(raw)
    calls = []
    original = data_loading._normalize_segment

    def spy(*args, **kwargs):
        calls.append(len(args[0]))
        return original(*args, **kwargs)

    monkeypatch.setattr(data_loading, "_normalize_segment", spy)
    _run(data_file, dialect=_dialect(normalize_booleans=True), strip_trailing_delim=True)
    assert len(calls) == 1


def test_normalize_line_endings_rewrites_plain_file(tmp_path: Path) -> None:
    data_file = tmp_path / "events.csv"
    data_file.write_bytes(b"1|a\r\n\r\n2|b\r\n")

    with prepare_local_load_file(
        data_file, dialect=_dialect(), strip_trailing_delim=False, normalize_line_endings=True
    ) as prepared:
        assert prepared != data_file
        assert prepared.read_bytes() == b"1|a\n2|b\n"

    with prepare_local_load_file(data_file, dialect=_dialect(), strip_trailing_delim=False) as untouched:
        assert untouched == data_file


@pytest.mark.parametrize("chunk", [1, 3, 8, 1 << 20])
def test_blank_null_text_matches_per_field_reference(tmp_path: Path, monkeypatch, chunk: int) -> None:
    monkeypatch.setattr(data_loading, "_REWRITE_CHUNK_BYTES", chunk)
    raw = b"1|NULL|x|\n2|null|Null|\n3||nullable|\n4|nul|NULLS|\n5|NULL|NULL|\r\nnull|a|b|"
    expected = []
    for line in raw.replace(b"\r\n", b"\n").split(b"\n"):
        if not line:
            continue
        fields = [b"" if f.lower() == b"null" else f for f in line.split(b"|")]
        expected.append(b"|".join(fields))
    data_file = tmp_path / "t.tbl"
    data_file.write_bytes(raw)

    with prepare_local_load_file(
        data_file, dialect=_dialect(), strip_trailing_delim=False, blank_null_text=True
    ) as out:
        assert out.read_bytes() == b"\n".join(expected) + b"\n"
