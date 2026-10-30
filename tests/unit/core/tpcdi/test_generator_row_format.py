import io

import pytest

from benchbox.core.tpcdi.generator.rows import PipeRowWriter, format_cell

pytestmark = pytest.mark.fast


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (7e-05, "0.00007"),
        (5e-05, "0.00005"),
        (0.0, "0.0"),
        (0.12345, "0.12345"),
        (1234.5, "1234.5"),
        (1e16, "10000000000000000"),
        (float("nan"), float("nan")),
    ],
)
def test_format_cell_avoids_scientific_notation(value, expected):
    result = format_cell(value)
    if isinstance(expected, float):
        assert result != result
    else:
        assert result == expected


def test_format_cell_leaves_non_floats_untouched():
    assert format_cell(3) == 3
    assert format_cell("7e-05") == "7e-05"
    assert format_cell(None) is None
    assert format_cell(True) is True


def test_pipe_row_writer_emits_plain_decimals():
    handle = io.StringIO()
    writer = PipeRowWriter(handle)
    writer.writerow([1, "a", 7e-05, 0.15])
    writer.writerows([[2, "b", 1e-05, 2.5]])
    assert handle.getvalue() == "1|a|0.00007|0.15\r\n2|b|0.00001|2.5\r\n"
