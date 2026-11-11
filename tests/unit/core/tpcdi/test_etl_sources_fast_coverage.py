from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

import benchbox.core.tpcdi.etl.sources as src_mod

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _FakeGenerator:
    def __init__(self, scale_factor=1.0, output_dir=None, start_date=None, end_date=None):
        self.scale_factor = scale_factor
        self.output_dir = output_dir
        self.start_date = start_date
        self.end_date = end_date

    def _generate_oltp_data(self):
        return [str(Path(self.output_dir) / "oltp.tbl")]

    def _generate_hr_data(self):
        return [str(Path(self.output_dir) / "hr.xml")]

    def _generate_external_data(self):
        return [str(Path(self.output_dir) / "ext.dat")]

    def generate_all_source_data(self):
        return {
            "oltp": [str(Path(self.output_dir) / "oltp.tbl")],
            "hr": [str(Path(self.output_dir) / "hr.xml"), str(Path(self.output_dir) / "hr2.xml")],
        }

    def _generate_customer_extract(self):
        return str(Path(self.output_dir) / "customer_extract.csv")

    def _generate_market_prices(self):
        return str(Path(self.output_dir) / "market_prices.csv")

    def get_file_format_info(self):
        return {"compression": "none", "format": "pipe"}


def test_format_wrappers_generate_and_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(src_mod, "TPCDISourceDataGenerator", _FakeGenerator)
    df = pd.DataFrame([{"id": 1, "name": "a"}])

    csv_fmt = src_mod.CSVSourceFormat(delimiter=",", quote_char='"', encoding="utf-8")
    csv_out = csv_fmt.generate_data("Customer", 10, output_dir=tmp_path)
    assert csv_out.endswith("oltp.tbl")
    csv_file = tmp_path / "x.csv"
    csv_fmt.write_to_file(df, csv_file)
    assert csv_file.exists()

    fw_fmt = src_mod.FixedWidthSourceFormat(field_widths={"id": 3, "name": 4}, fill_char=".")
    fw_out = fw_fmt.generate_data("Legacy", 10, output_dir=tmp_path)
    assert fw_out.endswith("ext.dat")
    fw_file = tmp_path / "x.fw"
    fw_fmt.write_to_file(df, fw_file)
    assert fw_file.read_text(encoding="utf-8").strip().startswith("1..a")

    pipe_fmt = src_mod.PipeDelimitedSourceFormat(escape_char="\\", null_representation="NULL")
    pipe_out = pipe_fmt.generate_data("Any", 10, output_dir=tmp_path)
    assert pipe_out.endswith("oltp.tbl")
    pipe_file = tmp_path / "x.tbl"
    pipe_fmt.write_to_file(pd.DataFrame([{"id": 1, "name": None}]), pipe_file)
    assert "NULL" in pipe_file.read_text(encoding="utf-8")


def test_xml_format_generate_and_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("lxml", reason="lxml required for pandas to_xml()")
    monkeypatch.setattr(src_mod, "TPCDISourceDataGenerator", _FakeGenerator)
    df = pd.DataFrame([{"id": 1, "name": "a"}])

    xml_fmt = src_mod.XMLSourceFormat(root_element="root", record_element="row")
    xml_out = xml_fmt.generate_data("Broker", 10, output_dir=tmp_path)
    assert xml_out.endswith("hr.xml")
    xml_file = tmp_path / "x.xml"
    xml_fmt.write_to_file(df, xml_file)
    assert xml_file.exists()


def test_source_data_generator_wrapper_methods(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(src_mod, "TPCDISourceDataGenerator", _FakeGenerator)
    gen = src_mod.SourceDataGenerator(scale_factor=0.1, seed=7)
    assert {"csv", "xml", "fixed_width", "pipe"} <= set(gen.formats.keys())
    gen.register_format("custom", src_mod.CSVSourceFormat())
    assert "custom" in gen.formats

    historical = gen.generate_historical_data("csv", tmp_path)
    assert "oltp" in historical and isinstance(historical["oltp"], Path)
    assert "hr_1" in historical

    incremental = gen.generate_incremental_data("pipe", tmp_path, batch_number=3)
    assert "oltp_batch3" in incremental
    assert "hr_1_batch3" in incremental

    customer = gen.generate_customer_management_data("csv", tmp_path, batch_number=1)
    market = gen.generate_daily_market_data("pipe", tmp_path, batch_date="2026-01-01")
    assert customer.name == "customer_extract.csv"
    assert market.name == "market_prices.csv"

    stats = gen.get_data_statistics()
    assert stats["status"] == "generated"
    assert stats["seed"] == 7

    fresh = src_mod.SourceDataGenerator(scale_factor=0.01, seed=None)
    assert fresh.get_data_statistics()["status"] == "not_generated"


