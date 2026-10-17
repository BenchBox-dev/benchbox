from __future__ import annotations

import json
from pathlib import Path

import pytest

from _project.scripts.explorer_pipeline.pipeline import ExplorerPipeline
from tests.unit.scripts.explorer_pipeline.conftest import MINIMAL_BUNDLE

pytestmark = [pytest.mark.unit, pytest.mark.fast]

LEGACY_FILES = (
    "manifest.json",
    "meta_leaderboard.json",
    "short_ids.json",
    "results_schema.json",
)
LEGACY_DIRS = ("details", "compare", "benchmarks")


@pytest.fixture()
def built_output(tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    bundles_dir = data_dir / "bundles"
    bundles_dir.mkdir(parents=True)
    (bundles_dir / "b.json").write_text(json.dumps(MINIMAL_BUNDLE), encoding="utf-8")
    output = tmp_path / "out"
    ExplorerPipeline().run(data_dir, output)
    return output


class TestPipelineOutputIsMetricFree:
    def test_no_legacy_files_present(self, built_output: Path) -> None:
        survivors = [name for name in LEGACY_FILES if (built_output / name).exists()]
        assert not survivors, f"pipeline emitted disallowed metric JSON artifacts: {survivors}"

    def test_no_legacy_dirs_present(self, built_output: Path) -> None:
        survivors = [name for name in LEGACY_DIRS if (built_output / name).is_dir()]
        assert not survivors, f"pipeline emitted disallowed metric directories: {survivors}"

    def test_only_canonical_artifacts_emitted(self, built_output: Path) -> None:
        actual = {p.name for p in built_output.iterdir()}
        assert actual == {"results.duckdb", "bundles"}, f"unexpected output-dir members: {actual}"

    def test_stale_artifacts_are_swept_on_rebuild(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        bundles_dir = data_dir / "bundles"
        bundles_dir.mkdir(parents=True)
        (bundles_dir / "b.json").write_text(json.dumps(MINIMAL_BUNDLE), encoding="utf-8")

        output = tmp_path / "out"
        output.mkdir(parents=True)
        for name in LEGACY_FILES:
            (output / name).write_text("{}", encoding="utf-8")
        for name in LEGACY_DIRS:
            (output / name).mkdir()
            (output / name / "stale.json").write_text("{}", encoding="utf-8")

        ExplorerPipeline().run(data_dir, output)

        for name in LEGACY_FILES:
            assert not (output / name).exists(), f"rebuild left {name} in place"
        for name in LEGACY_DIRS:
            assert not (output / name).exists(), f"rebuild left {name}/ in place"
