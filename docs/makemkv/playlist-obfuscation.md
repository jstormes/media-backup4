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

## What the application does

Implemented 2026-09-11. Two guards, neither of which picks a title.

Not a resurrection of the old decoy detector. That one tried to answer *which
title is the feature*, got it wrong in both directions and failed silently —
the reasoning is in
[`selection.py`](../../src/media_backup/makemkv/selection.py)'s module
docstring and it still stands. A scan cannot tell you what a title *is*.

But there is a narrower question a scan *can* answer: **does this disc have a
shape that makes copying everything ruinous?** That needs no winner picked. It
needs 287 permutations noticed, and a stop.

The machinery for stopping was already there and unreachable:
`model.ERR_DECOY_TITLES`, `gui_app.NEEDS_OPERATOR`, the "Needs you" state and
its amber row tag. Only `selection.choose` had stopped emitting the error.

### Guard 1 — the structural detector

`selection.permutation_classes` groups titles by clip list ignoring order;
`selection.obfuscation` returns the largest class whose distinct orderings
reach `OBFUSCATION_ORDERINGS` (8). `choose` refuses on that with
`ERR_DECOY_TITLES`, handing back **the full title list** — whoever resolves it
must match a published clip map against those titles, and cannot do that from
a count. `Selection.needs_operator` is true for that error and nothing else,
so the GUI renders "Needs you" in amber rather than "Failed".

Run against every disc in the archive, 102 with titles: **2 refused** (Power
Rangers 308 titles, Knives Out 283), **100 accepted**. No false positives.

**Its blind spot:** it needs clip lists. A scan with no `TINFO:26` gives it
nothing to group, and such a disc is copied in full exactly as before —
`tests.test_backup_runner.test_forty_titles_of_one_length_are_copied_rather_than_refused`
pins that. Every real obfuscated disc seen so far reports its clip lists.

### Guard 2 — the budget check

Independent of the detector, and the one that would actually have saved the
three concurrent jobs: it catches an obfuscated disc the pattern *misses*, and
any other runaway.

The runner already checked free space — against `req.disc_size_bytes`. Power
Rangers is a 46.6 GiB disc and passed that comfortably; the check ran before
the scan and could not have known the run would then queue 308 titles at
24.5 GB apiece. So a second check runs once a selection exists, against
`selection.expected_bytes(chosen)`, and refuses with `ERR_NO_SPACE` before the
first title rather than during the twentieth.

It is deliberately conservative: `expected_bytes` is MakeMKV's estimate of a
title's size *on the disc*, which a remux comes in under (0.84 on a Blu-ray),
so the check over-states what will be written. That is the right direction for
a guard whose job is catching runs that are multiples over, not shaving
margins — but it does mean a disc needing very nearly all the remaining space
can be refused when it would have fitted.

`free_bytes` is now injectable into `BackupRunner` (`free_space=`), for the
same reason `spawn` is: a test asserting what a run does must not also assert
how much room the host has. The suite runs on an 8 GB tmpfs while the Hancock
fixture alone declares 42 GB of titles, so without that the result depended on
the machine.

### What changed in the SPEC

§9's "save every title the scan reports" gains a second refusal beside the
existing "no title with a duration". The distinction worth holding: §9 forbids
*deciding which title is the feature*, and this does not decide — it declines,
with the full list preserved. Silently picking is the forbidden act; refusing
out loud is not.

§13's note that "with the selection policy of §9 nothing currently produces
this state" is no longer true — the needs-a-person state has exactly one
producer.

### Worth having either way

* **Log the scan.** The title inventory goes into `collection.json` but the
  `info` output itself is not kept, so `FPL_MainFeature` — present or absent —
  leaves no record. One file per disc, and the only evidence of what the
  engine concluded.
* **Record the clip map in the failure reason.** It is what an operator
  searches for, and it is already in `model.Title.segments`.

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

## Collecting discs, so a rule can be found

Resolving a disc by hand works and does not accumulate. Every obfuscated disc
costs the same afternoon as the last one, and nothing learned on Power Rangers
made Knives Out cheaper.

