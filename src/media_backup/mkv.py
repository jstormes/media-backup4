"""Reading back what a saved title actually is.

The size MakeMKV reports for a title is its size *on the disc*, and the remux
comes out under it -- 16% under on a Blu-ray. Judging completeness on that
ratio failed a backup that was byte-for-byte correct, twice, and said "only
84% of the expected size was written" both times. The number was right; the
comparison was not.

Duration is the honest measure. A copy that stopped early is *short*, the disc
told us how long each title runs, and neither figure is affected by container
overhead or dropped tracks.

This reads it out of the Matroska container directly rather than shelling out
to ffprobe, so a backup tool does not acquire a media-framework dependency to
answer a question the file already carries. Element IDs are from RFC 9559.
"""

from __future__ import annotations

import logging
import struct
from pathlib import Path

logger = logging.getLogger(__name__)

SEGMENT = 0x18538067
INFO = 0x1549A966
TIMESTAMP_SCALE = 0x2AD7B1
DURATION = 0x4489

#: Nanoseconds per tick when a file does not say. RFC 9559's default: 1 ms.
DEFAULT_TIMESTAMP_SCALE = 1_000_000

#: How far into the file to look. Segment Info sits near the front, after at
#: most a SeekHead; a 20 GB file should not be read to find out it is absent.
SCAN_LIMIT = 64 * 1024 * 1024


class _End(Exception):
    """Ran out of file mid-element."""


def _read_id(handle) -> int:
    """An EBML element id, marker bits and all -- that is how ids are written."""
    first = handle.read(1)
    if not first:
        raise _End
    value = first[0]
    if value == 0:
        raise ValueError("invalid element id")
    length, mask = 1, 0x80
    while not value & mask:
        mask >>= 1
        length += 1
        if length > 4:
            raise ValueError("element id too long")
    rest = handle.read(length - 1)
    if len(rest) != length - 1:
        raise _End
    return int.from_bytes(first + rest, "big")


def _read_size(handle) -> int | None:
    """An EBML size. None where the element declares itself unknown-length."""
    first = handle.read(1)
    if not first:
        raise _End
    marker = first[0]
    if marker == 0:
        raise ValueError("invalid element size")
    length, mask = 1, 0x80
    while not marker & mask:
        mask >>= 1
        length += 1
        if length > 8:
            raise ValueError("element size too long")
    value = marker & (mask - 1)
    rest = handle.read(length - 1)
    if len(rest) != length - 1:
        raise _End
    for byte in rest:
        value = (value << 8) | byte
    # All data bits set is the spec's way of saying "length unknown".
    return None if value == (1 << (7 * length)) - 1 else value


def _find(handle, wanted: int, end: int | None) -> tuple[bool, int | None]:
    """Scan sibling elements for ``wanted``.

    Returns (found, size-of-its-payload), positioned at that payload. A size of
    None means the element declared itself unknown-length, which Segment
    routinely does.
    """
    while end is None or handle.tell() < end:
        if handle.tell() > SCAN_LIMIT:
            return False, None
        try:
            element = _read_id(handle)
            size = _read_size(handle)
        except (_End, ValueError):
            return False, None
        if element == wanted:
            return True, size
        if size is None:
            return False, None
        handle.seek(size, 1)
    return False, None


def duration_seconds(path: Path) -> float | None:
    """How long this file actually runs, in seconds.

    ``None`` is a real answer and callers must treat it as one: a file that
    will not say how long it is has not been verified, and reporting it as
    good would be the same class of mistake as judging it on its size.
    """
    try:
        with path.open("rb") as handle:
            found, _ = _find(handle, SEGMENT, None)
            if not found:
                return None
            found, info_size = _find(handle, INFO, None)
            if not found:
                return None

            end = None if info_size is None else handle.tell() + info_size
            scale = DEFAULT_TIMESTAMP_SCALE
            duration = None
            while end is None or handle.tell() < end:
                try:
                    element = _read_id(handle)
                    size = _read_size(handle)
                except (_End, ValueError):
                    break
                if size is None:
                    break
                payload = handle.read(size)
                if len(payload) != size:
                    break
                if element == TIMESTAMP_SCALE:
                    scale = int.from_bytes(payload, "big") or scale
                elif element == DURATION:
                    duration = _float(payload)

            if duration is None:
                return None
            return duration * scale / 1_000_000_000
    except OSError as exc:
        logger.warning("could not read %s: %s", path, exc)
        return None


def _float(payload: bytes) -> float | None:
    if len(payload) == 4:
        return struct.unpack(">f", payload)[0]
    if len(payload) == 8:
        return struct.unpack(">d", payload)[0]
    return None
