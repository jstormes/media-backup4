# Publishing to Jellyfin: the second process

Turning the archive into a Jellyfin library is a **separate, agent-driven
process**. This document is the contract between the two: what this project
guarantees it, what it has to work out for itself, and what it must never do.

`library-layout.md` describes the layout it has to produce. This describes the
handoff.

## Why it is separate, and why it is an agent

The archive is organised for recovery and evidence: UUID directories,
MakeMKV's own filenames, every attempt and log kept. A media server wants
`Hancock (2008)/Hancock (2008) - Theatrical Cut.mkv`. Those are different jobs
with different failure modes, and collapsing them would mean the ripper could
corrupt the archive while trying to name something nicely.

It is an agent rather than a script because the remaining work is judgement,
not transformation. The disc says its name is "Hancock" and that it holds two
cuts. It does not say the year is 2008, that "Hancock" is a film rather than a
series, that the longer cut is sold as the Unrated Extended Version, or what
its IMDb id is. Getting from one to the other means looking things up,
weighing what comes back, and knowing when to stop and ask -- which is the
work an agent is for and a rename script is not.

## The boundary

The publishing process **reads** the archive and **writes** the Jellyfin tree.
It does not write into the archive. Nothing downstream of the rip may modify
`collection.json`, the media files, the logs or the rejected attempts.

Read from `finished/<collection-uuid>/`, not `collections/`. A collection under
`collections/` is still open: discs may yet be added, retried or abandoned.
Finishing is the operator saying it is done, and it is an atomic rename, so a
directory appearing under `finished/` is complete by construction.

## What it is given

`collection.json` at the root of each finished collection. The fields that
matter here:

| Field | Meaning |
|---|---|
| `title`, `identifier` | What the operator called the set, and the barcode off the case. `identifier` names the *physical release*, not the work. |
| `expected_disc_count` | What the set was declared to hold. A guard against publishing an incomplete box set. |
| `kind` | What the operator says it holds: `movie`, `movies`, `special`, `series`, or empty for "not sure". Pass it to `selection.classify()`; it decides whether a play-all is expected and whether equal runtimes are normal. |
| `discs[]` | One entry per physical disc, in `ordinal` order. |
| `discs[].makemkv_disc_name` | MakeMKV's name for the disc -- "Fresh Horses" where the volume label says `DVD_VIDEO`. The best name available. |
| `discs[].state` | Only `done` was backed up. `failed`, `abandoned` and anything else must not be published. |
| `discs[].media` | `optical_dvd`, `optical_bd` or `optical_bd_r`. **This is what says whether clip lists mean anything across titles** -- pass `media.startswith("optical_bd")` as `classify`'s `clips_global`. Do not infer it from the clip lists; measured wrong on one disc in 161. |
| `discs[].titles[]` | The titles the scan found, feature and extras alike. |
| `titles[].output_file` | **The file on disk**, reconciled after the run. This is the link from metadata to bytes; do not reconstruct it from the title index. |
| `titles[].duration`, `size_bytes`, `chapters` | For telling a feature from an extra, and the longer cut from the shorter. |
| `titles[].segments` | The clip list. On a Blu-ray, global clip ids, comparable across titles. On a DVD, **a cell range local to this title** -- every DVD title's starts at 1 and comparing two of them concludes nothing. See `makemkv/selection.py`. |
| `titles[].streams` | How many streams the scan found. What tells the richer authoring from the poorer when two titles are the same content. |
| `discs[].attempts[]` | Evidence. `error_kind` says why something is not `done`. |

Media files are at
`finished/<collection>/discs/<disc>/data/<output_file>`.

## What it has to decide

**Start from `selection.classify(titles, kind, clips_global=...)`.** It returns
one of `content`, `play_all`, `fragment`, `duplicate`, `degenerate` per title,
and it is the same clip-list reasoning described below rather than a second
opinion about it. What is left as `content` is what there is to name. Anything
else is reported as deliberately left in the archive, by title and reason.

