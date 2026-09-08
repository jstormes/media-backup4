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

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable

from .records import Drv, parse_line

__all__ = ["DriveIndex", "DriveRow", "Resolution", "parse_drives", "resolve"]

#: How long an enumeration may be reused. Long enough for a burst of jobs
#: starting together to share one, short enough that a disc swapped in
#: between is not resolved from a stale list. resolve() still checks the
#: label, but two discs can share one.
DEFAULT_TTL_S = 5.0

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


class DriveIndex:
    """One drive enumeration, shared by every job that needs it.

    ``info disc:9999`` lists *every* drive, so N jobs each running their own
    is N times the work for one answer -- and every one of them opens every
    drive. A single drive that hangs MakeMKV's probe therefore stalls all of
    them, not just its own job. Sharing the result bounds that: one probe is
    attempted, and whatever it says goes to everyone waiting.

    The lock also serialises the probes, so jobs starting together never pile
    concurrent makemkvcon processes onto the same drives.

    None of which applies to an isolated run: it enumerates one drive, cannot
    stall anyone else's, and its list names only its own device. The runner
    keeps its own list in that case -- see :mod:`.isolation`.
    """

    def __init__(self, ttl_s: float = DEFAULT_TTL_S,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl_s
        self._clock = clock
        self._lock = threading.Lock()
        self._drives: list[Drv] = []
        self._taken_at = float("-inf")

    def drives(self, enumerate_once: Callable[[], Iterable[str]],
               *, force: bool = False) -> list[Drv]:
        """The current drive list, enumerating only if there is no fresh one.

        ``force`` skips the cache, for a caller that knows the previous
        answer is stale -- a drive that was still loading, say.
        """
        with self._lock:
            fresh = self._drives and self._clock() - self._taken_at <= self._ttl
            if fresh and not force:
                return self._drives
            drives = parse_drives(enumerate_once())
            # A failed probe is not cached: the next job should try again
            # rather than inherit an empty list.
            if drives:
                self._drives = drives
                self._taken_at = self._clock()
            return drives

    def invalidate(self) -> None:
        """Drop the cached list. Call when a disc is inserted or ejected."""
        with self._lock:
            self._drives = []
            self._taken_at = float("-inf")
