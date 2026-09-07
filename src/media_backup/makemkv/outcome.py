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
    #: How many titles the selection asked for, and how the run answered.
    titles_expected: int = 0
    titles_saved: int = 0
    titles_failed: int = 0
    #: .mkv files actually on disk. MakeMKV's own count agreeing with this is
    #: the check; either alone can be wrong.
    files_written: int = 0
    #: What the scan said the chosen titles weigh. The yardstick for the
    #: output, in place of the disc's size -- an MKV run leaves out menus and
    #: unwanted tracks by design, so the disc size says nothing about it.
    expected_bytes: int = 0

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
        """Bytes written against what the chosen titles were said to weigh."""
        if self.expected_bytes <= 0:
            return 0.0
        return self.bytes_written / self.expected_bytes

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
    """Decide the outcome of a saved-titles run. Pure."""
    policy = policy or OutcomePolicy()
    detail = {
        "exit_code": obs.exit_code,
        "size_ratio": round(obs.size_ratio, 4),
        "progress_ratio": round(obs.progress_ratio, 4),
        "layout": obs.layout,
        "read_errors": obs.read_error_count,
        "bytes_written": obs.bytes_written,
        "expected_bytes": obs.expected_bytes,
        "titles_expected": obs.titles_expected,
        "titles_saved": obs.titles_saved,
        "titles_failed": obs.titles_failed,
        "files_written": obs.files_written,
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

    # 3. A fatal condition during the run. Judged *before* any generic
    #    announcement, because MakeMKV prints both: the announcement says only
    #    that the run died, while the cause code says why. Reporting the
    #    announcement would send the operator to clean a disc over what is
    #    really a cdrom-group problem.
    for code in sorted(obs.message_codes):
        if code in messages.FATAL:
            hint = messages.describe(code) or f"fatal MakeMKV error {code}"
            return Verdict(FAILURE, hint, {**detail, "code": code})

    # 4. Nothing on disk at all. A run that said why gets to say why.
    if obs.files_written == 0:
        if obs.saw_any(messages.FAILURE):
            return Verdict(FAILURE, "MakeMKV could not save any title", detail)
        return Verdict(FAILURE, "no titles were saved", detail)

    # 5. The run has to have got to the end of the progress bar.
    if obs.saw_any_progress and obs.progress_ratio < policy.progress_floor:
        return Verdict(FAILURE,
                       f"stopped at {obs.progress_ratio:.0%} of the disc",
                       detail)

    # 6. Fewer titles than were asked for. Not a failure: one lost extra is
    #    not the same news as a lost feature, and the operator decides which
    #    this was -- the titles are recorded on the attempt either way.
    missing = max(0, obs.titles_expected - obs.files_written)
    if obs.titles_failed or missing or obs.saw_any(messages.PARTIAL):
        n = obs.titles_failed or missing
        return Verdict(PARTIAL,
                       f"{obs.files_written} of {obs.titles_expected} title(s) "
                       f"saved; {n} did not", detail)

    # 7. And the files have to be about as big as the scan said those titles
    #    were. This is the check that is independent of everything MakeMKV
    #    chose to print.
    if obs.expected_bytes > 0 and obs.size_ratio < policy.size_ratio_floor:
        return Verdict(FAILURE,
                       f"only {obs.size_ratio:.0%} of the expected size was "
                       f"written", detail)

    # 8. Every title accounted for, and MakeMKV said so itself.
    if obs.saw_any(messages.SUCCESS):
        return Verdict(SUCCESS, "all titles saved", detail)

    # 9. Complete by every measurable standard, but MakeMKV never said so.
    #    Keep it, flag it.
    return Verdict(SUCCESS_UNVERIFIED,
                   "the titles are all there, but MakeMKV printed no "
                   "completion message", detail)
