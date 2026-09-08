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
| `discs[]` | One entry per physical disc, in `ordinal` order. |
| `discs[].makemkv_disc_name` | MakeMKV's name for the disc -- "Fresh Horses" where the volume label says `DVD_VIDEO`. The best name available. |
| `discs[].state` | Only `done` was backed up. `failed`, `abandoned` and anything else must not be published. |
| `discs[].titles[]` | The titles the scan found, feature and extras alike. |
| `titles[].output_file` | **The file on disk**, reconciled after the run. This is the link from metadata to bytes; do not reconstruct it from the title index. |
| `titles[].duration`, `size_bytes`, `chapters` | For telling a feature from an extra, and the longer cut from the shorter. |
| `titles[].segments` | The clip list. Titles sharing a backbone are cuts of one work; see `makemkv/selection.py`. |
| `discs[].attempts[]` | Evidence. `error_kind` says why something is not `done`. |

Media files are at
`finished/<collection>/discs/<disc>/data/<output_file>`.

## What it has to decide

**Which titles are the work and which are extras.** The longest is the
feature; the rest are extras and belong in a `behind the scenes` or `extras`
subfolder, not loose beside it.

**Whether several titles are cuts of one film or separate works.**
`selection.relationship()` already answers this from the clip lists -- cuts of
one film share a backbone of segments, episodes share nothing. Two cuts become
one folder with two version labels. Separate works become separate folders, or
a season of episodes.

**Film or series.** Nothing on the disc says. A single long title with extras
is a film; several similar-length titles with no shared backbone, across discs
of one collection, is a series.

## What it has to ask

These are not derivable and must not be guessed:

* **The release year.** Jellyfin wants it and no MakeMKV attribute carries it.
* **The canonical title**, where the disc's differs from the released one.
* **Season and episode numbers** for a series. Playlist order is usually
  broadcast order and is not reliably so.
* **What an edition is called.** The data supports "the longer cut". "Unrated
  Extended Version" is in the disc's menu graphics, not in any field.

Ask **once per collection**, not once per file. A box set is one conversation.

A provider id may be looked up rather than asked, but see below.

## Rules

**Never write into the archive.** Read-only, without exception. If the archive
is wrong, the fix belongs upstream.

**Never invent an identifier.** An `[imdbid-…]` tag or an NFO `uniqueid` is
authoritative -- Jellyfin prefers local metadata over its own providers and
offers no way to disable that -- so a wrong id is worse than no id. If a lookup
is ambiguous, leave it out and let Jellyfin match on name and year, or ask.

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