Feed it both extra inputs or it will be wrong in the direction that loses
content: the collection's `kind`, and `clips_global=disc.media.startswith(
"optical_bd")`. On a DVD it then calls nothing a fragment, and requires an
identical declared size before calling two equal-length titles the same
content -- Challenge of the Superfriends has two 21:43 episodes that differ by
233 KB and two 21:37 titles that are byte-identical, one of them a commentary
version. Runtime alone cannot tell those pairs apart.

**Which titles are the work and which are extras.** The longest is *usually*
the feature; the rest are extras and belong in an `extras` subfolder, not
loose beside it. On a disc of episodes "the longest" is the play-all and
means nothing, which is what `kind` is for.

**Whether several titles are cuts of one film or separate works.**
`selection.relationship()` already answers this from the clip lists -- cuts of
one film share a backbone of segments, episodes share nothing. Two cuts become
one folder with two version labels. Separate works become separate folders, or
a season of episodes.

**Film or series.** Nothing on the disc says, so the operator does, in `kind`.
Where it is empty the old reading still applies -- a single long title with
extras is a film; several similar-length titles with no shared backbone,
across discs of one collection, is a series -- but an empty `kind` means no
play-all is inferred from runtime, so a season disc will offer its play-all as
content and someone has to notice.

## What it can look up

**imdb.com cannot be read by a program.** Measured 2026-09-11: CloudFront
answers a non-browser user-agent with `403`, and a browser user-agent with a
`202` carrying a zero-byte body. Any instruction to "check imdb.com" is
unfollowable, and an agent told to do it will either stall or quietly invent
an answer.

So the lookup happens against a **local mirror of IMDb's published datasets**,
running as a container on the media server. It answers more than the web pages
did: the release year, the canonical title with its punctuation, the provider
id, the runtime, regional titles, and season and episode numbers.

Row counts, 2026-09-11: `title_basics` 12.4M, `title_akas` 58.1M,
`title_episode` 9.6M, `name_basics` 15.6M, `title_ratings` 1.7M.

### What the environment must provide

A machine that has not been set up will silently publish everything without
provider tags, which looks like a style choice rather than a missing
dependency. That is the failure worth designing against, so set these in
`~/.profile` -- login scope, because the GUI is launched from the desktop and
`~/.bashrc` only reaches interactive shells:

```sh
export MEDIA_BACKUP_IMDB_HOST=nas2
export MEDIA_BACKUP_IMDB_USER=imdb
export MEDIA_BACKUP_IMDB_PASSWORD=…
```

`MEDIA_BACKUP_IMDB_PORT` and `_DATABASE` default to 3306 and `imdb`.
`media_backup.config` reads all five, the environment wins over
`config.json`, and `Config.to_dict()` redacts the password.

**The credentials are deliberately not in the repository.** This particular
one guards a read-only mirror of public data on the LAN and is not a secret,
but a project that keeps one password in source keeps the next one there too.
`config.validate()` emits a warning -- not an error -- when a host is set with
no user, so the operator is told lookups are off rather than discovering it
from a library full of untagged films.

Client packages: `mariadb-client` for a person at a terminal, `python3-pymysql`
for code.

## What it still has to ask

These are not in any dataset and must not be guessed:

* **Which candidate**, when a lookup returns several. It often does. A disc
  label that has lost its punctuation needs a `LIKE`, and `'What%s Up%Doc%'`
  matches four films -- 1972, 1978, 1985 and 1988 -- three of them wrong.
  Bring the candidates with their years and runtimes; do not pick one.
* **What an edition is called.** The data supports "the longer cut". "Unrated
  Extended Version" is in the disc's menu graphics, not in any field.
* **Which film a box-set disc holds**, where the label names several or, as
  with `FEBRUARY_2015_MULTI_FEATURES`, names none of them.
* **Confirmation of an episode mapping** before it is written into filenames.
  The database supplies the numbers and the per-episode runtimes; matching
  them to *these* titles is inference and should be confirmed.

Ask **once per collection**, not once per file. A box set is one conversation.

### Check the runtime against the rip

`runtimeMinutes` against the file's measured duration is the check the web
pages never made convenient, and it is what catches the right title of the
wrong release -- a theatrical id on an extended cut, or a film mistaken for
its remake.

Agreement within a minute or two is confirmation. A ten-minute gap is a reason
to stop and ask, not to publish.

Worked, 2026-09-11: the `4 Movie Laugh Pack` (UPC 025192277740) arrives as
four DVD titles across two discs whose labels name nothing and whose cell
ranges are meaningless across titles. Nothing in the archive says which film
is which. Measured durations of 108.8, 93.6, 102.3 and 89.7 minutes against
`title_basics` gave Animal House, Weird Science, Dazed and Confused and Fast
Times -- every one within 0.7 minutes, and the four runtimes far enough apart
that no other assignment is possible. That disc is not identifiable any other
way.

## Rules

**Never write into the archive.** Read-only, without exception. If the archive
is wrong, the fix belongs upstream.

**Never invent an identifier.** An `[imdbid-…]` tag or an NFO `uniqueid` is
authoritative -- Jellyfin prefers local metadata over its own providers and
offers no way to disable that -- so a wrong id is worse than no id. If a lookup
is ambiguous, leave it out and let Jellyfin match on name and year, or ask.
**If the lookup database is unreachable, publish without the tag and say so.**
An unreachable database is a reason to omit an id, never a reason to recall
one.

**Publish only `done` discs.** A disc that reads `Needs you` is waiting on a
person to rip it by hand; publishing what it did produce would file a fragment
as if it were the film.

**Refuse to publish an incomplete set.** If `expected_disc_count` is set and
fewer discs are `done`, stop and say so. `Collection.finish_warnings()` already
computes this; the operator may have finished the collection anyway, and that
is their call to make knowingly rather than one to be made silently here.

**Be idempotent.** Running twice must not produce `Hancock (2008) (1)`. Record
what was published and where, and reconcile against it on the next run.

**Sanitise names.** "Spider-Man: Across The Spider-Verse" contains a colon,
which is illegal on NTFS and exFAT. Jellyfin's own examples use " - ".

## A worked example

The Hancock disc, as the archive holds it:

```
finished/<collection>/discs/<disc>/data/
├── Hancock-A1_t00.mkv    title 1, 1:42:14, segments 123,141,125,142,…
└── Hancock-A2_t01.mkv    title 0, 1:32:13, segments 123,124,125,126,…
```

Two titles; `relationship()` says `cut_variants` -- they share ten clips and
each has nine of its own. So this is one film in two cuts, not two films. The
year and the edition names are asked for. What gets published:

```
Movies/
└── Hancock (2008)/
    ├── Hancock (2008) - Theatrical Cut.mkv          ← the shorter
    ├── Hancock (2008) - Unrated Extended Cut.mkv    ← the longer
    └── movie.nfo
