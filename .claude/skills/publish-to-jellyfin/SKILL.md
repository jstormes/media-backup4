---
name: publish-to-jellyfin
description: Stage finished disc backups into a ready_to_add area named the way Jellyfin needs, then rsync them to the media server. Use after discs finish backing up, when asked to publish or rename backups for Jellyfin, or to name a film's cuts, extras or episodes for a media server.
---

# Publish finished backups to Jellyfin

The ripper leaves an archive organised for recovery: UUID directories and
MakeMKV's filenames. This turns a finished collection into the tree Jellyfin
expects, staged locally and then pushed to the media server.

**The archive is the source and stays untouched.** Every write lands in the
staging area, and the media is **hardlinked** into it: no second copy of 20 GB,
both paths real, and deleting from the staging area leaves the archive whole.

**A hardlink cannot cross a filesystem, so check before staging.** The archive
lives on whatever `media_path` is mounted from, which is usually not the root
filesystem. On this machine `/srv/media-backup` is an NVMe mount and `/srv` is
root, so `/srv/jellyfin/ready_to_add` -- the path an earlier version of this
skill named -- would have failed on the first `ln`.

```bash
test "$(stat -c %d "$ARCHIVE")" = "$(stat -c %d "$STAGE")" || echo "different filesystems"
```

Stage under `media_path` (`/srv/media-backup/ready_to_add`) unless the
operator says otherwise. `config.validate` only cares about `collections/`,
`finished/` and `cancelled/`, so a sibling directory there is harmless.

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
/srv/media-backup/ready_to_add/
├── Movies/
│   └── Hancock (2008) [imdbid-tt0448157]/
│       ├── Hancock (2008) [imdbid-tt0448157] - Theatrical Cut.mkv
│       └── Hancock (2008) [imdbid-tt0448157] - Unrated Extended Cut.mkv
└── Shows/
    └── Series Name (2019) [imdbid-tt1234567]/
        └── Season 01/
            └── S01E01.mkv
```

Episode files are bare `S01E01.mkv` -- that is what the library's 1,665 of
them look like. Extras all go in a flat `extras/`, not the semantic folders:
nothing on a disc says whether a four-minute title is a deleted scene or a
featurette.

`Movies/` and `Shows/` because Jellyfin keeps them as separate libraries, so
the staging area says which is which and the operator's move is a merge.

Every file name begins **exactly** with its folder name before any version
label, and the separator is space-hyphen-space. Extras go in `extras/`.

Keep names legal. **The library is on exFAT**, which rejects
`" * / : < > ? \ |` outright -- this is not a portability nicety, a name
containing one cannot be written at all. Replace `:` with ` -` and drop a
trailing `?`: `WHAT'S UP DOC?` becomes `What's Up, Doc`. Apostrophes,
brackets, parentheses and commas are all fine. exFAT is also case-insensitive,
so two titles differing only in case collide.

Link, do not copy:

```bash
ln "$archive/data/Hancock_t01.mkv" \
   "$STAGE/Movies/Hancock (2008) [imdbid-tt0448157]/Hancock (2008) [imdbid-tt0448157] - Unrated Extended Cut.mkv"
```

Re-running must not produce `Hancock (2008) (1)`. When a target exists, compare
inode numbers: same inode means already staged, so report it and move on.

## 6. Put the provider id in the name, not in an NFO

The library uses `[imdbid-tt…]` in the folder name and repeated in the file
name -- 228 of its 235 films, and **not one NFO file**. Match it:

```
Movies/Match Point (2005) [imdbid-tt0416320]/
└── Match Point (2005) [imdbid-tt0416320].mkv
```

Earlier versions of this skill said to write `movie.nfo`. Do not: it would put
a second metadata mechanism into a library that consistently uses one. See
`docs/jellyfin/library-layout.md`, which measured this on 2026-09-07.

**Look the id up, never recall it.** A malformed or wrong tag is silently
ignored or silently authoritative -- neither looks like an error, both look
like Jellyfin matching badly. Confirm against imdb.com before writing, and if
the lookup is at all ambiguous leave the tag off entirely and let name and
year match. The library already carries three tags that fail this way:
`[indbid-…]`, `[imbdid-…]`, and one missing the `imdbid-` prefix.

## 7. Push it to the media server

