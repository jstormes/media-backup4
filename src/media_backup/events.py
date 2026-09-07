"""Events passed from job worker threads to the GUI thread.

Kept in their own module so :mod:`runner` and :mod:`jobs` can both use them
without importing each other.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Event kinds.
STATE = "state"        # the job moved to a new state
PROGRESS = "progress"  # a progress update; may be coalesced or dropped
MESSAGE = "message"    # a MSG record worth showing or logging
IDENTIFIED = "identified"  # the disc scan says what this disc actually is
FINISHED = "finished"  # terminal; the drive slot may now be released


@dataclass(frozen=True)
class JobEvent:
    job_id: str
    kind: str
    state: str = ""
    step: str = ""
    total_pct: float = 0.0
    step_pct: float = 0.0
    message: str = ""
    code: int = 0
    #: Set only on FINISHED.
    verdict: object = None
    observation: object = None
    error_kind: str = ""
    titles: tuple = ()
    #: Set on IDENTIFIED. What MakeMKV calls the disc -- "Fresh Horses" where
    #: the volume label is only ever "DVD_VIDEO".
    disc_name: str = ""
    disc_type: str = ""

    @property
    def is_terminal(self) -> bool:
        return self.kind == FINISHED
