import importlib.util
import sys
from pathlib import Path

here = Path(sys.argv[1]).resolve()
sys.path = [str(here)] + [
    entry for entry in sys.path if entry and "site-packages" not in entry and Path(entry).resolve() != Path.cwd()
]
unavailable = [name for name in ("pydantic", "yaml") if importlib.util.find_spec(name) is None]

import corpus_rule as r

outcome = r.derive_outcome(r.Facts("tpch", 1.0, frozenset({"power"}), None))
loaded = sorted(name for name in sys.modules if name.split(".")[0] in ("benchbox", "pydantic", "yaml"))
print(outcome.class_name, outcome.lanes, loaded, unavailable)
