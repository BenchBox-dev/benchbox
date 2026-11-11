import json
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


NOTEBOOK_DIR = Path(__file__).parent.parent.parent / "examples" / "notebooks"
NOTEBOOKS = [
    "bigquery_benchmarking.ipynb",
    "clickhouse_benchmarking.ipynb",
    "databricks_benchmarking.ipynb",
    "redshift_benchmarking.ipynb",
    "snowflake_benchmarking.ipynb",
]

REQUIRED_SECTIONS = [
    "Installation & Setup",
    "Quick Start Example",
    "Advanced Examples",
    "Platform-Specific Features",
    "Performance Analysis",
    "Troubleshooting",
]


@pytest.mark.parametrize("notebook_name", NOTEBOOKS)
def test_notebook_exists(notebook_name):
    notebook_path = NOTEBOOK_DIR / notebook_name
    assert notebook_path.exists(), f"{notebook_name} not found"


@pytest.mark.parametrize("notebook_name", NOTEBOOKS)
def test_notebook_is_valid_json(notebook_name):
    notebook_path = NOTEBOOK_DIR / notebook_name
    with open(notebook_path, encoding="utf-8") as f:
        try:
            json.load(f)
        except json.JSONDecodeError:
            pytest.fail(f"{notebook_name} is not a valid JSON file")


@pytest.mark.parametrize("notebook_name", NOTEBOOKS)
def test_notebook_has_required_sections(notebook_name):
    notebook_path = NOTEBOOK_DIR / notebook_name
    with open(notebook_path, encoding="utf-8") as f:
        notebook_content = json.load(f)

    headings = []
    for cell in notebook_content["cells"]:
        if cell["cell_type"] == "markdown":
            source = "".join(cell["source"])
            if source.startswith("## "):
                heading = source[3:].strip()
                heading = heading.split("\n")[0].strip()
                if heading and heading[0].isdigit():
                    parts = heading.split(maxsplit=1)
                    if len(parts) == 2 and parts[0].rstrip(".").isdigit():
                        heading = parts[1]
                headings.append(heading)

    for section in REQUIRED_SECTIONS:
        assert section in headings, f"{notebook_name} is missing section: {section}"


@pytest.mark.parametrize("notebook_name", NOTEBOOKS)
def test_notebook_has_installation_cell(notebook_name):
    notebook_path = NOTEBOOK_DIR / notebook_name
    with open(notebook_path, encoding="utf-8") as f:
        notebook_content = json.load(f)

    has_install_cell = False
    for cell in notebook_content["cells"]:
        if cell["cell_type"] == "code":
            source = "".join(cell["source"])
            if "pip install" in source and "benchbox" in source:
                has_install_cell = True
                break

    assert has_install_cell, f"{notebook_name} is missing pip install cell"


@pytest.mark.parametrize("notebook_name", NOTEBOOKS)
def test_notebook_has_run_benchmark_cell(notebook_name):
    notebook_path = NOTEBOOK_DIR / notebook_name
    with open(notebook_path, encoding="utf-8") as f:
        notebook_content = json.load(f)

    has_run_benchmark_cell = False
    for cell in notebook_content["cells"]:
        if cell["cell_type"] == "code":
            source = "".join(cell["source"])
            if "run_benchmark" in source:
                has_run_benchmark_cell = True
                break

    assert has_run_benchmark_cell, f"{notebook_name} is missing run_benchmark cell"


