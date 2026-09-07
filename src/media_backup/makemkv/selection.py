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


@dataclass(frozen=True)
class Selection:
    """What to save, or why nothing will be."""

    ok: bool
    titles: tuple = ()
    reason: str = ""
    error_kind: str = ""
    #: Passed to ``--minlength`` so a single ``mkv ... all`` pass saves
    #: exactly these titles. One pass means one read of the disc.
    min_length_seconds: int = 0

    def __bool__(self) -> bool:
        return self.ok


def choose(titles, policy: SelectionPolicy | None = None) -> Selection:
    """Decide which titles to save. Pure."""
    policy = policy or SelectionPolicy()
    usable = [t for t in titles if t.seconds > 0]
    if not usable:
        return Selection(
            False,
            reason="the disc scan reported no titles with a duration",
            error_kind=model.ERR_NO_FEATURE)

    longest = max(t.seconds for t in usable)
    threshold = max(int(longest * policy.feature_ratio),
                    policy.min_feature_seconds)
    features = [t for t in usable if t.seconds >= threshold]

    if not features:
        return Selection(
            False,
            reason=(f"nothing on this disc reaches "
                    f"{policy.min_feature_seconds // 60} minutes; the longest "
                    f"title is {_clock(longest)}"),
            error_kind=model.ERR_NO_FEATURE)

    if len(features) > policy.max_feature_titles:
        return Selection(
            False,
            reason=(
                f"{len(features)} titles are all about the same length as the "
                f"longest ({_clock(longest)}). That is how a disc hides its "
                f"feature among decoys, and there is no way to tell them "
                f"apart from here. Back this disc up by hand in MakeMKV."),
            error_kind=model.ERR_DECOY_TITLES)

    features.sort(key=lambda t: -t.seconds)
    return Selection(True, tuple(features),
                     min_length_seconds=min(t.seconds for t in features))


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
