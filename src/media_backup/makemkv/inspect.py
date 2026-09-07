"""Inspecting what a backup actually left on disk.

The strongest success signal available is independent of anything makemkvcon
prints: is the output shaped like a disc, and is it about as big as the disc
was? udisks2 already told us the disc size before the run started.

"Shaped like a disc" is two different shapes. ``makemkvcon backup`` writes a
**directory tree** for a Blu-ray (``BDMV/``) and a single **ISO image file**
for a DVD. Measured 2026-09-06; see :meth:`store.CollectionStore.wants_image`.
"""

from __future__ import annotations

import os
import stat as stat_module
from pathlib import Path

BDMV = "bdmv"
VIDEO_TS = "video_ts"
MIXED = "mixed"
MKV = "mkv"          # a directory of .mkv files: what an mkv run produces
ISO = "iso"          # a single image file, from the old backup-based runs
UNKNOWN = "unknown"
MISSING = "missing"

#: The first bytes of an ISO 9660 volume descriptor, at offset 0x8001. Cheap
#: and decisive: it beats trusting a file extension we chose ourselves.
ISO_MAGIC = b"CD001"
ISO_MAGIC_OFFSET = 0x8001


def classify_layout(dest: Path) -> str:
    """Identify the disc structure in ``dest``.

    Requires the index file *and* at least one stream, so a directory holding
    an empty ``BDMV/`` skeleton is not mistaken for a finished copy.

    ``AACS/``, ``CERTIFICATE/`` and ``discatt.dat`` are deliberately not
    required: an unencrypted disc legitimately has none of them, and demanding
    them would fail perfectly good rips.
    """
    if dest.is_file():
        return ISO if looks_like_iso(dest) else UNKNOWN

    if not dest.is_dir():
        return MISSING

    # What this project produces now. Checked first: a saved-titles directory
    # has no BDMV or VIDEO_TS in it and would otherwise read as UNKNOWN.
    if count_mkv(dest):
        return MKV

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


def count_mkv(dest: Path) -> int:
    """How many ``.mkv`` files a saved-titles directory holds.

    Counted rather than merely detected: makemkvcon reports how many titles it
    saved, and the number of files on disk agreeing with it is the check.
    """
    if not dest.is_dir():
        return 0
    try:
        return sum(1 for p in dest.iterdir()
                   if p.is_file() and p.suffix.lower() == ".mkv")
    except OSError:
        return 0


def looks_like_iso(path: Path) -> bool:
    """True if ``path`` carries an ISO 9660 volume descriptor.

    A truncated image -- a DVD backup that was killed part way -- has no
    descriptor at 0x8001 yet, so this also catches the copy that never got
    far enough to be worth keeping.
    """
    try:
        with path.open("rb") as handle:
            handle.seek(ISO_MAGIC_OFFSET)
            return handle.read(len(ISO_MAGIC)) == ISO_MAGIC
    except OSError:
        return False


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
    """Total bytes written at ``path``. Symlinks are not followed.

    Handles both shapes a backup can take: the sum of the regular files under
    a directory, or the size of a single image file.
    """
    total = 0
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
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
    """True if ``path`` is absent or is a directory containing nothing.

    A Blu-ray destination is a directory makemkvcon is happy to find already
    there, provided it is empty. A DVD destination must not exist at all --
    that case is a plain ``exists()`` check, not this one.
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
