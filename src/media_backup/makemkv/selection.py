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

**The one thing that did come back, and why it is not the old detector.**
Saban's Power Rangers, 2026-09-11: 308 titles, 287 of them the same thirteen
segments in 287 different orders. Copying everything meant a projected 7.5 TB
from a 46.6 GiB disc, onto a volume with 1.5 TB free and three other jobs
running on it. Knives Out, the same day, is the same shape: 283 titles, a pool
of 201. "Paid in disk, which is recoverable" is true right up to the point
where the disk fills and takes the other jobs with it.

So :func:`obfuscation` refuses those discs. It does not pick a title and it
does not drop one -- the full list is handed back with the refusal, for a
person to resolve against the disc and the playlist maps others have
published. The forbidden thing is silently choosing; declining loudly is not
the same act. See docs/makemkv/playlist-obfuscation.md.

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
        """True when a person, not a retry, is what this disc is waiting on.

        One case reaches this: playlist obfuscation, where the feature cannot
        be told from its decoys and copying the lot would fill the volume.
        Retrying such a disc produces the same 283 titles it produced last
        time. An empty disc still fails and still needs no help -- there is
        nothing to help with.
        """
        return self.error_kind == model.ERR_DECOY_TITLES


#: How many distinct orderings of one identical clip list before a disc is
#: treated as obfuscated. Measured 2026-09-11 across a 106-disc archive: the
#: two protected discs scored 287 (Power Rangers) and 201 (Knives Out), and
#: *every* other class on every other disc scored 1. There is no middle
#: ground to tune against, so this sits well above the noise deliberately --
#: too low refuses a good disc, too high only copies extra, which is the
#: policy anyway.
OBFUSCATION_ORDERINGS = 8


def permutation_classes(titles) -> dict[tuple, list]:
    """Group titles by their clip list ignoring order.

    Same clips, same order -- content authored twice. Expected, and copied:
    Hancock offers its feature that way.

    Same clips, *different* order -- the same footage rearranged. Nothing
    legitimate authors that. Seamless branching, which is what two cuts of a
    film are, gives each cut clips the other lacks; see :func:`_is_branching`.

    Different clips -- separate works, or branched cuts. Not this.
    """
    classes: dict[tuple, list] = {}
    for title in titles:
        clips = segments(title)
        if clips:
            classes.setdefault(tuple(sorted(clips)), []).append(title)
    return classes


def obfuscation(titles):
    """The largest pile of permutations of one clip list, if it is big enough.

    Returns ``(titles_in_class, distinct_orderings)`` or ``None``. Pure.
    """
    worst = None
    for members in permutation_classes(titles).values():
        orderings = {tuple(segments(t)) for t in members}
        if len(orderings) >= OBFUSCATION_ORDERINGS:
            if worst is None or len(orderings) > worst[1]:
                worst = (members, len(orderings))
    return worst


