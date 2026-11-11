from __future__ import annotations

from pathlib import Path

import pytest

from benchbox.core.expected_results import loader
from benchbox.core.expected_results.loader import (
    TpcdsAnswerBlock,
    load_tpcds_answer_values,
    parse_tpcds_answer_values,
)

pytestmark = [pytest.mark.unit, pytest.mark.medium]


def _write(tmp_path: Path, name: str, text: str, encoding: str = "utf-8") -> Path:
    path = tmp_path / name
    path.write_bytes(text.encode(encoding))
    return path


def _fixed(header, rows, widths, trailer=""):
    def line(cells):
        return " ".join(f"{cell:<{width}}" for cell, width in zip(cells, widths)).rstrip()

    return "\n".join([line(header), " ".join("-" * width for width in widths), *(line(row) for row in rows)]) + trailer


def test_a_fixed_width_file_is_cut_at_the_columns_of_its_separator(tmp_path):
    rows = [("1998", "2001001", "amalgimporto #1", "45162.45"), ("1998", "5003001", "exportischolar #1", "40600.56")]
    text = _fixed(("D_YEAR", "BRAND_ID", "BRAND", "EXT_PRICE"), rows, (6, 10, 18, 10), "\n\n2 rows selected.\n")

    (block,) = parse_tpcds_answer_values(_write(tmp_path, "3.ans", text))

    assert block.columns == ("D_YEAR", "BRAND_ID", "BRAND", "EXT_PRICE")
    assert block.rows == tuple(rows)


def test_tabs_expand_to_eight_column_stops(tmp_path):
    text = "A       B\n------- -------\nx\t1\ny\t2\n"

    (block,) = parse_tpcds_answer_values(_write(tmp_path, "t.ans", text))

    assert block.rows == (("x", "1"), ("y", "2"))


def test_a_nulls_last_file_reads_an_empty_cell_as_null(tmp_path):
    text = _fixed(("CHANNEL", "I_BRAND_ID", "TOTAL"), [("catalog", "1001001", "10"), ("catalog", "", "20")], (7, 10, 5))

    (block,) = parse_tpcds_answer_values(_write(tmp_path, "14_NULLS_LAST.ans", text))

    assert block.rows == (("catalog", "1001001", "10"), ("catalog", None, "20"))


def test_a_nulls_first_file_with_pipes_and_a_separator(tmp_path):
    text = "CHANNEL|I_BRAND_ID|TOTAL\n-------|----------|-----\n       |\t  |  674\ncatalog|   1001001|   10\n"

    (block,) = parse_tpcds_answer_values(_write(tmp_path, "14_NULLS_FIRST.ans", text))

    assert block.columns == ("CHANNEL", "I_BRAND_ID", "TOTAL")
    assert block.rows == ((None, None, "674"), ("catalog", "1001001", "10"))


def test_a_headerless_pipe_file_takes_its_first_line_as_the_header(tmp_path):
    text = "INSERT 0 6\nCHANNEL|ID|SALES\n||113.65\ncatalog channel||38.57\n(2 rows)\n"

    (block,) = parse_tpcds_answer_values(_write(tmp_path, "5_NULLS_FIRST.ans", text))

    assert block.columns == ("CHANNEL", "ID", "SALES")
    assert block.rows == ((None, None, "113.65"), ("catalog channel", None, "38.57"))


def test_a_file_with_two_result_sets_yields_two_blocks(tmp_path):
    text = _fixed(("AA", "BB"), [("1", "2")], (2, 2), "\n\n100 rows selected.\n\n") + _fixed(
        ("CC", "DD", "EE"), [("3", "4", "5")], (2, 2, 2)
    )

    first, second = parse_tpcds_answer_values(_write(tmp_path, "14.ans", text))

    assert first == TpcdsAnswerBlock(("AA", "BB"), (("1", "2"),))
    assert second == TpcdsAnswerBlock(("CC", "DD", "EE"), (("3", "4", "5"),))


def test_a_squeezed_header_is_read_one_name_per_word(tmp_path):
    text = "PROMOTIONS TOTAL RATE_OF_PROMOTIONS_TO_TOTAL\n---------- ---------- ----------------------------\n 2894229.7 5493575.41 52.68\n"

    (block,) = parse_tpcds_answer_values(_write(tmp_path, "61.ans", text))

    assert block.columns == ("PROMOTIONS", "TOTAL", "RATE_OF_PROMOTIONS_TO_TOTAL")
    assert block.rows == (("2894229.7", "5493575.41", "52.68"),)


def test_a_latin_1_file_is_decoded(tmp_path):
    text = "NAME\n----\nCote\xe9\n"

    (block,) = parse_tpcds_answer_values(_write(tmp_path, "30.ans", text, encoding="latin-1"))

    assert block.rows == (("Cote\xe9",),)


def test_a_row_that_does_not_fit_its_columns_is_an_error(tmp_path):
    text = "AAAAA BBBBB\n----- -----\n1     2\nxxxxxxxxxxxx\n"
    text += "\n"

    with pytest.raises(ValueError, match="falls between columns"):
        parse_tpcds_answer_values(_write(tmp_path, "bad.ans", text))