```

with `movie.nfo` carrying the title, year and any verified id, which keeps the
provider tag out of both filenames.

## Copy, hardlink or symlink

Both, in sequence -- decided 2026-09-07, once the library's location was known.

**Hardlink into a staging area on the archive's own filesystem.** No second
copy of 20 GB, both paths real, and removing a staged link leaves the archive
whole. A hardlink cannot cross a filesystem, so the staging area belongs under
`media_path`, not on the root filesystem: `/srv/jellyfin/ready_to_add` -- the
path first written down for this -- is on `/`, while the archive is an NVMe
mount, and every `ln` would have failed.

**Then rsync the staging area to the server.** The library is on another
machine, so no link can reach it. rsync over SSH rather than a mount, because
a failed transfer is then an exit code to retry rather than a job wedged in
uninterruptible sleep.

Deleting from the Jellyfin library must never delete from the archive. Across
a network that follows for free: the two are unrelated filesystems.

## Reclaiming the archive

The archive is not kept forever. Once the copy on the server is proven
**byte-for-byte by checksum** -- not by size, and not by rsync's exit code,
which on exFAT has no stored metadata to check against -- the media in
`data/` is deleted and the space returned.

What survives is `collection.json`, the attempt logs, and a `.published`
marker recording where each file went and the hash that matched. About a
megabyte, and it is the entire record of what the disc held, what was chosen
and why, and what makemkvcon said while copying it. Reclaiming space is not a
reason to discard provenance.

The bar is deliberately high because this deletes the last local copy. One
mismatched hash, one skipped disc, one disc in a state other than `done`, and
the whole collection stays. The fallback for a lost file is re-ripping the
physical disc, which is why the disc -- not the archive -- is the master.
