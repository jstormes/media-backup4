# Playlist obfuscation

When a disc's playlist metadata is not the truth about what it stores: decoy
playlists that hide the feature among hundreds of permutations, and declared
durations that describe a playback experience rather than the disc's contents.
Both mislead this project; one wastes terabytes, the other fails good backups.

Measured 2026-09-11 against MakeMKV 1.18.4, a Pioneer BDR-212D on `/dev/sr3`,
and a real disc: **Saban's Power Rangers** (2017, Lionsgate, US retail).
Cross-checked against **Mortal Engines** (2018, Universal) as a negative
control and against every disc already in the archive. Nothing here is
inferred from vendor documentation; the counts come from the collection
metadata this project wrote.

## The short version

Some studios author the feature as **one real playlist plus hundreds of
decoys**. Every playlist references the same video segments; only one lists
them in the right order. BD-J code on the disc tells a real player which one.
A ripper that cannot run that code sees hundreds of equally plausible
feature-length titles.

This project copies every title a scan reports ([`selection.choose`](../../src/media_backup/makemkv/selection.py)),
deliberately, so that no film is ever silently dropped. On an obfuscated disc
that policy writes **the whole decoy set**. On Power Rangers that is a
projected 7.5 TB from a 46.6 GiB disc, onto a 1.8 TB volume shared with three
other running jobs.

That is the one case where "it is paid in disk, which is recoverable" stops
being true, and it is the only reason this document exists.

## What it looked like

`SABAN'S_POWER_RANGERS`, BD-50, 46.6 GiB, scanned to **308 titles**:

| | |
|---|---|
| Titles at 2:03:58, 16 chapters, 24.5 GB | **287** |
| Segments those 287 draw on | 13, always the same: `501,502,504,505,506,507,508,509,510,511,512,513,514` |
| Distinct orderings of those 13 segments | **287** — every title a unique permutation |
| The real one | title **289**, `00988.mpls` |
| Its order | `505,501,507,502,506,509,511,513,508,504,514,512,510` |
| Remaining 21 titles | extras — a 2:20:12 documentary (`00815.mpls`, segments 2563–2571), a 33-minute featurette reel, and the individual `.m2ts` behind them |

The feature is 2:03:58 and the decoys are 2:03:58. There is no length, size,
chapter count or stream count that separates them — all 287 report `24.5 GB`,
`16` chapters, `11` streams. **The order of the segment list is the only field
that differs.**

MakeMKV knows the disc is obfuscated. It loads Java for it:

```
MSG:3344 "Using Java runtime from /usr/lib/jvm/java-25-openjdk-amd64/bin/java"
```

and it still could not resolve the playlist: a full rescan on 2026-09-11
(`info disc:0`, 41 s, 59,696 lines) contained **zero** occurrences of `FPL` or
`MainFeature`. When MakeMKV *can* resolve one it renames the title
`FPL_MainFeature` and moves it to the top of the list. Here it produced
nothing, so there was no engine-side answer to read.

### What it cost before it was caught

Eight rip invocations over 1h40m, against a queue of 308:

| titles | outcome |
|---|---|
| 245 | failed — drive reported `NOT READY:MEDIUM NOT PRESENT - TRAY OPEN`, then I/O errors |
| 135, 0–8 | failed instantly, `Failed to open disc`, while the drive respun |
| 9, 10, 11, 12 | **succeeded** — 4 × 22 GiB of decoy |
| 13 | failed — 5,438 `HashCheck Error` on segments 00501/00502/00505/00507 |
| 14 | cancelled by the operator mid-write at 16 GiB |

106 GiB written, none of it the film. Left alone it would have run ~4 days and
filled the volume in about 23 hours, taking three concurrent jobs with it.

The hash-check failures are worth noting as **a symptom, not a disc defect**.
Title 14 read the same four segments minutes later without complaint, and
title 289 read them clean. Decoy playlists can point at segment ranges that do
not decrypt cleanly; a burst of `HashCheck Error` confined to a disc that also
shows the structural signature below is evidence of obfuscation, not of rot.

## Two different things share this name

[SPEC §16.4](../SPEC.md) documents playlist obfuscation as *structural and
visible*, measured on the Hancock Blu-ray: a decoy there is a playlist of a
hundred play items all pointing at the **same** clip — a feature's duration and
no other property of one. MakeMKV filters those itself; it reported `TCOUNT:4`
against 175 playlists on that disc.