def test_a_pipe_row_with_the_wrong_cell_count_is_an_error(tmp_path):
    with pytest.raises(ValueError, match="row 1 has 2 cells but the header has 3"):
        parse_tpcds_answer_values(_write(tmp_path, "bad.ans", "A|B|C\n1|2\n"))


def test_a_missing_file_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        parse_tpcds_answer_values(tmp_path / "nope.ans")


def test_the_loader_picks_the_requested_null_order(tmp_path, monkeypatch):
    _write(tmp_path, "7.ans", "AA\n--\n1\n")
    _write(tmp_path, "14_NULLS_FIRST.ans", "A|B\n1|\n")
    _write(tmp_path, "14_NULLS_LAST.ans", "AA BB\n-- --\n1  2\n")
    monkeypatch.setattr(loader, "_find_tpcds_answers_dir", lambda: tmp_path)

    first = load_tpcds_answer_values(null_order="first")
    last = load_tpcds_answer_values(null_order="last")

    assert first["7"] == last["7"]
    assert first["14"][0].rows == (("1", None),)
    assert last["14"][0].rows == (("1", "2"),)


def test_the_loader_raises_on_a_malformed_answer_file_and_names_it(tmp_path, monkeypatch):
    _write(tmp_path, "1.ans", "AA\n--\n1\n")
    _write(tmp_path, "2.ans", "A|B\n1\n")
    monkeypatch.setattr(loader, "_find_tpcds_answers_dir", lambda: tmp_path)

    with pytest.raises(ValueError, match=r"2\.ans.*row 1 has 1 cells but the header has 2"):
        load_tpcds_answer_values()


def test_a_malformed_second_result_set_is_reported_with_its_position(tmp_path):
    text = _fixed(("AA", "BB"), [("1", "2")], (2, 2), "\n\n") + "CC DD\n-- --\n3  4\nxxxxxxxxxx\n"

    with pytest.raises(ValueError, match=r"falls between columns.*result set 2 of 2, separator at line"):
        parse_tpcds_answer_values(_write(tmp_path, "14.ans", text))


def _drifting(first, second, third, paid):
    return first.ljust(10) + second.ljust(10) + third.ljust(18) + paid.rjust(10)


_DRIFT_SEPARATOR = "---------- ---------- ---------- ----------"


def test_fields_a_character_or_two_left_of_their_dashes_are_read_by_their_gaps(tmp_path):

    rows = [_drifting("Hamlin", "Heather", "able", "149.65")]
    text = "NAME       FIRST      STORE      PAID\n" + _DRIFT_SEPARATOR + "\n" + "\n".join(rows) + "\n"
    assert rows[0].index("Heather") == 10 and rows[0].index("able") == 20

    (block,) = parse_tpcds_answer_values(_write(tmp_path, "d.ans", text))

    assert block.rows == (("Hamlin", "Heather", "able", "149.65"),)


def test_a_drifting_row_with_an_empty_cell_is_an_error_not_a_guess(tmp_path):
    row = _drifting("Hamlin", "Heather", "", "149.65")
    text = "NAME       FIRST      STORE      PAID\n" + _DRIFT_SEPARATOR + "\n" + row + "\n"

    with pytest.raises(ValueError, match="falls between columns"):
        parse_tpcds_answer_values(_write(tmp_path, "d.ans", text))


def test_the_loader_raises_on_a_malformed_file_named_24(tmp_path, monkeypatch):
    _write(tmp_path, "1.ans", "AA\n--\n1\n")
    _write(tmp_path, "24.ans", "AA BB\n-- --\n1  2\nxxxxxxxxxx\n")
    monkeypatch.setattr(loader, "_find_tpcds_answers_dir", lambda: tmp_path)

    with pytest.raises(ValueError, match=r"24\.ans"):
        load_tpcds_answer_values()


def test_the_loader_rejects_another_scale_or_an_unknown_null_order():
    with pytest.raises(ValueError, match="scale factor 1.0"):
        load_tpcds_answer_values(scale_factor=10.0)
    with pytest.raises(ValueError, match="null_order"):
        load_tpcds_answer_values(null_order="middle")


_ANSWERS = Path(__file__).resolve().parents[4] / "_sources" / "tpc-ds" / "answer_sets"


@pytest.mark.skipif(not _ANSWERS.exists(), reason="official answer files are not in this checkout")
def test_every_official_answer_file_parses_to_rectangular_blocks():
    names = sorted(path.name for path in _ANSWERS.glob("*.ans"))
    assert len(names) == 129
    for name in names:
        blocks = parse_tpcds_answer_values(_ANSWERS / name)
        assert blocks, name
        for block in blocks:
            assert block.columns and block.rows, name
            assert all(len(row) == len(block.columns) for row in block.rows), name