What would scale is knowing **how the disc itself decides**. A real player is
told which playlist to run — by navigation commands in `MovieObject.bdmv`, or
by the BD-J application in `BDMV/JAR`. That answer is on the disc, in the
clear, and it is small.

`media_backup.forensics` captures it.

### Why this is cheap

**AACS encrypts `BDMV/STREAM` and nothing else.** Playlists, clip info, BD-J
objects and the Java archives are all readable without a key — this is already
recorded in [SPEC §16.4](../SPEC.md). Measured on Knives Out, 2026-09-11:

```
/BDMV/STREAM        59 files   48,974 MB   encrypted, never captured
everything else   1531 files       80 MB   captured whole
```

**And it needs no root.** Mounting does, and `udisks` automount is
deliberately off for `sr*` so a disc is never mounted under a running
`makemkvcon`. So `media_backup.udf` reads the UDF filesystem straight off the
block device through `libudfread`, which the `cdrom` group already permits and
which ships with `libbluray`.

One trap worth keeping: libudfread's `UDF_DT_DIR` is **1**, not the 4 that
`dirent.h` uses. A walk keyed on 4 finds no directories at all and cheerfully
reports a Blu-ray as ten files.

### Using it

```bash
python3 -m media_backup.forensics capture /dev/sr3
python3 -m media_backup.forensics truth <capture-dir> \
    --playlist 00988.mpls --how forum-map --evidence 'matched the US retail map; verified by watching'
python3 -m media_backup.forensics summary
```

`capture` refuses a drive that another process already has open — a capture
that thrashes a four-hour rip is a poor trade for 80 MB. `--force` overrides.

### Where the captures live

`Config.forensics_path`, defaulting to `media_path/forensics` — beside
`collections/`, `finished/` and `cancelled/`, and created with them by
`ensure_directories`. Unlike `finished_path` and `cancelled_path` it is never
renamed into, so it is free to sit on another filesystem, and there is an
argument that it should:

```
KNIVES_OUT-7051b1979008a925/
  disc/        80.0 MB   the navigation tree, verbatim
  scan/         2.2 MB   the raw makemkvcon info transcript
  capture.json  308 KB   every file on the disc: size, sha256, copied or skipped and why
  jars.json     404 KB   5 archives, 2532 entries, per-entry sha256, obfuscation flag
  analysis.json  48 KB   the pool, the classes, the titles outside it
  truth.json      ~1 KB  the answer, once a person has found it
```

**82 MB of that is reproducible** by putting the disc back in a drive and
capturing again. `truth.json` is not: it is a record of research, it is about
a kilobyte, and if the volume is ever repurposed it is the only part that
cannot be rebuilt. Worth keeping somewhere durable — the repository, or
rsynced to the media server with everything else.

A bundle holds the disc's navigation tree verbatim under `disc/`, the raw
`makemkvcon info` transcript under `scan/`, an inventory of every file with
sizes and SHA-256 (including what was skipped and why), an index of every
Java archive, the obfuscation analysis, and — once someone has worked it out —
`truth.json`.

**`truth.json` is the half that cannot be automated and the half the corpus is
worthless without.** A hundred captured discs with no confirmed answers teach
nothing. It records `how` the answer was reached — a forum clip map, the
engine's own `FPL_MainFeature`, or watching it — because those are not equally
strong, and a rule trained on the weak ones is a rule that loses a film.

### What the scan itself now preserves

The raw `info` transcript is kept whole. `collection.json` records a parsed
title inventory but never the transcript, so whether MakeMKV resolved an
`FPL_MainFeature` — the engine's own answer to this exact question — has been
leaving no trace at all. Knives Out: `fpl_main_feature: false`.

### What has already been tried, and failed

Recorded so the next person does not spend the afternoon again. All measured
on Knives Out, 2026-09-11:

