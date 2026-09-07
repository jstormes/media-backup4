"""Minimal, real Matroska files for tests.

The saved-titles runner now reads each output file back and compares its
duration against what the disc said. A fixture that writes sparse bytes with
no container in them is not exercising that -- it is exercising the "this file
would not say how long it is" path, which is a different answer.

So these are real files: a genuine EBML header, a Segment holding an Info with
a TimestampScale and a Duration, and then sparse padding out to whatever size
the test wants. Small enough to build inline, real enough that the parser under
test has to actually work.
"""

from __future__ import annotations

import struct
from pathlib import Path

EBML_HEADER = b"\x1a\x45\xdf\xa3"
SEGMENT = b"\x18\x53\x80\x67"
INFO = b"\x15\x49\xa9\x66"
TIMESTAMP_SCALE = b"\x2a\xd7\xb1"
DURATION = b"\x44\x89"

#: One millisecond per tick, which is Matroska's own default.
SCALE_NS = 1_000_000


def _size(length: int) -> bytes:
    """An EBML size as a full-width 8-byte VINT: marker 0x01, 56 data bits."""
    return b"\x01" + length.to_bytes(7, "big")


def _element(element_id: bytes, payload: bytes) -> bytes:
    return element_id + _size(len(payload)) + payload


def matroska_header(seconds: float) -> bytes:
    """The bytes of a Matroska file that declares it runs ``seconds`` long."""
    info = (_element(TIMESTAMP_SCALE, SCALE_NS.to_bytes(4, "big"))
            + _element(DURATION, struct.pack(">d", seconds * 1000.0)))
    return (_element(EBML_HEADER, b"")
            + _element(SEGMENT, _element(INFO, info)))


def write_mkv(path: Path, seconds: float, size_bytes: int) -> Path:
    """Write a file that reads back as ``seconds`` long and ``size_bytes`` big.

    Sparse past the header: judging reads the declared duration and the file's
    ``st_size``, never the frames, so materialising twenty gigabytes would cost
    twenty gigabytes to prove nothing.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    header = matroska_header(seconds)
    with path.open("wb") as handle:
        handle.write(header)
        if size_bytes > len(header):
            handle.truncate(size_bytes)
    return path
