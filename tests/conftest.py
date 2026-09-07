"""Path setup for pytest, which may import test modules without the package."""

from . import _SRC  # noqa: F401  -- importing the package performs the setup
