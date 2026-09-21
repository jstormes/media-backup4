"""Reading memory pressure, so that work can decline to start.

On 2026-09-21 `systemd-oomd` killed four rips at once -- 118 GB of partial
work -- because the user session held above 50% memory pressure for twenty
seconds. Nothing was out of memory: swap was 4% used and the kernel OOM
killer never fired. The machine was simply moving four concurrent multi-GB
writes and a 55 GB publish read through a 15 GiB page cache, and PSI cannot
tell that apart from thrashing. See docs/operations/memory-pressure.md.

The lesson is that the load is cumulative and the last thing to join is what
tips it. The fourth rip started at 09:12:24 and writes stalled at 09:14:03.
So the cheapest defence is to look before adding work: this module reads the
number, and callers decide.

**Pressure is stall time, not usage.** ``/proc/pressure/memory`` reports the
percentage of a window in which tasks were delayed waiting for memory:

    some avg10=0.10 avg60=26.96 avg300=25.83 total=126229711
    full avg10=0.09 avg60=26.65 avg300=25.45 total=124850502

``some`` is "at least one task stalled"; ``full`` is "every runnable task
stalled", which is the one that matters -- it means nothing is getting done.
``avg60`` is used for gating rather than ``avg10`` because a rip takes an
hour and should not be refused over a two-second spike, and rather than
``avg300`` because that is still falling long after the box is quiet.

**An unreadable file means "go ahead", not "stop".** This is the opposite of
the rule for a checksum before deleting a rip, and deliberately so. There the
failure of a check must block, because the cost of proceeding is losing the
only copy. Here a missing ``/proc/pressure/memory`` -- an older kernel, PSI
compiled out, a container without it -- would otherwise block every rip
forever, and the cost of proceeding is the ordinary behaviour this project
had all along. A safety feature that can silently stop all work is worse than
the hazard it guards.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

__all__ = ["Pressure", "read", "blocked"]

DEFAULT_PATH = Path("/proc/pressure/memory")


@dataclass(frozen=True)
class Pressure:
    """One reading of ``/proc/pressure/memory``.

    ``available`` is False when PSI could not be read at all, which callers
    must treat as permission to proceed -- see the module docstring.
    """

    full_avg10: float = 0.0
    full_avg60: float = 0.0
    full_avg300: float = 0.0
    some_avg10: float = 0.0
    some_avg60: float = 0.0
    available: bool = True

    def __str__(self) -> str:
        if not self.available:
            return "memory pressure unavailable"
        return (f"memory pressure full avg10={self.full_avg10:.2f} "
                f"avg60={self.full_avg60:.2f}")


UNAVAILABLE = Pressure(available=False)


def read(path: Path = DEFAULT_PATH) -> Pressure:
    """Parse ``/proc/pressure/memory``. Never raises.

    Returns :data:`UNAVAILABLE` when the file is missing, unreadable or
    malformed, rather than guessing a number. A caller that cannot tell
    "quiet" from "could not look" would gate on noise.
    """
    try:
        text = path.read_text()
    except OSError:
        return UNAVAILABLE

    fields: dict[str, dict[str, float]] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2 or parts[0] not in ("some", "full"):
            continue
        row: dict[str, float] = {}
        for token in parts[1:]:
            key, _, value = token.partition("=")
            try:
                row[key] = float(value)
            except ValueError:
                continue
        fields[parts[0]] = row

    if "full" not in fields and "some" not in fields:
        return UNAVAILABLE

    full = fields.get("full", {})
    some = fields.get("some", {})
    return Pressure(
        full_avg10=full.get("avg10", 0.0),
        full_avg60=full.get("avg60", 0.0),
        full_avg300=full.get("avg300", 0.0),
        some_avg10=some.get("avg10", 0.0),
        some_avg60=some.get("avg60", 0.0),
        available=True,
    )


def blocked(limit: float, *, path: Path = DEFAULT_PATH,
            reading: Pressure | None = None) -> str:
    """Why new work should wait, or ``""`` when it may start.

    ``limit`` is a percentage of ``full avg60``; ``0`` or less disables the
    check entirely, matching ``max_concurrent_jobs`` where 0 means "no cap".

    Returns a sentence for the operator rather than a bool, because a job
    that sits in the queue without saying why is the kind of silent stall
    this project keeps having to debug.
    """
    if limit <= 0:
        return ""
    now = reading if reading is not None else read(path)
    if not now.available:
        return ""
    if now.full_avg60 <= limit:
        return ""
    return (f"waiting for memory pressure to fall: "
            f"full avg60 is {now.full_avg60:.0f}%, limit {limit:.0f}%")
