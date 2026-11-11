from pathlib import Path

import pytest

from benchbox.utils.stale_artifact_pruning import TableArtifactPattern, prune_stale_table_artifacts

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_prune_stale_table_artifacts_escapes_glob_meta_in_table_name(tmp_path: Path) -> None:
    literal_table = "foo[bar]"
    target = tmp_path
    literal_file = target / f"{literal_table}_1_2.dat"
    literal_file.write_text("x", encoding="utf-8")
    neighbor_file = target / "foob_1_2.dat"
    neighbor_file.write_text("x", encoding="utf-8")

    removed = prune_stale_table_artifacts(
        target_dir=target,
        table_names=[literal_table],
        pattern=TableArtifactPattern(
            single_suffix=".dat",
            raw_shard_glob_template="{table}_*.dat",
            compressed_shard_glob_template="{table}_*.dat{ext}",
            shard_regex_template=r"^{table}_\d+_\d+\.dat(?:\.[a-z0-9]+)?$",
        ),
        compression_extensions=[".zst"],
        use_compression=True,
        expect_sharded=True,
    )

    assert literal_file in removed
    assert neighbor_file.exists()