| attempt | result |
|---|---|
| `#####.mpls` strings in the JARs | **none.** 880 classes across two 4 MB archives, zero literal playlist filenames. BD-J addresses playlists numerically. |
| `PlayPL`-shaped navigation commands in `MovieObject.bdmv` | **none matched a playlist on the disc**, across 45 KB. Either the parse is wrong or this disc drives playback from BD-J — `index.bdmv` would settle it. |
| five-digit ASCII runs in the BDJO files | **a trap.** `00004.bdjo` contains `00006`, and `00006` is both a playlist *and* `/BDMV/JAR/00006.jar`. Playlist ids, JAR ids and application ids share one five-digit namespace. |

And one finding that shapes the work: **the BD-J applications are themselves
obfuscated.** 707 of 733 classes are named `a`, `aa`, `ab`; the short-name
ratio is 0.965. The capture flags this per archive (`name_obfuscated`), because
anyone planning to read the application should know beforehand that the names
carry nothing and structure is all there is.

Knives Out also keeps a second copy of its application at
`/BDMV/JAR/03000/809ad4ac00000` — no extension, 707 classes. The indexer finds
archives by magic rather than by name for that reason.

### What a corpus could answer

None of these is testable on two discs. All become testable on a dozen:

* Is the real playlist reachable from `index.bdmv` → BDJO → the application's
  accessible-playlist table, when that table is parsed properly rather than
  grepped?
* Do the decoys share a structural tell the feature lacks — a clip ordering
  that no `.clpi` timestamp sequence supports, say, which would be decidable
  from the captured clip info alone?
* Is the real playlist's position predictable within the pool (highest
  `.mpls` number, lowest, first by index)? Power Rangers' answer was
  `00988.mpls` against decoys from `00009` upward.
* Does the studio or the authoring house predict the scheme? Both discs so far
  are Lionsgate.

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

**The first attempt at this was wrong, and the test suite caught it.**

The reasoning above ran: a copy that stopped early would not emit
`MKV_COMPLETE`, so a short title MakeMKV vouched for must have been
over-declared by its playlist. The runner does one invocation per title, so
tracking which titles MakeMKV reported complete is cheap, and the check could
then fail only on titles it did not vouch for.

`test_a_copy_that_stopped_early_fails_despite_a_success_message` says
otherwise, and its name says it outright: a truncated copy **does** arrive
with a success message. That is why the duration check exists at all. Keying
the check on MakeMKV's own claim would have re-opened precisely the hole the
check was built to close. The premise was never measured — it was assumed from
one disc where the two happened to coincide.

What separates the two cases is not the message. It is **how much of the disc
is short**:

```python
# outcome.judge, step 8

    if obs.titles_short:
        summary = (f"{obs.titles_short} of {obs.files_written} saved "
                   f"title(s) run short of what the disc says they are")
        if obs.titles_short >= obs.files_written:
            return Verdict(FAILURE, summary, detail)
        return Verdict(PARTIAL, summary, detail)
```

Every saved title coming up short is a broken copy and fails. Some of them
coming up short is reported and handed to the operator.

This is not a new policy — it is step 6's policy, applied consistently. A
*missing* title has always been `PARTIAL`, on the stated grounds that "one
lost extra is not the same news as a lost feature, and the operator decides
which this was". A *short* title was `FAILURE`. Nothing justified the
asymmetry: both mean some content did not fully arrive, and neither can be
attributed to the feature or to an extra without the title-identity guess
that SPEC §9 forbids.

Mortal Engines is now `partial`, which `Verdict.is_good` already treats as
worth keeping, so the disc finishes and publishes. The truncation test still
fails its run, because there one title of one is short.

**What this deliberately does not do** is compare durations to decide which
titles are real. Two discarded candidates, both measured against the archive
before being dropped:

* *chapters per distinct clip* — Mortal Engines' warning reel is 66 chapters
  over one clip, which looked decisive until the archive said Superman's
  actual feature is 44 over one and Road House's is 28. No gap.
* *absolute size* — an 8 MB title is obviously not a feature, but saying so
  in code is the §9 guess wearing a different hat.

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
