"""Test suite for media_backup.

Makes ``src/`` importable so the tests run against the working tree
without an install step (there is no pyproject.toml yet).
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
