#!/usr/bin/env python3

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from benchbox.cli.commands.results import validate_cli

if __name__ == "__main__":
    validate_cli(prog_name="validate_results.py")
