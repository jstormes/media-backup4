"""What to copy off a disc, and how the copied titles relate to each other.

Everything MakeMKV reports gets copied. That is the whole policy.

This module used to decide which title was *the feature*, with a relative
length threshold, a duplicate filter, a decoy detector and a set of doubts
that could hand a disc back to the operator. It was wrong in both directions
and the failures were silent:

* two DVD double features lost their second film on 2026-09-08 -- "Leeches /
  The Cold Equations" and "Firehead and Last Lives" -- one to a duplicate
  filter keyed on a cell range that every DVD title spells the same way, one
  to a 90% ratio that the shorter film missed by three percent;
* a kids' disc of twenty-minute shorts and a TV disc of episodes have no
  feature to find, and a threshold built to find one either takes a single
  episode or refuses the disc.

None of that is a threshold that wanted tuning. It was the wrong question:
what a title *is* -- film, alternate cut, episode, deleted scene -- is not
answerable from a scan, and it does not have to be answered here. The copy is
the archive; naming happens at publish time, where there is a person and the
disc sleeve.

So :func:`choose` takes the scan and returns it. MakeMKV has already applied
its own default minimum length to that list, which is the only filter left and
the one place it belongs -- neither the scan nor the save passes
``--minlength``, so what the scan lists is exactly what the save writes.

**What this costs, measured rather than guessed.** A disc that authors its
feature twice is now copied twice: Hancock writes 88 GB where the film is 44,
and its 39 titles include seventeen that are single clips of the feature
offered separately. A disc carrying decoy playlists writes the decoys. That is
the price of never silently dropping a film, and it is paid in disk, which is
recoverable -- the failure it replaces was not.

What remains here is description, not decision: the clip lists a title is
built from, whether two titles are cuts of one work or separate works, and
which file on disk each title became.
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import model


@dataclass(frozen=True)
class Selection:
    """What to copy, or why there is nothing to."""

    ok: bool
    titles: tuple = ()
    reason: str = ""
    error_kind: str = ""

    def __bool__(self) -> bool:
        return self.ok

    @property
    def needs_operator(self) -> bool:
        """Never true now, and kept because the GUI still asks.

        A disc used to be handed back when its feature could not be told from
        its decoys. Nothing is refused for that any more: the decoys are
        copied along with everything else and sorted out at publish time. An
        empty disc still fails, and still needs no help -- there is nothing to
        help with.
        """
        return False


def choose(titles) -> Selection:
    """Every title the scan found, longest first. Pure.

    Longest first because the runner reports progress in this order and an
    operator watching it wants the big one moving first, not because the
    first one means anything.
    """
    usable = [t for t in titles if t.seconds > 0]
    if not usable:
        return Selection(
            False,
            reason="the disc scan reported no titles with a duration",
            error_kind=model.ERR_NO_FEATURE)
    return Selection(True, tuple(sorted(usable, key=lambda t: -t.seconds)))


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

    A shared backbone is necessary and not sufficient: each cut must also
    carry clips the other lacks, which is what branching *is*. Requiring that
    is what keeps a DVD double feature out. Those two films report cell ranges
    like ``1-12`` and ``1-15`` -- one a subset of the other, so they overlap
    completely and share nothing at all in reality. No exclusive clips on
    both sides means this is not branching, whatever the ratio says.
    """
    if len(titles) < 2:
        return SINGLE
    pairs = [(a, b) for i, a in enumerate(titles) for b in titles[i + 1:]]
    if all(_is_branching(a, b, threshold) for a, b in pairs):
        return CUT_VARIANTS
    return SEPARATE_WORKS


def _is_branching(first, second, threshold: float) -> bool:
    """True when two titles look like alternate cuts sharing a backbone."""
    a, b = set(segments(first)), set(segments(second))
    if not a or not b:
        return False
    if not (a - b) or not (b - a):
        return False
    return shared_ratio(first, second) >= threshold


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
