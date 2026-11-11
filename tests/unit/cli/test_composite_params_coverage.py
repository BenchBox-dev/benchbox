from __future__ import annotations

import importlib

import click
import pytest

mod = importlib.import_module("benchbox.cli.composite_params")

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_compression_parse_and_validation() -> None:
    c = mod.CompressionConfig.parse("zstd:9")
    assert c.type == "zstd" and c.level == 9 and c.enabled is True
    assert mod.CompressionConfig.parse("none").enabled is False
    with pytest.raises(click.BadParameter):
        mod.CompressionConfig.parse("bad:1")


def test_plan_capture_parse_variants() -> None:
    p = mod.PlanCaptureConfig.parse("queries:1,6,17,strict:true")
    assert p.queries == ["1", "6", "17"]
    assert p.strict is True

    with pytest.raises(click.BadParameter):
        mod.PlanCaptureConfig.parse("sample:0.1")
    with pytest.raises(click.BadParameter):
        mod.PlanCaptureConfig.parse("first:5")


def test_convert_validation_and_partition_parsing() -> None:
    c = mod.TableFormatConfig.parse("iceberg:zstd,partition:year,month")
    assert c.format == "iceberg"
    assert c.partition_cols == ["year", "month"]
    assert mod.TableFormatConfig.parse(None) is None
    with pytest.raises(click.BadParameter):
        mod.TableFormatConfig.parse("badfmt")


def test_validation_and_force_parse() -> None:
    v = mod.ValidationConfig.parse("full")
    assert v.preflight and v.postgen and v.postload and v.check_platforms
    assert mod.ValidationConfig.parse("disabled").mode == "disabled"
    with pytest.raises(click.BadParameter):
        mod.ValidationConfig.parse("bad")

    f = mod.ForceConfig.parse("datagen,upload")
    assert f.any is True and f.datagen and f.upload
    with pytest.raises(click.BadParameter):
        mod.ForceConfig.parse("bad")


def test_click_param_types_convert() -> None:
    assert mod.FORCE.convert(True, None, None).any is True
    assert mod.COMPRESSION.convert("gzip:6", None, None).level == 6
    assert mod.PLAN_CONFIG.convert("queries:3", None, None).queries == ["3"]
    assert mod.TABLE_FORMAT.convert("parquet", None, None).format == "parquet"
    assert mod.VALIDATION.convert("postgen", None, None).postgen is True

    with pytest.raises(click.BadParameter):
        mod.FORCE.convert("bad", None, None)