@pytest.mark.skipif(not _ANSWERS.exists(), reason="official answer files are not in this checkout")
def test_only_24_needs_the_drift_tolerance(monkeypatch):
    monkeypatch.setattr(loader, "_MAX_COLUMN_DRIFT", 0)
    needs_tolerance = set()
    for path in sorted(_ANSWERS.glob("*.ans")):
        try:
            parse_tpcds_answer_values(path)
        except ValueError:
            needs_tolerance.add(path.name)
    assert needs_tolerance == {"24.ans"}


@pytest.mark.skipif(not _ANSWERS.exists(), reason="official answer files are not in this checkout")
def test_query_24_is_read_as_two_result_sets_of_four_columns():
    first, second = parse_tpcds_answer_values(_ANSWERS / "24.ans")

    columns = ("C_LAST_NAME", "C_FIRST_NAME", "S_STORE_NAME", "PAID")
    assert first.columns == second.columns == columns
    assert first.rows == (
        ("Martins", "Cara", "bar", "241.96"),
        ("Smallwood", "Rhonda", "bar", "3089.28"),
        ("Terry", "Sandra", "bar", "509.42"),
    )
    assert second.rows == (
        ("Hamlin", "Heather", "able", "149.65"),
        ("Martin", "Harold", "bar", "5834.88"),
        ("Nall", "Mike", "able", "999.7"),
        ("Southern", "Jeannie", "bar", "446.31"),
    )


@pytest.mark.skipif(not _ANSWERS.exists(), reason="official answer files are not in this checkout")
@pytest.mark.parametrize(
    ("name", "rows"), [("1.ans", 100), ("3.ans", 89), ("97.ans", 1), ("61.ans", 1), ("2.ans", 2513)]
)
def test_official_files_with_a_row_count_trailer_parse_to_that_many_rows(name, rows):
    assert sum(len(block.rows) for block in parse_tpcds_answer_values(_ANSWERS / name)) == rows


@pytest.mark.skipif(not _ANSWERS.exists(), reason="official answer files are not in this checkout")
@pytest.mark.parametrize("null_order", ["first", "last"])
def test_the_loader_reads_all_99_official_answer_sets(null_order):
    answers = load_tpcds_answer_values(null_order=null_order)

    assert set(answers) == {str(n) for n in range(1, 100)}


@pytest.mark.parametrize("boundary", ["\n", "(1 row)\n", "1 row selected.\n", "----- OUTPUT Query 2\n"])
def test_repeated_pipe_headers_after_result_boundaries_start_another_block(tmp_path, boundary):
    text = "A|B\n1|2\n" + boundary + "A|B\n3|4\n"

    blocks = parse_tpcds_answer_values(_write(tmp_path, "39.ans", text))

    assert blocks == (TpcdsAnswerBlock(("A", "B"), (("1", "2"),)), TpcdsAnswerBlock(("A", "B"), (("3", "4"),)))


def test_a_pipe_data_row_equal_to_the_header_without_a_boundary_is_preserved(tmp_path):
    text = "A|B\n1|2\nA|B\n3|4\n"

    assert parse_tpcds_answer_values(_write(tmp_path, "same.ans", text)) == (
        TpcdsAnswerBlock(("A", "B"), (("1", "2"), ("A", "B"), ("3", "4"))),
    )


def test_a_malformed_pipe_second_block_reports_its_result_set(tmp_path):
    text = "A|B\n1|2\n\nA|B\n3|4|5\n"

    with pytest.raises(ValueError, match=r"row 1 has 3 cells.*result set 2"):
        parse_tpcds_answer_values(_write(tmp_path, "39.ans", text))


@pytest.mark.skipif(not _ANSWERS.exists(), reason="official answer files are not in this checkout")
def test_official_query_39_separates_243_first_statement_rows_and_14_second_statement_rows():
    first, second = parse_tpcds_answer_values(_ANSWERS / "39.ans")

    assert len(first.rows) == 243
    assert len(second.rows) == 14
    assert first.columns == second.columns
    assert first.rows[0][1] == "265"
    assert second.rows[0][1] == "1569"
    assert all(float(row[4]) > 1.5 for row in second.rows)


@pytest.mark.skipif(not _ANSWERS.exists(), reason="official answer files are not in this checkout")
@pytest.mark.parametrize("null_order", ["first", "last"])
def test_official_inventory_maps_all_103_statements_and_query_98_pages(null_order):
    answers = load_tpcds_answer_values(null_order=null_order)

    assert set(answers) == {str(n) for n in range(1, 100)}
    multipart = {"14", "23", "24", "39"}
    assert {key for key, blocks in answers.items() if len(blocks) == 2} == multipart | {"98"}
    assert all(len(blocks) == (2 if key in multipart | {"98"} else 1) for key, blocks in answers.items())
    assert answers["98"][0].columns == answers["98"][1].columns
    assert len(answers["98"][0].rows) == 1997
    assert len(answers["98"][1].rows) == 519
    assert sum(len(blocks) if key in multipart else 1 for key, blocks in answers.items()) == 103


