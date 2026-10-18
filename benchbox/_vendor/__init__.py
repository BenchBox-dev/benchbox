import json
from pathlib import Path

_runtime_directory = Path(__file__).parent
_runtime_pin = json.loads((_runtime_directory / "sqlglot.json").read_text(encoding="utf-8"))
_archive = _runtime_pin["archive"]
if (
    _archive != f"{_runtime_pin['wheel']}.zip"
    or Path(_archive).name != _archive
    or not (_runtime_directory / _archive).is_file()
):
    raise ImportError("Bundled SQLGlot runtime pin does not identify its packaged archive")
__path__.append(f"{_runtime_directory / _archive}/benchbox/_vendor")