Staging is local; the library is on another machine. `rsync` over SSH, not a
mount: a failed transfer is then an exit code to retry rather than a hung job,
and nothing on the ripper depends on the server being up.

**The library filesystem is exFAT.** That drives every flag here:

```bash
rsync -rltDvh --no-perms --no-owner --no-group --modify-window=1 \
      --partial --append-verify \
      "$STAGE/Movies/" nas2:/srv/dev-disk-by-uuid-78AA-077A/Movies/
```

- `--no-perms --no-owner --no-group`, and `-rltD` rather than `-a`. exFAT
  stores no permissions or ownership: the mount fabricates them from
  `fmask=0000,dmask=0000`, which is why every file in the library reads
  `rwxrwxrwx root:root`. Trying to preserve or set modes is pointless, and
  `--chmod` would be too.
- `--modify-window=1` because FAT-family timestamps are coarse. Without it a
  re-run can decide every file changed and send 10 GB again.
- `--partial --append-verify` to resume a part-sent 20 GB title instead of
  restarting it.

Run it with `--dry-run` first and read the file list.

**Check the names before sending.** exFAT rejects `" * / : < > ? \ |`
outright, so replacing `:` with ` -` is a hard requirement rather than a
tidiness rule -- "Spider-Man: Across The Spider-Verse" cannot be written at
all otherwise. exFAT is also case-insensitive, so two films differing only in
case would collide.

```bash
find "$STAGE" -mindepth 1 -printf '%P\n' | grep -E '["*:<>?\\|]'
```

There is no 4 GB limit: this is exFAT, not FAT32. A 26 GB title already sits
in the library.

After the transfer, tell the operator to rescan the Jellyfin library -- new
files are not noticed until it does.

## 8. Verify, then report

Check every one of these before reporting success:

- Every title with an `output_file` has a staged file.
- Every staged file resolves back to one such title.
- Every staged media file has a link count above 1 (`stat -c '%h %n'`) —
  proving a hardlink rather than a copy.
- Every file name begins with its parent folder's name.
- The archive is unchanged. Fingerprint it before staging and compare after:
  `find /srv/media-backup/finished -type f -printf '%i %s %p\n' | sort`.
- After a transfer, the remote size matches: `ssh nas2 find … -printf '%s %P\n'`
  against the same for the staging area. Do not trust rsync's exit code alone
  on exFAT, where it cannot set the metadata it would normally verify.

Report per collection: what was staged and under what name, what was asked and
answered, any surplus files left in the archive, and anything skipped with the
reason.

## Worked example

The two collections finished on 2026-09-07:

| Archive | Titles with `output_file` | Staged as |
|---|---|---|
| `Hancock`, UPC 043396279001 | `Hancock_t00.mkv` 1:32:13, `Hancock_t01.mkv` 1:42:14 — shared clip backbone, so two cuts | `Movies/Hancock (2008) [imdbid-tt0448157]/Hancock (2008) [imdbid-tt0448157] - Theatrical Cut.mkv` and `… - Unrated Extended Cut.mkv` |
| `WHAT'S UP DOC?`, UPC 883929152186 | `WHAT'S UP DOC-A1_t00.mkv` 1:33:30 | `Movies/What's Up, Doc (1972) [imdbid-tt0069495]/What's Up, Doc (1972) [imdbid-tt0069495].mkv` — the `?` is dropped, exFAT will not take it |

Hancock's `data/` also holds `Hancock_t02.mkv` and `_t03.mkv`, duplicates no
title claims. They stay in the archive and go in the report.

And the two from 2026-09-07 that this skill was first run against:

| Archive | Title with `output_file` | Staged as |
|---|---|---|
| `Match Point`, UPC 678149486629 | `B1_t00.mkv` 2:04:12 | `Movies/Match Point (2005) [imdbid-tt0416320]/…` |
| `DVD_VIDEO`, UPC 043396100589 | `Fresh Horses-A1_t00.mkv` 1:42:39 | `Movies/Fresh Horses (1988) [imdbid-tt0095178]/…` |

Note the second: the volume label is the generic `DVD_VIDEO` and the film is
`Fresh Horses`. Always take the name from `makemkv_disc_name`, never from the
label. Both discs' short titles carried no `output_file` -- selection never
saved them -- so there were no extras to stage, which is not the same as
extras having been missed.
