import sys
from pathlib import Path

import pytest

from benchbox.core.tpcds.c_tools import (
    DSQGenBinary,
    TPCDSCTools,
    _resolve_tpcds_tool_and_template_paths,
)
from benchbox.core.tpcds.generator import TPCDSDataGenerator
from benchbox.utils.tpc_compilation import get_precompiled_bundle_root
from tests.utilities.paths import REPO_ROOT

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _require_precompiled_bundle() -> Path:
    bundle_root = get_precompiled_bundle_root("dsqgen") or get_precompiled_bundle_root("dsdgen")
    if bundle_root is None:
        pytest.skip("Precompiled TPC-DS bundle not available for this platform")
    assert bundle_root is not None
    return bundle_root


def test_resource_resolver_prefers_precompiled_bundle() -> None:

    bundle_root = _require_precompiled_bundle()

    tools_path, templates_path = _resolve_tpcds_tool_and_template_paths()

    assert tools_path == bundle_root
    assert templates_path.name == "query_templates"
    assert "_binaries" in str(templates_path) or "_sources" in str(templates_path)
    assert templates_path.exists()


def test_tpcds_c_tools_uses_precompiled_bundle() -> None:

    bundle_root = _require_precompiled_bundle()

    tools = TPCDSCTools()

    assert tools.tools_path == bundle_root
    assert tools.dsqgen_path.parent == bundle_root
    assert tools.templates_path.name == "query_templates"
    assert "_binaries" in str(tools.templates_path) or "_sources" in str(tools.templates_path)
    assert tools.templates_path.exists()


def test_dsqgen_binary_uses_precompiled_bundle() -> None:

    bundle_root = _require_precompiled_bundle()

    dsqgen = DSQGenBinary()

    assert Path(dsqgen.dsqgen_path).parent == bundle_root
    assert dsqgen.templates_dir.name == "query_templates"
    assert "_binaries" in str(dsqgen.templates_dir) or "_sources" in str(dsqgen.templates_dir)
    assert dsqgen.templates_dir.exists()


def test_sample_dataset_detected_for_fractional_scale(tmp_path) -> None:
    bundle_root = _require_precompiled_bundle()
    sample_dir = bundle_root / "samples" / "tpcds_sf001"

    if not sample_dir.exists():
        sample_dir = REPO_ROOT / "examples" / "data" / "tpcds_sf001"
    if not sample_dir.exists():
        pytest.skip("No sample dataset available for scale factor 0.01")

    generator = object.__new__(TPCDSDataGenerator)
    generator.scale_factor = 0.01
    generator._package_root = REPO_ROOT
    generator.should_use_compression = lambda: True
    detected = TPCDSDataGenerator._get_sample_data_dir(generator)

    assert detected is not None
    assert detected.name in ("tpcds_sf001",)


@pytest.mark.skipif(sys.platform == "win32", reason="TPC-DS binaries for Windows have different structure")
def test_template_separation_works_end_to_end() -> None:
    bundle_root = _require_precompiled_bundle()

    dsqgen = DSQGenBinary()

    assert Path(dsqgen.dsqgen_path).parent == bundle_root, "Binary should be from precompiled bundle"
    assert "_binaries" in str(dsqgen.templates_dir) or "_sources" in str(dsqgen.templates_dir), (
        "Templates should be from bundled _binaries/ or _sources/"
    )
    assert dsqgen.tools_dir == bundle_root, "Tools dir should point to binary location"

    idx_file = bundle_root / "tpcds.idx"
    dst_file = bundle_root / "tpcds.dst"
    assert idx_file.exists(), "tpcds.idx should exist in binary location"
    assert dst_file.exists(), "tpcds.dst should exist in binary location"

    assert dsqgen.templates_dir.exists(), "Templates directory should exist"
    template_list = dsqgen.templates_dir / "templates.lst"
    assert template_list.exists(), "templates.lst should exist in templates directory"

    for dialect in ["netezza", "oracle", "db2"]:
        try:
            query = dsqgen.generate(query_id=1, seed=42, scale_factor=1.0, dialect=dialect)
            assert query, f"Query should be generated for {dialect} dialect"
            assert len(query) > 0, f"Query should not be empty for {dialect} dialect"
            assert "select" in query.lower() or "with" in query.lower(), (
                f"Query should contain SQL for {dialect} dialect"
            )
        except Exception as e:
            pytest.fail(f"Query generation failed for {dialect} dialect with separated paths: {e}")


