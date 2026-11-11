import json
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


NOTEBOOK_DIR = Path(__file__).parent.parent.parent / "examples" / "notebooks"

PARAMS = [
    (
        "bigquery_benchmarking.ipynb",
        ["google-cloud-bigquery", "google-cloud-storage"],
    ),
    (
        "databricks_benchmarking.ipynb",
        ["databricks-sql-connector", "databricks-sdk"],
    ),
    (
        "snowflake_benchmarking.ipynb",
        ["snowflake-connector-python"],
    ),
    (
        "redshift_benchmarking.ipynb",
        ["redshift_connector", "boto3"],
    ),
    (
        "clickhouse_benchmarking.ipynb",
        ["clickhouse-driver"],
    ),
]


@pytest.mark.parametrize("nb_name, pkgs", PARAMS)
def test_notebook_has_platform_installs_and_runner_import(nb_name, pkgs):
    nb_path = NOTEBOOK_DIR / nb_name
    assert nb_path.exists(), f"Notebook missing: {nb_name}"

    with nb_path.open("r", encoding="utf-8") as f:
        nb = json.load(f)

    code_cells = [c for c in nb.get("cells", []) if c.get("cell_type") == "code"]
    assert code_cells, f"No code cells found in {nb_name}"

    install_sources = [
        "".join(c.get("source", [])) for c in code_cells if "pip install" in "".join(c.get("source", []))
    ]
    assert install_sources, f"pip install cell not found in {nb_name}"
    src = install_sources[0]

    platform_name = nb_name.replace("_benchmarking.ipynb", "")
    uses_extras = f"benchbox[{platform_name}]" in src

    if not uses_extras:
        for token in pkgs:
            assert token in src, f"{nb_name} missing required install token: {token}"

    has_runner_ref = any("benchbox.core.runner" in "".join(c.get("source", [])) for c in code_cells)
    assert has_runner_ref, f"{nb_name} missing benchbox.core.runner usage"


