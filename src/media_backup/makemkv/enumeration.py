"""Resolving a stable device path to a volatile ``disc:N`` index.

``backup`` only accepts a ``disc:N`` source, but those indices are assigned
per scan and shift when drives are hotplugged. The device node is the stable
identity, so every backup has to resolve one to the other immediately before
it runs -- and then prove it resolved to the disc the operator actually chose.

Getting this wrong backs up the wrong disc into a collection, which is both
silent and very hard to notice later. Hence :func:`resolve`, which refuses
rather than guesses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .records import Drv, parse_line

__all__ = ["DriveRow", "Resolution", "parse_drives", "resolve"]

#: Reasons a resolution can fail, recorded verbatim as an attempt's error_kind.
NOT_FOUND = "device_not_found"
NO_DISC = "no_disc"
LOADING = "drive_loading"
LABEL_MISMATCH = "disc_changed"

DriveRow = Drv


def parse_drives(lines: Iterable[str]) -> list[Drv]:
    """Extract the real drives from an enumeration run.

    16 DRV rows are always emitted, one per slot, whether or not a drive is
    there. Filter on state, never on slot position.
    """
    drives = []
    for line in lines:
        record = parse_line(line)
        if isinstance(record, Drv) and record.is_drive:
            drives.append(record)
    return drives


@dataclass(frozen=True)
class Resolution:
    """The outcome of resolving one device to a disc index."""

    ok: bool
    index: int = -1
    row: Drv | None = None
    error_kind: str = ""
    detail: str = ""

    def __bool__(self) -> bool:
        return self.ok


def resolve(drives: Iterable[Drv], device: str, expected_label: str = "") -> Resolution:
    """Find the ``disc:N`` index for ``device`` and verify its identity.

    ``expected_label`` is the volume label udisks2 reported when the operator
    picked the disc. If both labels are known and they disagree, the disc in
    the drive is not the one that was chosen -- refuse.
    """
    matches = [d for d in drives if d.device == device]
    if not matches:
        return Resolution(False, error_kind=NOT_FOUND,
                          detail=f"{device} is not in MakeMKV's drive list")

    row = matches[0]

    if row.is_loading:
        return Resolution(False, row=row, error_kind=LOADING,
                          detail=f"{device} is still loading the disc")

    if not row.has_disc:
        return Resolution(False, row=row, error_kind=NO_DISC,
                          detail=f"{device} has no disc (state {row.state})")

    if expected_label and row.disc_name and row.disc_name != expected_label:
        return Resolution(
            False, row=row, error_kind=LABEL_MISMATCH,
            detail=(f"{device} holds {row.disc_name!r}, "
                    f"but {expected_label!r} was selected"),
        )

    return Resolution(True, index=row.index, row=row)