Power Rangers is the other kind, and §16.4's conclusion does not reach it:

| | Hancock-style | Power-Rangers-style |
|---|---|---|
| Decoy is | ~100 play items on 1–2 distinct clips | the 13 real clips, reordered |
| Duration | right | right |
| Distinct clips | 1–2, an obvious tell | 13, same as the feature |
| Chapters | 1 or 100, wrong | 16, same as the feature |
| MakeMKV filters it | **yes** — `TCOUNT:4` from 175 | **no** — 308 titles survive |

The Hancock decoys are degenerate and MakeMKV discards them. The Power Rangers
decoys are *structurally indistinguishable from the feature* on every field a
scan reports except the ordering of the segment list. That is what makes them
survive the filter, and it is why this needs its own detection rather than
relying on `TCOUNT`.

## The signal that works

**A permutation class**: a group of titles whose segment lists are the same
multiset in different orders.

Two titles with the *identical* map are the same content authored twice — the
project already expects that and copies both (Hancock, 39 titles, 88 GB where
the film is 44). Two titles that are *permutations* of one multiset contain
the same footage rearranged. No legitimate authoring produces that. Seamless
branching, the thing that makes two cuts of one film, gives each cut segments
the other lacks — which is exactly what
[`selection._is_branching`](../../src/media_backup/makemkv/selection.py)
already tests for.

Measured across the whole archive, snapshot 2026-09-11 08:42 — **106 discs,
966 titles**, DVD and Blu-ray, including every case that broke the previous
decoy detector:

| disc | titles | largest permutation class |
|---|---|---|
| **power rangers** (BD) | 308 | **287** |
| Hancock (BD) — feature authored twice | 39 | 1 |
| Starship Troopers 5-movie set (DVD) | 16 | 1 |
| Sci-fi Collection (DVD double feature) | 2 | 1 |
| Challenge of the Superfriends (DVD, episodes) | 8 | 1 |
| Scooby-Doo Holiday Collection (DVD, episodes) | 8 | 1 |
| mortal engines (BD) | 27 | 1 |
| *every other disc in the archive* | — | **1** |

One disc separates from 105 others, 287 against 1. The threshold can be put
anywhere in that gap; put it high, because the cost of being too low is
refusing a good disc and the cost of being too high is copying extra, which is
the current behaviour anyway.

## Signals that do not work

Recorded because each of these looked convincing and two of them were briefly
believed during this investigation.

**`designator` being empty is not a signal.** It is a DVD-only field. Counted
across the archive:

```
optical_bd      0 set /  627 titles
optical_bd_r    0 set /    1 titles
optical_dvd   322 set /  338 titles
```

Every Blu-ray has it empty, obfuscated or not. Mortal Engines has all 27 empty
and is a perfectly clean disc.

**`Using Java runtime` is not a signal.** MakeMKV loads a JRE for BD-J discs
generally. It appears once in the Mortal Engines log too.

**Duration clustering alone is not a signal.** A TV disc is a pile of titles
with near-identical runtimes; that is what a TV disc *is*. Clustering only
becomes meaningful once it is qualified by the shared-multiset test — which is
what separates 287 identical-length decoys from 8 identical-length episodes.

**Absence of `FPL_MainFeature` is weak on its own.** It is absent on plenty of
ordinary discs that were never obfuscated. It is worth recording as
corroboration, not acting on alone.

## What the application should do

Not resurrect the old decoy detector. That detector tried to answer *which
title is the feature*, got it wrong in both directions and failed silently —
the reasoning is in
[`selection.py`](../../src/media_backup/makemkv/selection.py)'s module
docstring and it still stands. A scan cannot tell you what a title *is*.

But there is a narrower question a scan *can* answer: **does this disc have a
shape that makes copying everything ruinous?** That does not require picking a
winner. It requires noticing 287 permutations and stopping.

The machinery for stopping already exists and is currently unreachable:
`model.ERR_DECOY_TITLES`, `gui_app.NEEDS_OPERATOR`, the "Needs you" state and
its amber row tag are all still wired. Only `selection.choose` no longer emits
the error.

### Guard 1 — the structural detector

