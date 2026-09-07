"""Inspecting what a backup actually left on disk.

The strongest success signal available is independent of anything makemkvcon
prints: is the output tree shaped like a disc, and is it about as big as the
disc was? udisks2 already told us the disc size before the run started.
"""

from __future__ import annotations

import os
import stat as stat_module
from pathlib import Path

BDMV = "bdmv"
VIDEO_TS = "video_ts"
MIXED = "mixed"
UNKNOWN = "unknown"
MISSING = "missing"


def classify_layout(dest: Path) -> str:
    """Identify the disc structure in ``dest``.

    Requires the index file *and* at least one stream, so a directory holding
    an empty ``BDMV/`` skeleton is not mistaken for a finished copy.

    ``AACS/``, ``CERTIFICATE/`` and ``discatt.dat`` are deliberately not
    required: an unencrypted disc legitimately has none of them, and demanding
    them would fail perfectly good rips.
    """
    if not dest.is_dir():
        return MISSING

    bdmv = dest / "BDMV"
    video_ts = dest / "VIDEO_TS"

    has_bdmv = (bdmv / "index.bdmv").is_file() and _any_with_suffix(bdmv / "STREAM", ".m2ts")
    has_video_ts = (
        _any_named(video_ts, "VIDEO_TS.IFO") and _any_with_suffix(video_ts, ".vob")
    )

    if has_bdmv and has_video_ts:
        return MIXED
    if has_bdmv:
        return BDMV
    if has_video_ts:
        return VIDEO_TS
    return UNKNOWN


def _any_named(directory: Path, name: str) -> bool:
    if not directory.is_dir():
        return False
    target = name.lower()
    try:
        return any(p.name.lower() == target for p in directory.iterdir())
    except OSError:
        return False


def _any_with_suffix(directory: Path, suffix: str) -> bool:
    if not directory.is_dir():
        return False
    try:
        return any(p.suffix.lower() == suffix and p.is_file()
                   for p in directory.iterdir())
    except OSError:
        return False


def tree_size(path: Path) -> int:
    """Total bytes of regular files under ``path``. Symlinks are not followed."""
    total = 0
    if not path.exists():
        return 0
    for root, _dirs, files in os.walk(path, followlinks=False):
        for name in files:
            try:
                info = os.lstat(os.path.join(root, name))
            except OSError:
                continue
            if stat_module.S_ISREG(info.st_mode):
                total += info.st_size
    return total


def is_empty(path: Path) -> bool:
    """True if ``path`` is absent or contains nothing.

    makemkvcon refuses to back up into a non-empty directory (MSG:5068), so
    this is checked immediately before every spawn.
    """
    if not path.exists():
        return True
    if not path.is_dir():
        return False
    try:
        next(path.iterdir())
    except StopIteration:
        return True
    except OSError:
        return False
    return False
