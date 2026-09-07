"""Choosing which titles to save, and when to refuse the disc entirely.

``makemkvcon mkv`` saves titles, not discs, so something has to decide which
ones. That decision is also where a whole class of copy protection has to be
caught.

Playlist obfuscation
--------------------

Some Blu-rays carry dozens or hundreds of decoy playlists, every one cut to
roughly the length of the real feature, precisely so that a tool picking "the
longest title" picks garbage. There is no reliable way to tell the real one
from the decoys without watching them, so this module does not try. Past a
handful of feature-length titles it refuses, says why, and leaves that disc to
the operator and the MakeMKV GUI.

Refusing costs one disc of manual work. Guessing costs a collection that looks
complete and is not, discovered years later, which is the failure this whole
project is arranged to avoid.

Why "feature length" is relative
--------------------------------

Because that is the shape of the protection: the decoys are cut to the
feature's length, so they cluster near the longest title. An absolute
threshold cannot separate them, and it misfires on ordinary discs -- the
Blu-ray measured on 2026-09-07 had one feature at 2:20:05 and extras running
to 14:49, so "anything over ten minutes" would have called four titles
features and refused nothing useful.
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import model


@dataclass(frozen=True)
class SelectionPolicy:
    """Thresholds for deciding what to save. Tunable per install."""

    #: A title is feature-length at this fraction of the longest title.
    feature_ratio: float = 0.90
    #: ...and at least this long outright, so a disc of five-minute music
    #: videos does not report twenty "features" and get refused as protected.
    min_feature_seconds: int = 600
    #: More feature-length titles than this means decoys rather than a box
    #: set. Five covers a TV disc's worth of episodes.
    max_feature_titles: int = 5
    #: How many candidates to name when handing a disc back. Enough to act
    #: on, not so many that the message is a wall.
    describe_limit: int = 8


@dataclass(frozen=True)
class Selection:
    """What to save, or why a human has to decide instead."""

    ok: bool
    titles: tuple = ()
    reason: str = ""
    error_kind: str = ""
    #: Passed to ``--minlength`` so a single ``mkv ... all`` pass saves
    #: exactly these titles. One pass means one read of the disc.
    min_length_seconds: int = 0
    #: Everything that could have been the feature. Carried whether or not a
    #: decision was reached, because when one was not this is the list the
    #: operator needs in front of them.
    candidates: tuple = ()

    def __bool__(self) -> bool:
        return self.ok

    @property
    def needs_operator(self) -> bool:
        """True when the disc is fine but the *choice* is beyond us.

        Distinct from a disc with nothing worth saving, which needs no help --
        there is nothing to help with.
        """
        return not self.ok and self.error_kind in (
            model.ERR_DECOY_TITLES, model.ERR_AMBIGUOUS_TITLES)


def segments(title) -> list[str]:
    """The clip list behind a title, as MakeMKV reports it.

    Two spellings in the wild, both seen on real discs on 2026-09-07: a
    Blu-ray lists its clips ("123,141,125"), a DVD gives a cell range
    ("1-28"). Both mean an ordered run of segments.
    """
    out: list[str] = []
    for part in (title.segments or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            first, _, last = part.partition("-")
            try:
                out.extend(str(n) for n in range(int(first), int(last) + 1))
                continue
            except ValueError:
                pass
        out.append(part)
    return out


def is_degenerate(title) -> bool:
    """True if this title's clip list could not be a film.

    The obfuscation, seen directly on a real disc (Hancock, 2026-09-07): a
    playlist of a hundred play items that all point at the *same* clip. It has
    a feature's duration and no other property of one. A genuine title's clips
    are distinct -- 19 items, 19 clips on that same disc.

    Only fires on long lists. A single-clip title is perfectly ordinary, and
    plenty of discs report no segments at all.
    """
    segs = segments(title)
    if len(segs) < 10:
        return False
    return len(set(segs)) / len(segs) < 0.5


def distinct(titles) -> list:
    """Drop titles that are the same content authored more than once.

    Blu-rays routinely carry a playlist twice, differing only in a subpath.
    Hancock offers four feature-length titles that are two films: a theatrical
    cut and an extended cut, each authored twice with identical clip lists.
    Saving all four writes every frame twice.

    Deduplicated *before* the decoy count, or three cuts authored in pairs
    would read as six features and be refused.
    """
    seen, out = set(), []
    for title in titles:
        key = title.segments
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        out.append(title)
    return out


def describe(titles, limit: int = 8) -> str:
    """The candidate list, for an operator who has to choose in MakeMKV."""
    lines = []
    for title in titles[:limit]:
        bits = [_clock(title.seconds)]
        if title.chapters:
            bits.append(f"{title.chapters} chapters")
        segs = segments(title)
        if segs:
            bits.append(f"{len(set(segs))} of {len(segs)} clips distinct")
        label = title.source or title.name or f"title {title.index}"
        lines.append(f"  {label} -- " + ", ".join(bits))
    if len(titles) > limit:
        lines.append(f"  ...and {len(titles) - limit} more")
    return "\n".join(lines)


def _doubts(titles, policy: SelectionPolicy) -> str:
    """Why this set of candidates should not be trusted. Empty means trust it.

    One candidate is never in doubt: there is nothing to choose between.
    """
    if len(titles) < 2:
        return ""

    # The same clips in a different order is how a disc hides its feature.
    # Two genuine cuts differ in which clips they use, not merely the order --
    # Hancock's extended cut pulls in clips the theatrical one never touches.
    by_set: dict[frozenset, str] = {}
    for title in titles:
        key = frozenset(segments(title))
        if not key:
            continue
        label = title.source or f"title {title.index}"
        if key in by_set:
            return (f"{label} and {by_set[key]} play the same clips in a "
                    f"different order. Two cuts of a film differ in which "
                    f"clips they use; this is what a disc does to hide which "
                    f"title is the real one.")
        by_set[key] = label

    # A feature-length title with a single chapter is not a feature.
    thin = [t for t in titles if t.chapters == 1]
    if thin:
        return (f"{len(thin)} of {len(titles)} candidates run a feature's "
                f"length with one chapter, which a real feature does not.")
    return ""


def choose(titles, policy: SelectionPolicy | None = None) -> Selection:
    """Decide which titles to save, or hand the disc back. Pure."""
    policy = policy or SelectionPolicy()
    usable = distinct([t for t in titles
                       if t.seconds > 0 and not is_degenerate(t)])
    if not usable:
        return Selection(
            False,
            reason="the disc scan reported no titles with a duration",
            error_kind=model.ERR_NO_FEATURE)

    longest = max(t.seconds for t in usable)
    threshold = max(int(longest * policy.feature_ratio),
                    policy.min_feature_seconds)
    features = sorted((t for t in usable if t.seconds >= threshold),
                      key=lambda t: -t.seconds)

    if not features:
        return Selection(
            False,
            reason=(f"nothing on this disc reaches "
                    f"{policy.min_feature_seconds // 60} minutes; the longest "
                    f"title is {_clock(longest)}"),
            error_kind=model.ERR_NO_FEATURE)

    listing = describe(features, policy.describe_limit)

    if len(features) > policy.max_feature_titles:
        return Selection(
            False, candidates=tuple(features),
            reason=(
                f"{len(features)} titles are all about the same length as the "
                f"longest ({_clock(longest)}). That is how a disc hides its "
                f"feature among decoys, and there is no way to tell them apart "
                f"from here.\n\nBack this disc up by hand in MakeMKV. The "
                f"candidates are:\n{listing}"),
            error_kind=model.ERR_DECOY_TITLES)

    doubt = _doubts(features, policy)
    if doubt:
        return Selection(
            False, candidates=tuple(features),
            reason=(f"{doubt}\n\nBack this disc up by hand in MakeMKV. The "
                    f"candidates are:\n{listing}"),
            error_kind=model.ERR_AMBIGUOUS_TITLES)

    return Selection(True, tuple(features), candidates=tuple(features),
                     min_length_seconds=min(t.seconds for t in features))


# -- what the titles are to each other --------------------------------------

CUT_VARIANTS = "cut_variants"      # alternate cuts of one work
SEPARATE_WORKS = "separate_works"  # different films, or episodes
SINGLE = "single"                  # only one title; nothing to relate


def shared_ratio(first, second) -> float:
    """How much of the shorter title's clip list the two have in common."""
    a, b = set(segments(first)), set(segments(second))
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def relationship(titles, threshold: float = 0.5) -> str:
    """Are these alternate cuts of one work, or separate works?

    Seamless branching is why this is answerable. A disc carrying two cuts
    stores the common footage once and the differing segments separately, so
    the cuts share a backbone of clips and each carries its own. Hancock,
    measured 2026-09-07: 19 clips in each cut, 10 shared, 9 exclusive apiece.

    Two episodes of a series share nothing but perhaps a title card, so they
    fall well under the threshold and read as separate works.
    """
    if len(titles) < 2:
        return SINGLE
    pairs = [(a, b) for i, a in enumerate(titles) for b in titles[i + 1:]]
    if all(shared_ratio(a, b) >= threshold for a, b in pairs):
        return CUT_VARIANTS
    return SEPARATE_WORKS