def choose(titles) -> Selection:
    """Every title the scan found, longest first, unless the disc is protected.

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

    found = obfuscation(usable)
    if found:
        members, orderings = found
        # The whole list goes back with the refusal. Whoever picks this up
        # needs to match a published segment map against these titles, and
        # cannot do that from a number.
        return Selection(
            False,
            tuple(sorted(usable, key=lambda t: -t.seconds)),
            reason=(f"{orderings} titles are the same {len(segments(members[0]))} "
                    f"clips in different orders, all {members[0].duration}. "
                    f"This disc hides its feature among decoy playlists; one "
                    f"of those titles is the film and the scan cannot say "
                    f"which. See docs/makemkv/playlist-obfuscation.md"),
            error_kind=model.ERR_DECOY_TITLES)

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


# -- sorting the pile at publish time ---------------------------------------
#
# None of this changes what the ripper saves -- SPEC section 9 still copies
# every title the scan reports. This is the vocabulary the *publish* step needs
# to say why a title was not published, and it is here rather than in the
# publishing agent because it is exactly the clip-list reasoning above.

CONTENT = "content"        # publish it
PLAY_ALL = "play_all"      # the disc's "play all"; its parts are published instead
FRAGMENT = "fragment"      # a slice of a longer title
DUPLICATE = "duplicate"    # the same clips as another title, kept once
DEGENERATE = "degenerate"  # one clip repeated to fake a runtime


def is_degenerate(title, repeats: int = 10) -> bool:
    """True when a title is one clip played over and over.

    Measured on Speed Racer disc 1 (Mach GoGoGo), 2026-09-12: ``00001.mpls``
    runs 989 minutes and is built from 901 play-items that are all the same
    clip. Hancock carries two of the same shape, 100 play-items on one or two
    clips, which SPEC section 16.4 records.

    It is not content, and -- more sharply -- its *declared* size poisons any
    estimate summed across titles. A 989-minute entry on a 46 GB disc would
    have the free-space guard refuse a disc that fits comfortably.
    """
    clips = segments(title)
    return len(clips) >= repeats and len(set(clips)) <= max(1, len(clips) // repeats)


def clips_discriminate(titles) -> bool:
    """Whether a clip list can be compared between two of these titles.

    On a Blu-ray it names global files in ``BDMV/STREAM``, so two titles
    naming clip 510 demonstrably share content. On a DVD MakeMKV reports a
    cell range **local to its own title**: Challenge of the Superfriends disc
    2 side A offers seven different episodes and every one of them reports
    ``1``. Comparing those says nothing, and the fragment rule built on
    comparing them would drop all seven in favour of the play-all. SPEC
    section 16.3 calls this the most expensive fact in the document.

    Prefer the recorded media type --
    ``classify(..., clips_global=disc.media.startswith("optical_bd"))``, which
    covers ``optical_bd_r`` too. This is the fallback for a caller that does
    not have it, and it reads locality off the titles themselves, two ways:

    * **every title expands to exactly {1..N}.** That is what a cell range
      looks like and a global clip list almost never does (SPEC section 16.3).
      It takes at least one numerically-spelled list to say this: a caller that
      hands over its own spelling -- :func:`dvd.clip_list` writes ``1002/1`` --
      is not answering the question, and an empty "all of them agree" is not a
      yes.
    * **one clip list appears on titles of different runtimes.** It cannot be
      naming the same content twice. Side A's seven episodes share the list
      ``1`` across seven runtimes.

    Either is enough to say the ids are title-local. A disc with no clip lists
    at all is not discriminating either; there is nothing to compare.

    **It is a fallback and not a substitute.** Measured 2026-09-12 across the
    161 discs in the archive that record a media type: it agrees with 160 and
    is wrong about one. Giant's second side is a DVD whose feature reports
    ``31-40,41-56``, a cell range that does not begin at 1, so the first rule
    does not fire and the guess says global. Section 16.3's "every DVD title
    expands to exactly {1..N}", measured over 21 scans, has a counterexample
    at 161. Pass the media type.
    """
    lists = [tuple(segments(t)) for t in titles]
    lists = [c for c in lists if c]
    if not lists:
        return False
    numeric = [c for c in lists if all(x.isdigit() for x in c)]
    if numeric and all(sorted(int(c) for c in clips)
                       == list(range(1, len(set(clips)) + 1))
                       for clips in numeric):
        return False
    runtimes: dict[tuple, set] = {}
    for clips, t in zip((tuple(segments(x)) for x in titles), titles):
        if clips:
            runtimes.setdefault(clips, set()).add(t.seconds)
    return all(len(seen) == 1 for seen in runtimes.values())


def play_all_parts(titles) -> dict[int, list]:
    """Map each play-all title to the titles it is made of, by clip list.

    A "play all" is the union of two or more siblings whose clip lists are
    **disjoint** and which together cover it exactly. That is a different
    shape from a fragment, and telling them apart is the whole point:

    * brave new world, disc 1 -- ``0,1,2`` is exactly ``0`` + ``1`` + ``2``,
      three episodes that share no clips. Publishing the parent and dropping
      the parts would replace nine episodes with three two-hour files.
    * Hancock -- the feature's clips have many subsets, but they overlap and
      no disjoint set of siblings covers the feature, so nothing here is a
      play-all and the slices stay fragments.

    Verified against both discs 2026-09-12. Only meaningful where
    :func:`clips_discriminate` holds; :func:`classify` checks that.
    """
    sets = {t.index: frozenset(segments(t)) for t in titles if segments(t)}
    out: dict[int, list] = {}
    for parent in titles:
        whole = sets.get(parent.index)
        if not whole or len(whole) < 2:
            continue
        parts = [t for t in titles
                 if t.index != parent.index
                 and sets.get(t.index) and sets[t.index] < whole]
        if len(parts) < 2:
            continue
        covered: set = set()
        disjoint = True
        for part in parts:
            clips = sets[part.index]
            if clips & covered:
                disjoint = False
                break
            covered |= clips
        if disjoint and covered == whole:
            out[parent.index] = parts
    return out


def play_all_by_runtime(titles, spread: float = 0.2, slack: int = 4,
                        most: int = 12) -> dict[int, list]:
    """Map each play-all to its parts by runtime, for discs with no clip identity.

    A DVD's play-all cannot be recognised from clip lists -- see
    :func:`clips_discriminate` -- but it can be recognised from the clock. Its
    runtime is the sum of the episodes' runtimes, and on real discs the sum is
    exact. Measured 2026-09-12 on Challenge of the Superfriends:

    * disc 2 side A -- the 2:32:04 title is 1303 + 1303 + 1299 + 1304 + 1302 +
      1309 + 1304 = 9124 seconds, to the second;
    * disc 2 side B -- 43:17 is 21:40 + 21:37, to the second.

    Brave new world's Blu-ray play-all matches the same way (2:13:17 = 41:08 +
    43:48 + 48:21), so the rule is not a DVD workaround; it is a second,
    independent reading of the same fact.

    **Subset-sum will find spurious answers if it is let loose**, and this is
    the reason the rule is only applied to a collection the operator has
    called episodic. Given thirty extras and a slack of a few seconds, some
    combination of them sums to the feature. Two constraints keep it honest:

    * the parts must be near-equal in length (``spread``), because episodes of
      one show are -- 1299 to 1309 seconds above, and brave new world's 2468
      to 2901 is the widest real case seen;
    * there must be between two and ``most`` of them.

    ``slack`` is seconds of total error allowed, which covers MakeMKV
    truncating each runtime to the second.
    """
    out: dict[int, list] = {}
    for parent in titles:
        if parent.seconds <= 0:
            continue
        pool = [t for t in titles
                if t.index != parent.index and 0 < t.seconds < parent.seconds]
        if len(pool) < 2:
            continue
        for seed in pool:
            group = [t for t in pool
                     if abs(t.seconds - seed.seconds) <= spread * seed.seconds]
            if len(group) < 2:
                continue
            found = _subset_summing_to(group, parent.seconds, slack, most)
            if found:
                out[parent.index] = found
                break
    return out


def _subset_summing_to(parts, target: int, slack: int, most: int):
    """The largest subset of ``parts`` whose runtimes sum to ``target``. Pure.

    Largest rather than any: seven episodes is a better reading of a play-all
    than a lucky pair, and a pair that happens to fit should not shadow the
    real answer.
    """
    reach: dict[tuple, list] = {(0, 0): []}
    for t in parts:
        for (total, count), chosen in list(reach.items()):
            if count >= most:
                continue
            step = (total + t.seconds, count + 1)
            if step[0] > target + slack or step in reach:
                continue
            reach[step] = chosen + [t]
    best = None
    for (total, count), chosen in reach.items():
        if count >= 2 and abs(total - target) <= slack:
            if best is None or count > len(best):
                best = chosen
    return best


def classify(titles, kind: str = model.KIND_UNKNOWN,
             clips_global: bool | None = None) -> dict[int, str]:
    """What each title is, for the publish step. Pure.

    ``kind`` is what the operator said the collection holds -- one of
    :data:`model.KINDS`, recorded when the collection was created. It is here
    because the disc does not say, and getting it wrong is not symmetric: a
    rule that is right for a film is what drops a season's episodes. What it
    changes:

    * **episodic** (a series or a special) -- a play-all is expected, and is
      recognised from runtime as well as from clip lists, so the parent is
      dropped and its episodes kept. Without the kind the parent survives as
      content and the operator sees a 2:32 file alongside the seven episodes
      it contains, which is the safe direction but not the right answer.
    * **one movie** -- the longest title is the film and is never demoted to
      a play-all. A film authored as two chapter-clips that the disc also
      offers separately would otherwise read as a play-all of them, and the
      film would be dropped in favour of its two halves. Shorter play-alls
      are still found: Mrs. Doubtfire's Blu-ray has one covering seven
      featurettes.
    * **a collection of movies** -- as the default. A play-all of films is
      real but rare, and the runtime rule is not worth its false positives
      on a disc with a pile of extras.

    ``clips_global`` says whether a clip list means the same thing in two
    titles. It does on a Blu-ray and it does not on a DVD, where MakeMKV
    reports a cell range local to each title -- so the caller should pass
    ``disc.media == "optical_bd"``, which is recorded on every disc. Left
    unset it is guessed by :func:`clips_discriminate`, and the guess is only
    as good as the disc: August Rush's DVD gives its feature ``1-31`` and its
    ten-minute featurette ``1,2,3,4,5,6,7``, no two titles agree, and the
    guess says "comparable" when nothing here is. That reading makes the
    featurette a fragment of the film and drops it. Pass the media type.

    Order matters, and one step of it was learned from a disc rather than
    reasoned out. Degenerate titles are junk whatever else they look like.
    **Duplicates go next, before play-alls**: Challenge of the Superfriends
    disc 1 offers episode 1 twice -- once with two audio streams and once with
    one, the commentary -- and both use cell 1002/1. Left in, that second copy
    makes the play-all's children overlap, the disjointness test fails, and
    the play-all is published while seven episodes are dropped as fragments.
    Measured 2026-09-12. Play-alls are then recognised before their parts can
    be mistaken for fragments of them.
    """
    verdict = {t.index: CONTENT for t in titles}
    comparable = (clips_discriminate(titles) if clips_global is None
                  else bool(clips_global))

    for t in titles:
        if is_degenerate(t):
            verdict[t.index] = DEGENERATE

    live = [t for t in titles if verdict[t.index] == CONTENT]

    # Same content authored twice, kept once, richer stream set surviving --
    # Hancock's pair carry 23 streams and 15.
    #
    # What "the same" means depends on whether the clip lists can be compared.
    # Where they can, it is the same clips and the same runtime. Where they
    # cannot it is the same runtime *and the same declared size to the byte*,
    # which is a much sharper test than runtime alone and was measured on the
    # disc that needed it: side A's episodes 1 and 2 both run 21:43 and are
    # 979,191,808 and 979,425,280 bytes -- two different episodes -- while
    # side B's 21:37 pair are 1,006,301,184 bytes each, one being the other
    # with a commentary track. Runtime alone would have dropped an episode.
    seen: dict = {}
    for t in sorted(live, key=lambda x: (-x.streams, -x.size_bytes)):
        key = ((tuple(segments(t)), t.duration) if comparable
               else (t.duration, t.size_bytes))
        if key in seen:
            verdict[t.index] = DUPLICATE
        else:
            seen[key] = t

    live = [t for t in titles if verdict[t.index] == CONTENT]

    if comparable:
        # On a one-film disc the feature itself must never read as a play-all:
        # a film authored as two clips that the disc also offers separately is
        # exactly that shape, and the film would be dropped in favour of its
        # halves. Only the longest title is protected -- Mrs. Doubtfire's
        # Blu-ray does carry a real play-all, 36:55 of the seven featurettes
        # behind it, and dropping the play-all rule altogether would publish
        # that lump instead of the seven.
        protect = max(live, key=lambda x: x.seconds).index if (
            live and kind == model.KIND_MOVIE) else None
        for index in play_all_parts(live):
            if index != protect:
                verdict[index] = PLAY_ALL
    if kind in model.EPISODIC_KINDS:
        for index in play_all_by_runtime(live):
            verdict[index] = PLAY_ALL

    live = [t for t in titles if verdict[t.index] == CONTENT]

    # A fragment is a slice of a longer title, which is a statement about
    # clips. Where they cannot be compared there is no evidence for it and
    # nothing is called a fragment: DVDs do not offer slices of the feature as
    # titles anyway, and inferring it from a cell range every title spells the
    # same way is what dropped seven episodes.
    if comparable:
        sets = {t.index: frozenset(segments(t)) for t in live}
        for t in live:
            clips = sets[t.index]
            if not clips:
                continue
            if any(clips < sets[o.index] and o.seconds > t.seconds
                   for o in live if o.index != t.index):
                verdict[t.index] = FRAGMENT

    return verdict


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
