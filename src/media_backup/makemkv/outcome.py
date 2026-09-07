"""Deciding whether a backup succeeded.

``makemkvcon`` returns exit 0 for operational failures -- a run that copied
nothing still exits 0 -- so the exit code is evidence, not a verdict. Instead
we gather everything observable into a :class:`BackupObservation` and judge it
with one pure function.

That split is deliberate. The failure taxonomy of several hundred real discs
cannot be enumerated up front, so the policy is a small pure function over a
struct: every real-world failure becomes a fixture, and revising the policy
later is a test-first afternoon rather than an archaeology dig.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import inspect as layouts
from . import messages

SUCCESS = "success"
SUCCESS_UNVERIFIED = "success_unverified"
PARTIAL = "partial"
FAILURE = "failure"
CANCELLED = "cancelled"


@dataclass(frozen=True)
class OutcomePolicy:
    """Thresholds for judging a run. Tunable once real discs have been seen."""

    #: Bytes written must reach this fraction of the disc size. Catches a
    #: truncated copy, a killed process and ENOSPC regardless of output.
    size_ratio_floor: float = 0.90
    #: Final total progress must reach this fraction of PRGV's own max.
    progress_floor: float = 0.99
    #: Accept a complete-looking copy whose directory layout we don't
    #: recognise. DVD layouts are not yet characterised, and failing every
    #: unrecognised tree would block the whole DVD collection on day one.
    allow_unknown_layout: bool = True


@dataclass
class BackupObservation:
    """Everything gathered about one backup run."""

    exit_code: int | None = None
    message_codes: dict[int, int] = field(default_factory=dict)
    max_total_progress: int = 0
    progress_max: int = 0
    saw_any_progress: bool = False
    layout: str = layouts.UNKNOWN
    bytes_written: int = 0
    disc_size_bytes: int = 0
    terminated_by_us: bool = False
    stall_reason: str = ""

    def saw(self, code: int) -> bool:
        return self.message_codes.get(code, 0) > 0

    def saw_any(self, codes) -> bool:
        return any(self.message_codes.get(c, 0) > 0 for c in codes)

    @property
    def read_error_count(self) -> int:
        return sum(self.message_codes.get(c, 0) for c in messages.READ_ERRORS)

    @property
    def hash_error_count(self) -> int:
        return sum(self.message_codes.get(c, 0) for c in messages.HASH_ERRORS)

    @property
    def size_ratio(self) -> float:
        if self.disc_size_bytes <= 0:
            return 0.0
        return self.bytes_written / self.disc_size_bytes

    @property
    def progress_ratio(self) -> float:
        if self.progress_max <= 0:
            return 0.0
        return self.max_total_progress / self.progress_max


@dataclass(frozen=True)
class Verdict:
    outcome: str
    reason: str
    detail: dict = field(default_factory=dict)

    @property
    def is_good(self) -> bool:
        """True if the copy is usable -- i.e. worth ejecting and keeping."""
        return self.outcome in (SUCCESS, SUCCESS_UNVERIFIED, PARTIAL)


def judge(obs: BackupObservation, policy: OutcomePolicy | None = None) -> Verdict:
    """Decide the outcome of a backup run. Pure."""
    policy = policy or OutcomePolicy()
    detail = {
        "exit_code": obs.exit_code,
        "size_ratio": round(obs.size_ratio, 4),
        "progress_ratio": round(obs.progress_ratio, 4),
        "layout": obs.layout,
        "read_errors": obs.read_error_count,
        "bytes_written": obs.bytes_written,
        "disc_size_bytes": obs.disc_size_bytes,
    }

    # 1. Our own doing. Checked first so a cancel is never reported as a disc
    #    fault -- the operator knows what they did and should not be told to
    #    go and clean a perfectly good disc.
    if obs.terminated_by_us or obs.saw(messages.CANCELLED):
        return Verdict(CANCELLED, obs.stall_reason or "cancelled", detail)

    # 2. Exit 1 is the only exit code that means anything: a usage error, i.e.
    #    a bug in the argv we built.
    if obs.exit_code == 1:
        return Verdict(FAILURE, "makemkvcon rejected the command line", detail)

    # 3. A fatal condition during the run. Judged *before* the generic
    #    "Backup failed." announcement, because MakeMKV prints both: 5080 says
    #    only that the run died, while the cause code says why. Reporting the
    #    announcement would send the operator to clean a disc over what is
    #    really a cdrom-group problem.
    for code in sorted(obs.message_codes):
        if code in messages.FATAL:
            hint = messages.describe(code) or f"fatal MakeMKV error {code}"
            return Verdict(FAILURE, hint, {**detail, "code": code})

    # 4. Explicit terminal failure with no cause code to explain it.
    if obs.saw_any(messages.FAILURE):
        return Verdict(FAILURE, "MakeMKV reported: Backup failed", detail)

    # 5. The copy has to have got to the end of the progress bar.
    if obs.saw_any_progress and obs.progress_ratio < policy.progress_floor:
        return Verdict(FAILURE,
                       f"copy stopped at {obs.progress_ratio:.0%} of the disc",
                       detail)

    # 6. And it has to be about as large as the disc it came from. This is the
    #    check that is independent of everything MakeMKV chose to print.
    if obs.disc_size_bytes > 0 and obs.size_ratio < policy.size_ratio_floor:
        return Verdict(FAILURE,
                       f"only {obs.size_ratio:.0%} of the disc was written",
                       detail)

    # 7. Nothing on disk at all.
    if obs.layout == layouts.MISSING:
        return Verdict(FAILURE, "no output was produced", detail)

    # 8. Copy finished but some files are corrupt. Usually a dirty disc that
    #    mostly read; the operator decides whether that is good enough.
    if obs.saw_any(messages.PARTIAL):
        n = obs.hash_error_count
        return Verdict(PARTIAL,
                       f"backup completed but {n or 'some'} file(s) failed the "
                       "hash check", detail)

    # 9. A well-formed copy with the success message is unambiguous.
    recognised = obs.layout in (layouts.BDMV, layouts.VIDEO_TS, layouts.MIXED,
                                layouts.ISO)
    if obs.saw_any(messages.SUCCESS) and recognised:
        return Verdict(SUCCESS, "backup completed", detail)

    # 10. Complete by every measurable standard, but either the success
    #     message or the layout was not what we expected. Keep it, flag it.
    if recognised:
        return Verdict(SUCCESS_UNVERIFIED,
                       "copy looks complete but MakeMKV printed no completion "
                       "message", detail)
    if policy.allow_unknown_layout and obs.saw_any(messages.SUCCESS):
        return Verdict(SUCCESS_UNVERIFIED,
                       f"backup completed but the output layout "
                       f"({obs.layout}) was not recognised", detail)

    return Verdict(FAILURE, "no recognisable disc structure was produced", detail)