def match_files(titles, files: list[tuple[str, int]]) -> dict[int, str]:
    """Work out which file on disk each chosen title became.

    MakeMKV's suggested filename embeds the title's index within the list it
    was showing when asked, and the save pass runs a different ``--minlength``
    from the scan, so that number can move. The files on disk are the ground
    truth; this reconciles them against the titles by the three things that do
    not move -- the exact name if it happens to match, MakeMKV's own
    designator, and failing both, size.

    ``files`` is (name, size) pairs. Pure, so the awkward cases are testable
    without writing four gigabytes.
    """
    remaining = list(files)
    matched: dict[int, str] = {}

    def take(title, chosen):
        matched[title.index] = chosen[0]
        remaining.remove(chosen)

    for title in titles:
        hit = next((f for f in remaining
                    if title.suggested_file and f[0] == title.suggested_file), None)
        if hit:
            take(title, hit)

    for title in titles:
        if title.index in matched or not title.designator:
            continue
        hit = next((f for f in remaining
                    if f"-{title.designator}_" in f[0]), None)
        if hit:
            take(title, hit)

    for title in titles:
        if title.index in matched or not remaining:
            continue
        take(title, min(remaining, key=lambda f: abs(f[1] - title.size_bytes)))

    return matched


def expected_bytes(selection: Selection) -> int:
    """What the scan said these titles weigh, for judging the output against.

    The disc's own size is the wrong yardstick for an MKV run -- menus,
    duplicate angles and dropped tracks are all absent by design -- but
    MakeMKV reported a size per title, and the files it writes should be close
    to it.
    """
    return sum(t.size_bytes for t in selection.titles)


def _clock(seconds: int) -> str:
    return f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"