```python
# in selection.py

#: Distinct orderings of one identical segment multiset before a disc is
#: treated as obfuscated. Power Rangers measured 287; every other disc in a
#: 106-disc, 966-title archive measured 1. Set well above the noise: a false positive
#: stops a good disc, a false negative only copies extra, which is already
#: the policy.
OBFUSCATION_ORDERINGS = 8


def permutation_classes(titles) -> dict[tuple, list]:
    """Group titles by their segment multiset, ignoring order.

    Same multiset, same order  -> content authored twice. Expected; copied.
    Same multiset, differing order -> the same footage rearranged. Decoys.
    Different multisets -> separate works, or branched cuts. Not this.
    """
    classes = defaultdict(list)
    for title in titles:
        segs = segments(title)           # the existing helper; handles DVD "1-28"
        if segs:
            classes[tuple(sorted(segs))].append(title)
    return classes


def obfuscation(titles):
    """The largest permutation class, if one is big enough to matter.

    Returns (titles_in_class, distinct_orderings) or None.
    """
    worst = None
    for multiset, members in permutation_classes(titles).items():
        orderings = {tuple(segments(t)) for t in members}
        if len(orderings) >= OBFUSCATION_ORDERINGS:
            if worst is None or len(orderings) > worst[1]:
                worst = (members, len(orderings))
    return worst


def choose(titles) -> Selection:
    usable = [t for t in titles if t.seconds > 0]
    if not usable:
        return Selection(False,
                         reason="the disc scan reported no titles with a duration",
                         error_kind=model.ERR_NO_FEATURE)

    found = obfuscation(usable)
    if found:
        members, orderings = found
        # Do NOT pick one. Do NOT drop the rest. Hand the disc to a person
        # with everything needed to identify the real playlist by hand.
        return Selection(
            False,
            titles=tuple(usable),        # keep the full list for the operator
            reason=(f"{orderings} titles are the same {len(segments(members[0]))} "
                    f"segments in different orders, all {members[0].duration} "
                    f"-- playlist obfuscation. One is the feature and the scan "
                    f"cannot say which. See docs/makemkv/playlist-obfuscation.md"),
            error_kind=model.ERR_DECOY_TITLES)

    return Selection(True, tuple(sorted(usable, key=lambda t: -t.seconds)))
```

`needs_operator` then stops being hardwired `False`:

```python
    @property
    def needs_operator(self) -> bool:
        return self.error_kind == model.ERR_DECOY_TITLES
```

and the GUI's existing `NEEDS_OPERATOR` path renders it as "Needs you" in
amber, with no further change.

### Guard 2 — the budget check

Independent of the detector, and the more important of the two: it catches an
obfuscated disc the pattern *fails* to recognise, and it catches any other
runaway. This is what would actually have saved the three concurrent jobs.

```python
# before the first save of a disc, in the runner

def affordable(selection, dest, headroom=1.15):
    """Will what the scan promised fit, with room to spare?

    expected_bytes() already exists and sums the scan's own per-title sizes.
    Power Rangers promised 308 x 24.5 GB = 7.5 TB against 1.5 TB free.
    """
    need = expected_bytes(selection) * headroom
    free = shutil.disk_usage(dest).free
    if need > free:
        return Selection(False,
                         titles=selection.titles,
                         reason=(f"the scan promises {need/1e12:.1f} TB and "
                                 f"{free/1e12:.1f} TB is free"),
                         error_kind=model.ERR_NO_SPACE)
    return selection
```

`ERR_NO_SPACE` already exists. A disc that trips this should stop before the
first title, not partway through — the damage is done by the twentieth title,
not the first.

### What this would change in the SPEC

Implementing Guard 1 is a deliberate narrowing of a stated policy, not a bug
fix, so it does not happen quietly:

* **§9 "Save every title the scan reports. That is the whole policy."** would
  gain a second refusal alongside the existing "no title with a duration" one.
  The distinction to hold on to is that §9 forbids *deciding which title is the
  feature*, and this does not decide — it refuses, with the full title list
  preserved for the operator. The forbidden thing is silently picking; the
  proposed thing is loudly declining.
* **§13's note** that "with the selection policy of §9 nothing currently
  produces this state" would stop being true — the needs-a-person state would
  have exactly one producer.

Guard 2 changes no policy at all. It refuses a disc that cannot fit, which is
`ERR_NO_SPACE` doing the job it already has.

### Worth having either way

