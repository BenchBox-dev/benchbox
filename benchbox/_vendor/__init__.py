import json
from pathlib import Path

_runtime_directory = Path(__file__).parent
_runtime_pin = json.loads((_runtime_directory / "sqlglot.json").read_text(encoding="utf-8"))
__path__.append(f"{_runtime_directory / _runtime_pin['archive']}/benchbox/_vendor")
