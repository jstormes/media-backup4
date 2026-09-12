"""Collection and disc data model.

Pure: dataclasses plus dict round-tripping, no filesystem. :mod:`store` owns
persistence.

Every path recorded here is **relative to the collection directory**. The
whole directory is renamed into ``finished/`` when a collection is completed,
so an absolute path would rot silently at exactly the moment the data becomes
an archive nobody looks at for years.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

SCHEMA_VERSION = 1

# -- disc states ------------------------------------------------------------

PENDING = "pending"        # added to the collection, not started
QUEUED = "queued"          # start requested, waiting for a slot
RESOLVING = "resolving"    # finding the makemkv index, verifying identity
SCANNING = "scanning"      # reading the title inventory
COPYING = "copying"        # makemkvcon backup is running
VERIFYING = "verifying"    # process exited, judging the result
EJECTING = "ejecting"      # good copy, ejecting the disc
DONE = "done"              # terminal, good
FAILED = "failed"          # terminal for this attempt; retryable
ABANDONED = "abandoned"    # terminal, operator gave up

#: States in which a job is live. Anything here at startup was interrupted.
ACTIVE_STATES = frozenset({QUEUED, RESOLVING, SCANNING, COPYING, VERIFYING, EJECTING})
TERMINAL_STATES = frozenset({DONE, FAILED, ABANDONED})

# -- collection states ------------------------------------------------------

OPEN = "open"
FINISHED = "finished"
CANCELLED = "cancelled"

# -- what a collection holds ------------------------------------------------
#
# A scan cannot tell a season of episodes from a film and its extras. Both are
# "several titles, one of them longest", and on a DVD the clip lists cannot
# settle it either. The operator can tell at a glance from the case in their
# hand, so they are asked once per collection and the answer steers the
# publish-time filters.
#
# UNKNOWN is the honest default: the filters then fall back to what the clip
# lists alone support, which is what they did before this field existed.

KIND_UNKNOWN = ""
#: One film. Optionally several cuts of it, optionally extras, and sometimes
#: something that is not a film at all -- the Blade set carries a television
#: pilot beside the features. The longest title is the work; slices of it are
#: fragments.
KIND_MOVIE = "movie"
#: Several films in one package -- a double bill, a four-film pack. Each
#: feature-length title is its own work, so "the longest is the feature" is
#: exactly wrong here, and extras and stray television episodes may sit
#: alongside them.
KIND_MOVIES = "movies"
#: A themed set of one-off programmes rather than a numbered run: the
#: Scooby-Doo holiday collection. Episode-length titles that are content, but
#: with no season or episode numbers to carry.
KIND_SPECIAL = "special"
#: A numbered run of episodes -- Challenge of the Super Friends, Speed Racer.
#: Many titles of near-identical length, all of them content, usually beside a
#: "play all" that must be dropped in favour of its parts. A short count here
#: is a missing episode, not a quiet success.
KIND_SERIES = "series"

KINDS = (KIND_UNKNOWN, KIND_MOVIE, KIND_MOVIES, KIND_SPECIAL, KIND_SERIES)

#: The kinds whose titles are episodes rather than features: equal runtimes are
#: expected, a "play all" is normal, and every episode-length title is content.
EPISODIC_KINDS = (KIND_SPECIAL, KIND_SERIES)

KIND_LABELS = {
    KIND_UNKNOWN: "Not sure",
    KIND_MOVIE: "Movie",
    KIND_MOVIES: "Collection of movies",
    KIND_SPECIAL: "Special",
    KIND_SERIES: "Show series",
}

# -- failure kinds ----------------------------------------------------------

ERR_CANCELLED = "cancelled"
ERR_STALLED = "stalled"
ERR_TIMEOUT = "timeout"
ERR_INTERRUPTED = "interrupted"
ERR_DISC_CHANGED = "disc_changed"
ERR_NO_SPACE = "no_space"
ERR_RESOLVE = "resolve_failed"
ERR_SPAWN = "spawn_failed"
ERR_UNKILLABLE = "unkillable"
ERR_COPY = "copy_failed"
#: Too many feature-length titles to tell the real one from the decoys.
ERR_DECOY_TITLES = "decoy_titles"
#: Few enough to list, but something about them says do not trust the pick.
ERR_AMBIGUOUS_TITLES = "ambiguous_titles"
#: Nothing on the disc is long enough to be worth saving.
ERR_NO_FEATURE = "no_feature"
#: The job never started: the store could not clear the way for it.
ERR_STORE = "store_failed"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_id() -> str:
    return str(uuid.uuid4())


@dataclass
class Title:
    """One title from a disc scan. Recorded so the archive is searchable."""

    index: int
    name: str = ""
    duration: str = ""
    size_bytes: int = 0
    source: str = ""
    #: MakeMKV's segments map, the ordered clip list behind this title
    #: ("123,141,125,..."). Two titles with the same map are the same content
    #: authored twice, which is most of what a Blu-ray's title list is.
    segments: str = ""
    chapters: int = 0
    #: MakeMKV's suggested output filename (attribute 27). Only a suggestion:
    #: the "_tNN" in it counts within the title list MakeMKV was showing at
    #: the time, and the save pass runs a different --minlength from the scan.
    #: :attr:`output_file` is what it actually became; see selection.match_files.
    suggested_file: str = ""
    #: MakeMKV's own designator for the title ("A1", "B2"). Unlike the _tNN
    #: index this does not move when the title list is filtered differently.
    designator: str = ""
    #: The file this title actually became, filled in after the run.
    output_file: str = ""
    #: How many streams the scan found on this title. A disc can offer the
    #: same footage twice with different track sets -- Hancock's feature comes
    #: as 23 streams and as 15 -- so this is what tells the richer from the
    #: poorer when the clip lists are identical.
    streams: int = 0

    @property
    def seconds(self) -> int:
        """``duration`` in seconds; 0 when MakeMKV reported none.

        Stored as MakeMKV writes it ("1:42:39") because that is what goes in
        the archive and what an operator reads. Everything that compares
        durations wants a number.
        """
        total = 0
        for part in self.duration.split(":"):
            try:
                total = total * 60 + int(part)
            except ValueError:
                return 0
        return total

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Title":
        return cls(**{k: v for k, v in data.items() if k in cls.__annotations__})


@dataclass
class Attempt:
    """One try at copying a disc. Kept even when it failed -- especially then."""

    attempt: int
    started_at: str = field(default_factory=now)
    ended_at: str = ""
    device: str = ""
    drive_model: str = ""
    drive_serial: str = ""
    makemkv_index: int = -1
    source_spec: str = ""
    argv: list[str] = field(default_factory=list)
    exit_code: int | None = None
    final_progress: int = 0
    progress_max: int = 0
    bytes_written: int = 0
    layout: str = ""
    outcome: str = ""
    failure_reason: str = ""
    error_kind: str = ""
    read_error_count: int = 0
    hash_error_count: int = 0
    #: code -> count. Bounded, and exactly what is needed to tune the policy
    #: once a hundred real discs have gone through.
    message_codes: dict[str, int] = field(default_factory=dict)
    log: str = ""
    rejected_path: str = ""
    eject_ok: bool | None = None
    eject_error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Attempt":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class Disc:
    """One physical disc within a collection."""

    disc_id: str = field(default_factory=new_id)
    ordinal: int = 1
    label: str = ""
    makemkv_disc_name: str = ""
    media: str = ""
    disc_size_bytes: int = 0
    state: str = PENDING
    state_detail: str = ""
    created_at: str = field(default_factory=now)
    updated_at: str = field(default_factory=now)
    titles: list[Title] = field(default_factory=list)
    attempts: list[Attempt] = field(default_factory=list)

    @property
    def attempt_count(self) -> int:
        return len(self.attempts)

    @property
    def last_attempt(self) -> Attempt | None:
        return self.attempts[-1] if self.attempts else None

    @property
    def is_active(self) -> bool:
        return self.state in ACTIVE_STATES

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def is_good(self) -> bool:
        return self.state == DONE

    @property
    def main_title(self) -> "Title | None":
        """The biggest title on the disc, for summarising it on screen.

        The largest, not the first: a DVD's extras are a couple of hundred
        megabytes against a feature's several gigabytes, and size is an
        integer where duration is a string.

        Not a claim that this *is* the feature. A disc can hold two films or
        a season of episodes, and since 2026-09-08 nothing in the app decides
        which title is the work -- see :mod:`makemkv.selection`. This is a
        label for a list, and the caller should word it as one.
        """
        return max(self.titles, key=lambda t: t.size_bytes, default=None)

    @property
    def display_name(self) -> str:
        """What to call this disc on screen.

        MakeMKV's name wins over the volume label. The label is whatever was
        stamped on the disc, which for a DVD is very often the generic
        ``DVD_VIDEO``, while MakeMKV knows it as "Fresh Horses". Even where
        both are meaningful MakeMKV's is the better written one -- proper case
        and punctuation against ``SPIDER_MAN_ACROSS_SPIDER_VERSE``.
        """
        return (self.makemkv_disc_name or self.label
                or f"Disc {self.ordinal}")

    def to_dict(self) -> dict:
        data = asdict(self)
        data["titles"] = [t.to_dict() for t in self.titles]
        data["attempts"] = [a.to_dict() for a in self.attempts]
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Disc":
        known = {f for f in cls.__dataclass_fields__}
        kwargs = {k: v for k, v in data.items() if k in known}
        kwargs["titles"] = [Title.from_dict(t) for t in data.get("titles", [])]
        kwargs["attempts"] = [Attempt.from_dict(a) for a in data.get("attempts", [])]
        return cls(**kwargs)


@dataclass
class Collection:
    """A boxed set, a season, or a single film -- whatever the operator groups."""

    collection_id: str = field(default_factory=new_id)
    #: UPC/SKU/free text off the disc case. Not unique, not validated.
    identifier: str = ""
    title: str = ""
    notes: str = ""
    #: Optional declared size of the set. The only guard against filing an
    #: incomplete box set, an error otherwise invisible for years.
    expected_disc_count: int | None = None
    #: One of KINDS, or "" when the operator did not say. Steers the
    #: publish-time filters: a season's "play all" must be dropped in favour
    #: of its episodes, while a film's slices must be dropped in favour of the
    #: feature, and those two look alike from the clip lists alone.
    kind: str = KIND_UNKNOWN
    state: str = OPEN
    created_at: str = field(default_factory=now)
    updated_at: str = field(default_factory=now)
    finished_at: str = ""
    schema_version: int = SCHEMA_VERSION
    app_version: str = ""
    discs: list[Disc] = field(default_factory=list)

    @property
    def done_count(self) -> int:
        return sum(1 for d in self.discs if d.is_good)

    @property
    def has_active_jobs(self) -> bool:
        return any(d.is_active for d in self.discs)

    @property
    def is_complete(self) -> bool:
        """Every disc terminal, at least one good, and the count matches."""
        if not self.discs or not all(d.is_terminal for d in self.discs):
            return False
        if self.done_count == 0:
            return False
        if self.expected_disc_count is not None:
            return self.done_count == self.expected_disc_count
        return self.done_count == len(self.discs)

    def finish_warnings(self) -> list[str]:
        """Why finishing now would be premature. Empty means clean."""
        warnings = []
        if not self.discs:
            warnings.append("This collection has no discs.")
        active = [d.display_name for d in self.discs if d.is_active]
        if active:
            warnings.append("Still copying: " + ", ".join(active))
        bad = [d.display_name for d in self.discs
               if d.is_terminal and not d.is_good]
        if bad:
            warnings.append("Not backed up: " + ", ".join(bad))
        waiting = [d.display_name for d in self.discs if d.state == PENDING]
        if waiting:
            warnings.append("Never started: " + ", ".join(waiting))
        if self.expected_disc_count is not None and self.done_count != self.expected_disc_count:
            warnings.append(
                f"{self.done_count} disc(s) backed up, but this set was "
                f"declared as {self.expected_disc_count}.")
        return warnings

    def next_ordinal(self) -> int:
        return max((d.ordinal for d in self.discs), default=0) + 1

    def disc(self, disc_id: str) -> Disc | None:
        for d in self.discs:
            if d.disc_id == disc_id:
                return d
        return None

    def to_dict(self) -> dict:
        data = asdict(self)
        data["discs"] = [d.to_dict() for d in self.discs]
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Collection":
        known = {f for f in cls.__dataclass_fields__}
        kwargs = {k: v for k, v in data.items() if k in known}
        kwargs["discs"] = [Disc.from_dict(d) for d in data.get("discs", [])]
        return cls(**kwargs)