* **Log the scan.** The title inventory currently goes into `collection.json`
  but the `info` output itself is not kept, so `FPL_MainFeature` — present or
  absent — leaves no record. It is one file per disc and it is the only
  evidence of what the engine concluded.
* **Record the segment map in the failure reason.** It is what an operator
  needs to search for, and it is already in `model.Title.segments`.

## Recovering an obfuscated disc by hand

This is what was actually done for Power Rangers on 2026-09-11.

1. **Get the segment map for the real playlist.** The MakeMKV forum has
   verified maps for most affected releases; search the film's name. For this
   one, [Power Rangers 2017](https://forum.makemkv.com/forum/viewtopic.php?t=16258)
   gives US retail `00988.mpls` as
   `505,501,507,502,506,509,511,513,508,504,514,512,510`, and a different map
   for the Redbox pressing. **Pressings differ — match on the segment map, not
   on the playlist filename or the title number.**

2. **Rescan and find the matching title.** Title ids are positions in the list
   MakeMKV is showing, so a stale id from an earlier scan can point at a
   different playlist. Rescan, then match:

   ```bash
   makemkvcon -r --progress=-same --cache=1024 info disc:0 > scan.txt
   grep -n '505,501,507,502,506,509,511,513,508,504,514,512,510' scan.txt
   # TINFO:289,26,0,"505,501,..."   -> title 289
   ```

3. **Rip that one title.** Build the argv with the project's own
   `command.mkv_argv` rather than by hand, so the `bwrap` drive isolation
   matches what the runner does:

   ```python
   command.mkv_argv(cfg, 0, dest, 289, device="/dev/sr3")
   ```

4. **Verify the picture, not just the map.** The segment map proves the
   ordering matches a map someone else published. It does not prove the file
   plays. Scrub a few minutes in and check the cuts land where they should
   before publishing.

The result, for comparison against a decoy: 22.4 GiB, `2:03:58`, h264
1920×1080, 16 chapters, TrueHD + 5 × AC3 + 3 × PGS, and **zero** hash-check or
SCSI errors during the save — against 5,438 hash errors on decoy title 13
minutes earlier on the same drive and disc. The scan promised 11 streams and
the file has 10; that is the default selection string dropping a `havecore` or
`havemulti` track, per [`track-selection.md`](track-selection.md), not a fault.

Note that the decoys are the *same duration* as the feature — `7438.472` s on
both title 9 and title 289. Duration confirms nothing here. Only the ordering
does.

## Declared duration is not what is on the disc

A second way playlist metadata misleads, found 2026-09-11 on a disc with no
obfuscation at all. Same root cause as everything above — **the scan describes
what a player would present, not what the disc physically stores** — but here
it makes the app fail a backup that is perfectly good.

### The symptom

Mortal Engines, 27 titles, 56.9 GB, reported `failed`:

```
state:  failed / "1 of 27 saved title(s) run short of what the disc says they are"
error:  copy_failed     exit_code: 0     progress: 65536/65536 (100%)
read_errors: 0          hash_errors: 0   5036 ("Copy complete") x 27
```

MakeMKV reported every title complete. The verdict comes from the app's own
check in `runner._match_output`: read each finished `.mkv` back and compare its
real duration against the scan's declared duration, `duration_tolerance_s = 10`.

### What it measured

26 of 27 agree within **one second**. One does not:

| title | source | disc says | file says | delta |
|---|---|---|---|---|
| t1 (the feature) | `00612.mpls` | 7701 s | 7701.1 s | +0.1 |
| t7 | `01150.mpls` | 1303 s | 1303.5 s | +0.5 |
| t13 | `01156.mpls` | 1579 s | 1579.1 s | +0.1 |
| *(23 more)* | | | | within 1 s |
| **t0** | `00010.mpls` | **330 s** | **17.7 s** | **−312.3** |

`t0` is the **copyright warning reel**: video-only, no audio, no subtitles,
17.7 s, 8.4 MB, 1080p, 4 chapters. A frame at 2 s is a German legal
disclaimer; at 9 s, the English "COPYRIGHT NOTICE — THIS COPYRIGHTED WORK HAS
BEEN LICENSED FOR PRIVATE USE ONLY…".

The playlist declares **330 s across 66 chapters** — the full multi-language
warning set, from which a player shows the card matching its region. The disc
stores segment 186 once: 17.7 s, four cards. 330 ÷ 17.7 = 18.6.

So the check compared *what a player would sit through* against *what exists on
the disc*, and failed a 56.9 GB backup over an 8 MB legal notice.

### Why it is certainly not a truncated copy

A copy that stopped early does not emit `MKV_COMPLETE` and does not reach
`progress_max`. This run did both, 27 times, with zero read errors — and the
2:08:21 feature landed at +0.1 s. Verified independently: 14 streams (TrueHD +
7 AC3/E-AC3 + 5 PGS), four decode spot-checks clean, end credits at 7650 s of
7701.

### Title shapes that trip this

Any playlist declaring more than the disc stores. All common, none faults:

* **multi-language warning reels** — one segment, presented per region
* **looping menu backgrounds** — a short clip declared as minutes
* **multi-angle titles** — angles counted once each in the declared length
* **"play all" chapter-index playlists** — the same segments re-referenced

### The fix

Keep the check — it is the only real completeness test the project has, and
the reasoning behind it in `_match_output`'s docstring is sound. Narrow what it
is allowed to *fail* on.

The discriminator is already in hand and costs nothing: the runner does **one
`makemkvcon` invocation per title**, so it knows whether MakeMKV vouched for
each one individually. It just does not currently keep that — `_save_one`
consumes every line into one shared `BackupObservation`, so `obs.message_codes`
ends up as `{'5036': 27, ...}` for the attempt with no way back to which title
each code came from.

```python
# runner._save_one -- return per-title success, not just the exit code.
# Counting 5036 across the title's own loop avoids touching _consume, which
# returns None and accumulates into the shared obs.

def _save_one(self, index, title, obs, log, started):
    ...
    before = obs.message_codes.get(messages.MKV_COMPLETE, 0)   # 5036
    for line in self._iter_lines(process, ...):
        self._log(log, line)
        self._consume(line, obs)
        self._check_watchdogs(obs, started)
    saw_complete = obs.message_codes.get(messages.MKV_COMPLETE, 0) > before
    return process.wait(), saw_complete


# runner._save_titles -- keep the set MakeMKV vouched for

    vouched: set[int] = set()
    for position, title in enumerate(chosen.titles):
        code, complete = self._save_one(index, title, obs, log, started)
        codes.append(code)
        if complete and code == 0:
            vouched.add(title.index)
    obs.vouched_titles = vouched


# runner._match_output -- a short title only counts against the disc when
# MakeMKV did NOT claim it finished

    elif title.seconds and measured < title.seconds - tolerance:
        if index in obs.vouched_titles:
            # MakeMKV saved this title completely and it still runs short:
            # the playlist declared more than the disc stores. Record it so
            # the operator can see it; do not fail the disc for it.
            obs.titles_over_declared += 1
            logger.info("job %s: %s runs %.0fs, playlist declares %ds -- "
                        "declared-only content, not a short copy",
                        self.request.job_id, name, measured, title.seconds)
        else:
            obs.titles_short += 1
```

`judge()` then fails on `titles_short` exactly as it does today, and reports
`titles_over_declared` as detail rather than as a fault. A copy genuinely cut
off mid-title still fails: it would not be in `vouched_titles`.

**What this deliberately does not do** is compare durations to decide which
titles are "real". That is the §9 mistake in a new costume. It only stops the
app from calling a disc failed on the strength of a number the disc never
promised to honour.

### The cost of leaving it

Measured: one 56.9 GB backup marked `failed` and excluded from `finished/`,
requiring a hand override to publish. Every disc carrying a multi-language
warning reel — which is most commercial Blu-rays — is a candidate to hit this
the same way.

## How this was got wrong

Both errors happened during the investigation on 2026-09-11 and are recorded
because the shape of them is the point.

**First: reading a coincidence as a cause.** The empty `designator` on all 308
titles was presented as evidence that MakeMKV's resolver had failed, supported
by the observation that other collections populate the field normally. They
do — because they are DVDs. The field is empty on all 627 Blu-ray titles in
the archive. A count that had been run one way (across collections) needed to
be run the other way (across media types) before it meant anything.

**Second: reporting a per-invocation fact as a per-disc one.** `Using Java
runtime` was read as confirmation that the disc was obfuscated. It appears in
the Mortal Engines log too. Checking the negative control took one `grep` and
was done only after the claim had already been made.

The check that catches both: **before a field is evidence, find out what it
looks like on a disc that is fine.**
