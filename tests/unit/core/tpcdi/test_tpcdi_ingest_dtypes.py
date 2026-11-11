from __future__ import annotations

from pathlib import Path

import pytest

from benchbox.core.tpcdi.benchmark import TPCDIBenchmark

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _make_benchmark(tmp_path: Path) -> TPCDIBenchmark:
    return TPCDIBenchmark(scale_factor=0.01, output_dir=tmp_path)


def test_csv_transform_dtype_contract(tmp_path: Path) -> None:
    benchmark = _make_benchmark(tmp_path)
    source = tmp_path / "trade_history.csv"
    source.write_text(
        "TradeID,Symbol,TradeDate,Quantity\n1,AAA,2023-01-15,100\n2,BBB,2023-01-16,200\n",
        encoding="utf-8",
    )

    result = benchmark._transform_csv_file(str(source), "historical")

    frame = result["dataframe"]
    assert result["records_processed"] == 2
    assert {name: str(dtype) for name, dtype in frame.dtypes.items()} == {
        "TradeID": "int64",
        "Symbol": "str",
        "TradeDate": "str",
        "Quantity": "int64",
        "BatchID": "int64",
    }
    assert frame["TradeDate"].tolist() == ["2023-01-15", "2023-01-16"]


def test_fixed_width_transform_dtype_contract(tmp_path: Path) -> None:
    benchmark = _make_benchmark(tmp_path)
    source = tmp_path / "securities.dat"

    rows = [
        f"{'AAA11111'}{'Acme Corp One':<30}{'NYSE':<10}{'100':<15}",
        f"{'BBB22222'}{'Beta Industries Two':<30}{'NASDAQ':<10}{'200':<15}",
    ]
    source.write_text("\n".join(rows) + "\n", encoding="utf-8")

    result = benchmark._transform_fixed_width_file(str(source), "historical")

    frame = result["dataframe"]
    assert result["records_processed"] == 2
    assert frame["symbol"].tolist() == ["AAA11111", "BBB22222"]
    assert frame["name"].tolist() == ["Acme Corp One", "Beta Industries Two"]
    assert frame["exchange"].tolist() == ["NYSE", "NASDAQ"]
    assert frame["shares_outstanding"].tolist() == [100, 200]
    assert str(frame["symbol"].dtype) == "str"
    assert str(frame["shares_outstanding"].dtype) == "int64"


def test_json_transform_keeps_dates_as_strings(tmp_path: Path) -> None:
    benchmark = _make_benchmark(tmp_path)
    source = tmp_path / "accounts.json"

    source.write_text(
        '[{"account_id": 1, "date": "2023-01-01", "opening_date": "2023-01-01", "status": "Active"},'
        ' {"account_id": 2, "date": "2023-02-01", "opening_date": "2023-02-01", "status": "Active"}]',
        encoding="utf-8",
    )

    result = benchmark._transform_json_file(str(source), "historical")

    frame = result["dataframe"]
    assert result["records_processed"] == 2
    assert str(frame["date"].dtype) == "str"
    assert str(frame["opening_date"].dtype) == "str"
    assert frame["date"].tolist() == ["2023-01-01", "2023-02-01"]


def test_pandas_floor_is_declared_in_packaging_metadata() -> None:
    import re

    from packaging.specifiers import SpecifierSet
    from packaging.version import Version

    pyproject = Path(__file__).resolve().parents[4] / "pyproject.toml"
    pandas_pins = re.findall(r'"pandas(>=?[^"]*)"', pyproject.read_text(encoding="utf-8"))
    assert pandas_pins, "no pandas pin found in pyproject.toml"
    for pin in pandas_pins:
        spec = SpecifierSet(pin)
        assert spec.contains(Version("3.0.0")), f"pandas{pin} excludes 3.0.0"
        assert not spec.contains(Version("2.9.9")), f"pandas{pin} still allows 2.x"


