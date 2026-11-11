from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


INDEX_PATH = Path("index.html")


@pytest.mark.unit
@pytest.mark.skipif(not INDEX_PATH.exists(), reason="index.html not present (not in public release)")
def test_index_page_describes_dependencies_accurately():

    contents = INDEX_PATH.read_text(encoding="utf-8")

    assert "Zero Dependencies" not in contents
    assert "Run Your Own Benchmarks" in contents
    assert "Python-Only Core" in contents
    assert "optional extras" in contents.lower()


