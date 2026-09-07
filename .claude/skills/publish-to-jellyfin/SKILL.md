---
name: publish-to-jellyfin
description: Stage finished disc backups into /srv/jellyfin/ready_to_add named the way Jellyfin needs. Use after discs finish backing up, when asked to publish or rename backups for Jellyfin, or to name a film's cuts, extras or episodes for a media server.
---

# Publish finished backups to Jellyfin

The ripper leaves an archive organised for recovery: UUID directories and
MakeMKV's filenames. This turns a finished collection into the tree Jellyfin
expects, staged under `/srv/jellyfin/ready_to_add` for the operator to move
into the library.

**The archive is the source and stays untouched.** Every write lands under
`/srv/jellyfin/ready_to_add`. Both are on one filesystem, so the media is
**hardlinked**: no second copy of 20 GB, both paths real, and deleting from
the staging area leaves the archive whole.

Naming rules are in `docs/jellyfin/library-layout.md`; the contract this
implements is `docs/jellyfin/publishing.md`. Read the layout doc before naming
anything — the rules are exact and unforgiving.

## 1. Pick the collections

Read `collection.json` from each directory under
`/srv/media-backup/finished/`. A directory there is complete by construction:
finishing is an atomic rename.

Publish a disc when `state` is `done`. Report and skip anything else — a disc
reading `Needs you` is waiting on a person to rip it by hand, and staging what
it did produce would file a fragment as if it were the film.

When `expected_disc_count` exceeds the number of `done` discs, say so and stop.
The operator may choose to publish an incomplete set, but knowingly.

## 2. Take the inventory from the JSON, not the directory

Drive from `discs[].titles[]` where `output_file` is set. That field was
reconciled against the files after the run and is the link from metadata to
bytes.

The `data/` directory can hold **more** files than that. MakeMKV saves by
minimum length, which cannot exclude a second playlist of the same runtime, so
a disc offering its feature twice writes both. Leave the unclaimed files in the
archive and name them in the report.

Backups made before 2026-09-07 can hold surplus copies: compare the claimed
file against any unclaimed one of the same runtime with
`ffprobe -v error -show_entries stream=codec_type -of csv=p=0`. They are the
same footage with different track sets, and the recorded one is not always the
better equipped — Hancock's pair carry fifteen and seven subtitle tracks. Stage
the richer one and say in the report that you did. Later backups save one file
per chosen title and pick the richer copy themselves.

## 3. Work out what the disc holds

Longest title is the feature. Shorter ones are extras.

Two or more feature-length titles are either **cuts of one film** or
**separate works**, and the clip lists say which: cuts share a backbone of
segments, separate works share nothing. `media_backup.makemkv.selection` has
`relationship()` and `shared_ratio()` — use them rather than eyeballing
durations.

- Cuts of one film → one folder, one file per cut, version labels.
- Separate works, several discs, similar runtimes → a series.

## 4. Ask for what the disc cannot say

Ask **once per collection**, in one message, and only for what is missing:

- **Release year.** Jellyfin wants it; no MakeMKV attribute carries it.
- **Canonical title**, where the disc's differs. Disc names are often
  upper-case volume labels: `WHAT'S UP DOC?` is released as `What's Up, Doc?`.
- **What each cut is called** — "Theatrical Cut", "Unrated Extended Cut". The
  data supports "the longer one" and no more.
- **Season and episode numbers**, for a series.

A provider id may be looked up instead of asked. Use it only when the lookup
is unambiguous: an `[imdbid-…]` tag or an NFO `uniqueid` overrides Jellyfin's
own providers and cannot be turned off, so a wrong id is worse than none.
Where the lookup is ambiguous, leave it out and let name and year match.

## 5. Build the tree

```
/srv/jellyfin/ready_to_add/
├── Movies/
│   └── Hancock (2008)/
│       ├── Hancock (2008) - Theatrical Cut.mkv
│       ├── Hancock (2008) - Unrated Extended Cut.mkv
│       └── movie.nfo
└── Shows/
    └── Series Name (2019)/
        └── Season 01/
            └── Series Name S01E01.mkv
```

`Movies/` and `Shows/` because Jellyfin keeps them as separate libraries, so
the staging area says which is which and the operator's move is a merge.

Every file name begins **exactly** with its folder name before any version
label, and the separator is space-hyphen-space. Extras go in a `behind the
scenes`, `deleted scenes`, `featurettes` or `extras` subfolder; the full list
is in the layout doc.

Keep names legal: replace `:` with ` -`. Leave the rest of the punctuation
alone unless the operator says the library will live on NTFS or exFAT, where
`? * " < > |` also need replacing.

Link, do not copy:

```bash
ln "$archive/data/Hancock_t01.mkv" \
   "/srv/jellyfin/ready_to_add/Movies/Hancock (2008)/Hancock (2008) - Unrated Extended Cut.mkv"
```

Re-running must not produce `Hancock (2008) (1)`. When a target exists, compare
inode numbers: same inode means already staged, so report it and move on.

## 6. Write the NFO

One `movie.nfo` beside the film, or `tvshow.nfo` beside a series. It keeps
provider tags out of every filename, which matters because version labels and
provider tags compete for the same name.

```xml
<?xml version="1.0" encoding="utf-8"?>
<movie>
  <title>Hancock</title>
  <year>2008</year>
  <uniqueid type="imdb" default="true">tt0448157</uniqueid>
</movie>
```

Include `uniqueid` only for an id actually looked up and confirmed.

## 7. Verify, then report

Check every one of these before reporting success:

- Every title with an `output_file` has a staged file.
- Every staged file resolves back to one such title.
- Every staged media file has a link count above 1 (`stat -c '%h %n'`) —
  proving a hardlink rather than a copy.
- Every file name begins with its parent folder's name.
- The archive is unchanged: `find /srv/media-backup/finished -newer …` finds
  nothing, and no file there has been renamed or removed.

Report per collection: what was staged and under what name, what was asked and
answered, any surplus files left in the archive, and anything skipped with the
reason.

## Worked example

The two collections finished on 2026-09-07:

| Archive | Titles with `output_file` | Staged as |
|---|---|---|
| `Hancock`, UPC 043396279001 | `Hancock_t00.mkv` 1:32:13, `Hancock_t01.mkv` 1:42:14 — shared clip backbone, so two cuts | `Movies/Hancock (2008)/Hancock (2008) - Theatrical Cut.mkv` and `… - Unrated Extended Cut.mkv` |
| `WHAT'S UP DOC?`, UPC 883929152186 | `WHAT'S UP DOC-A1_t00.mkv` 1:33:30 | `Movies/What's Up, Doc? (1972)/What's Up, Doc? (1972).mkv` |

Hancock's `data/` also holds `Hancock_t02.mkv` and `_t03.mkv`, duplicates no
title claims. They stay in the archive and go in the report.
