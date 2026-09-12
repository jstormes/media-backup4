---
name: publish-to-jellyfin
description: Stage finished disc backups named the way Jellyfin needs, rsync them to the media server, and reclaim the local space once the copy is checksum-verified. Use after discs finish backing up, when asked to publish or rename backups for Jellyfin, or to name a film's cuts, extras or episodes for a media server.
---

# Publish finished backups to Jellyfin

The ripper leaves an archive organised for recovery: UUID directories and
MakeMKV's filenames. This turns a finished collection into the tree Jellyfin
expects, staged locally and then pushed to the media server.

**The archive is the source and stays untouched while publishing.** Every
write lands in the staging area, and the media is **hardlinked** into it: no
second copy of 20 GB, both paths real, and deleting from the staging area
leaves the archive whole.

It is reclaimed only at the very end, and only once the copy on the server has
been proven byte-for-byte -- see step 9. Until that proof exists the archive is
the only copy that has not crossed a network.

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

**Skip any collection holding a `.published` marker.** Step 9 writes one after
reclaiming the media, so the directory still carries `collection.json` and the
logs but no longer has the files. Without this check a second run reads the
metadata, finds no media, and reports a fault that is not one.

When `expected_disc_count` exceeds the number of `done` discs, say so and stop.
The operator may choose to publish an incomplete set, but knowingly.

## 2. Take the inventory from the JSON, not the directory

Drive from `discs[].titles[]` where `output_file` is set. That field was
reconciled against the files after the run and is the link from metadata to
bytes.

**Expect many titles per disc, and expect most of them to be junk.** Since
2026-09-08 the ripper copies *everything* MakeMKV reports and makes no
judgement about what any title is -- it stopped guessing because guessing lost
two films, and sorting the pile is now this skill's job. A film disc that used
to arrive as one file now arrives as anywhere from two to thirty-nine, and
they are a mix of:

- the feature, or **two features** on a double bill, or a season's episodes;
- alternate cuts of the same film;
- genuine extras -- featurettes, deleted scenes, trailers;
- **the same content authored twice.** A Blu-ray routinely carries a playlist
  twice with different track sets. Hancock's 39 titles are two films, each
  authored twice, plus extras.
- **slices of the feature.** A Blu-ray offers individual clips of the film as
  titles in their own right. Hancock lists clip 123 alone at 5:50, clip 125 at
  11:01, and fifteen more. These are not extras and must never be published;
  they are the film, cut up.

Publish the feature or features, publish the extras worth keeping, and leave
the rest in the archive. Name everything you did not publish in the report so
the operator can see what was decided rather than discovering it later.

### Telling the pile apart

The clip lists do most of the work, and `media_backup.makemkv.selection` has
the helpers. **On a Blu-ray the segments map names global clip files and can
be compared across titles. On a DVD it is a cell range local to its own title
and comparing it across titles is meaningless** -- every DVD title's range
starts at 1, so two unrelated films both report `1-12`. Never conclude
anything from two DVD titles sharing a range.

For a Blu-ray, in order:

1. **Duplicates.** Same clip list *and* same duration is the same content
   authored twice. Keep the one with the higher `streams` -- the copies are
   not interchangeable, Hancock's pair carry fifteen subtitle tracks against
   seven -- and say in the report that you did.
2. **Fragments.** A title whose clip set is a strict subset of a longer kept
   title's is a slice of it. Drop it. This is what takes 28 GB off Hancock.
3. **Cuts or separate works.** `relationship()` on what remains. It requires
   each cut to carry clips the other lacks, which is what seamless branching
   is, so a subset relationship reads as separate works rather than cuts.

For a DVD none of that is available, so use duration, chapter count and the
disc label, and ask. The label is often the giveaway:
`FIREHEAD_AND_LAST_LIVES` names both films on the disc, and the two
feature-length titles are them.

**Two feature-length titles on a DVD are usually two films, not two cuts.**
That is the case the old ripper got wrong twice.

Backups made before 2026-09-07 can hold surplus copies: compare the claimed
file against any unclaimed one of the same runtime with
`ffprobe -v error -show_entries stream=codec_type -of csv=p=0`. They are the
same footage with different track sets, and the recorded one is not always the
better equipped. Stage the richer one and say in the report that you did.

## 3. Work out what the disc holds

After step 2 has thrown out the duplicates and the fragments, what remains is
content. The longest title is *usually* the feature, and that is a starting
point rather than a rule — a disc can hold two films, a season of episodes,
or a dozen twenty-minute shorts, and on those discs "the longest" means
nothing.

Read the shape of what is left:

- **One long title, the rest much shorter** → a film and its extras. The
  ordinary case.
- **Two long titles** → two films, or two cuts of one. `relationship()`
  decides it on a Blu-ray; on a DVD the clip lists cannot decide it, so use
  the disc label and ask. Cuts of one film → one folder, one file per cut,
  version labels. Two films → two folders.
- **Many titles of similar length, none dominant** → episodes, or a kids'
  disc of shorts. This is a series, and you need season and episode numbers
  from the operator. Runtime clusters around 22, 45 or 60 minutes are the
  giveaway; so is a disc label naming a show rather than a film.

`media_backup.makemkv.selection` has `relationship()` and `shared_ratio()` —
use them rather than eyeballing durations, and remember they are only
meaningful on a Blu-ray.

**Episode order is not in MakeMKV's data.** Its title order usually follows
disc order, which usually follows broadcast order, and "usually" is not good
enough to number episodes by. The published episode list you need is on nas2 --
`title_episode`, 9.6M rows, with per-episode runtimes to align against. See
step 6. Confirm the mapping with the operator before writing the filenames.

## 4. Look up what you can; ask for the rest

**Try the IMDb database on nas2 first** -- step 6 has the queries. It answers
the release year, the canonical title with its punctuation, the provider id,
the runtime to check the rip against, and season and episode numbers. Asking
for something the database already knows wastes the operator's attention and
invites a typo into a filename.

What it cannot answer, and you must still ask **once per collection, in one
message**:

- **Which candidate**, when the lookup returns several. It usually does: a
  `LIKE` on a disc label that has lost its punctuation returned four films for
  `WHAT'S UP DOC?`. Bring the candidates with their years and runtimes rather
  than guessing between them.
- **What each cut is called** — "Theatrical Cut", "Unrated Extended Cut". Not
  in IMDb's datasets, and the disc data supports "the longer one" and no more.
- **Which film a box set disc holds**, where the label names several.
- **Confirmation of an episode mapping** before it is written into filenames.
  The database supplies the numbers; matching them to *these* titles is done
  by runtime and should be confirmed, not assumed.

A provider id may be looked up rather than asked, and now should be. Use it
only when the lookup is unambiguous: an `[imdbid-…]` tag or an NFO `uniqueid`
overrides Jellyfin's own providers and cannot be turned off, so a wrong id is
worse than none. Where it is ambiguous, leave it out and let name and year
match.

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
like Jellyfin matching badly. If the lookup is at all ambiguous leave the tag
off entirely and let name and year match. The library already carries three
tags that fail this way: `[indbid-…]`, `[imbdid-…]`, and one missing the
`imdbid-` prefix.

### Look it up in the database on nas2, not on imdb.com

**imdb.com cannot be read by a program.** Measured 2026-09-11: CloudFront
answers a non-browser user-agent with `403`, and a browser user-agent with a
`202` carrying a zero-byte body. There is no useful response either way, so
any instruction to "check imdb.com" is unfollowable.

nas2 runs a MariaDB mirror of IMDb's published datasets instead -- and it is
better than the web pages were, because it carries runtimes and episode
numbers the pages never exposed cleanly.

```bash
mariadb -h "$MEDIA_BACKUP_IMDB_HOST" -u "$MEDIA_BACKUP_IMDB_USER" \
        -p"$MEDIA_BACKUP_IMDB_PASSWORD" imdb -t -e "..."
```

Credentials come from the environment (`~/.profile`), never from
`config.json` or this file; `media_backup.config` exposes them as
`cfg.imdb_host` / `_user` / `_password` and redacts the password from
`to_dict()`. `python3-pymysql` is installed for code that needs it.

Row counts as of 2026-09-11: `title_basics` 12.4M, `title_akas` 58.1M,
`title_episode` 9.6M, `name_basics` 15.6M, `title_ratings` 1.7M.

**The ordinary lookup.** Constrain `titleType` -- `movie` and `tvMovie` are
different things and a disc is usually one of them:

```sql
SELECT tconst, titleType, primaryTitle, startYear, runtimeMinutes
FROM title_basics
WHERE primaryTitle = 'Annie''s Point' AND titleType IN ('movie','tvMovie')
ORDER BY startYear;
```

**It narrows; it does not decide.** The disc label drops punctuation, so a
`LIKE` is often needed, and a `LIKE` returns a field. Verified 2026-09-11:

```sql
WHERE primaryTitle LIKE 'What%s Up%Doc%' AND titleType = 'movie'
```

returns **four** films -- `What's Up, Doc?` (1972), `What's Up Superdoc!`
(1978), `What's Up Doc?` (1985) and `What's Up Doc` (1988). Three are the
wrong film. Use the year and the runtime to choose, and where they do not
settle it, ask. A confident wrong `tconst` is worse than no tag.

**Check the runtime against the rip.** This is the cross-check the web never
made easy, and it catches the right title of the wrong release:

```bash
ffprobe -v error -show_entries format=duration -of csv=p=0 "$file"
```

Annie's Point: `runtimeMinutes` 87 against a measured 1:27:09. Agreement to a
minute or two is confirmation; a ten-minute gap means a different cut or a
different release, and is a reason to stop and ask rather than to publish.

**Regional titles** are in `title_akas`, which is what settles a disc whose
label is a local variant:

```sql
SELECT a.title, a.region, a.types FROM title_akas a
WHERE a.titleId = 'tt0069495' AND a.region IN ('US','GB');
```

**Episode numbers are in the database.** Step 3 says episode order is not in
the data and must be asked for. That was true of MakeMKV's output and is not
true here:

```sql
SELECT b.tconst, e.seasonNumber, e.episodeNumber, b.primaryTitle, b.runtimeMinutes
FROM title_episode e JOIN title_basics b ON b.tconst = e.tconst
WHERE e.parentTconst = 'tt0098769' AND e.seasonNumber = 1
ORDER BY e.episodeNumber;
```

Match the disc's titles to that list **by runtime**, and confirm the mapping
with the operator before writing `S01E01.mkv` names. MakeMKV's title order
still proves nothing, so the database supplies the numbering and the runtimes
supply the alignment -- neither alone is enough.

**If the database is unreachable**, say so and publish without the tag rather
than guessing one. Name and year still match in Jellyfin; a wrong id does not.

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

- Every staged file resolves back to exactly one title with an `output_file`.
  Not the converse: most titles are *not* staged now, because the ripper
  copies everything and step 2 discards the duplicates, the fragments and the
  junk. Checking that every title has a file would fail on every disc.
- Every title you did not stage is accounted for in the report, by name and
  reason. This is the check that replaces the one above, and it is the one
  that catches a film left behind.
- Every staged media file has a link count above 1 (`stat -c '%h %n'`) —
  proving a hardlink rather than a copy.
- Every file name begins with its parent folder's name.
- The archive is unchanged. Fingerprint it before staging and compare after:
  `find /srv/media-backup/finished -type f -printf '%i %s %p\n' | sort`.
- After a transfer, the remote size matches: `ssh nas2 find … -printf '%s %P\n'`
  against the same for the staging area. Do not trust rsync's exit code alone
  on exFAT, where it cannot set the metadata it would normally verify. Compare
  `%s` from `find`, never `du`, which rounds up to the allocation unit and
  shows a couple of MB of phantom difference on every file. Any `ssh` used
  inside a loop needs `-n` -- but never together with a heredoc, which `-n`
  silently empties; see step 9.

Report per collection: what was staged and under what name, what was asked and
answered, and **every title left in the archive with the reason** — duplicate
of another title, fragment of the feature, extra not worth publishing, junk.
A reader should be able to tell from the report alone that nothing was lost,
without opening the collection.

## 9. Prove the copy, then reclaim the space

Only after step 8's checks pass. This deletes the last local copy of the rip,
so the bar is a checksum and nothing less.

**Size and a zero exit code are not enough.** rsync verifies its own stream,
but exFAT stores no permissions, ownership or fine-grained times, so the
metadata it would normally compare against on a later run does not exist.
Hash both ends and compare.

**Three ways this check has quietly gone wrong**, all of them on 2026-09-08,
all of them producing an answer that looked fine. Copy the block below rather
than writing the loop from memory:

```bash
STAGE=/srv/media-backup/ready_to_add/Movies
REMOTE=/srv/dev-disk-by-uuid-78AA-077A/Movies
files=(
  "Demon Seed (1977) [imdbid-tt0075931]/Demon Seed (1977) [imdbid-tt0075931].mkv"
  "Demon Seed (1977) [imdbid-tt0075931]/extras/Additional Scene.mkv"
)

verified=0
for f in "${files[@]}"; do
    local_hash=$(sha256sum "$STAGE/$f" | cut -d' ' -f1)
    # ssh -n, and printf %q -- see the three notes below.
    remote_hash=$(ssh -n nas2 "sha256sum $(printf '%q' "$REMOTE/$f")" | cut -d' ' -f1)
    if [ -z "$remote_hash" ]; then
        echo "NO HASH from the server for $f -- not a mismatch, a broken command"
        exit 1
    fi
    [ "$local_hash" = "$remote_hash" ] || { echo "MISMATCH: $f"; exit 1; }
    verified=$((verified + 1))
done

[ "$verified" -eq "${#files[@]}" ] \
    || { echo "only $verified of ${#files[@]} checked"; exit 1; }
```

1. **`ssh` reads stdin, so it eats a loop's input.** `while read f; do ... ssh
   ... done <<EOF` runs **once** and then stops, because the first `ssh`
   swallowed the remaining lines. Measured: three input lines, one iteration.
   Worse, the loop **exits 0**, so it reads as a clean pass over every file.
   That is how two of three files came to be reclaimed on one unverified
   hash. `ssh -n` attaches stdin to `/dev/null` and fixes it.

2. **Count what you checked.** A loop that ends early is indistinguishable
   from one that passed unless the count is asserted, which is the whole
   lesson of point 1. The `$verified` tally is not decoration.

3. **Never wrap a remote path in single quotes.** This skill used to advise
   `ssh nas2 sha256sum "'$dest'"`, and it is wrong: a path containing an
   apostrophe closes the quote and the remote shell dies with
   ``syntax error near unexpected token `(` ``. "L'iniziazione" is a real film
   in this library and it did exactly that -- the empty result then compared
   unequal and printed **MISMATCH on a copy that was byte-perfect**. Use
   `printf '%q'`, which escapes the path for the remote shell; verified
   against that exact filename. Where a whole block must run remotely, send it
   as a heredoc to `ssh nas2 'bash -s'` and quote paths with double quotes on
   the far side -- an apostrophe inside double quotes is literal.

4. **`-n` and a heredoc are mutually exclusive.** They are the two fixes above
   and they cancel each other: `-n` *is* "attach stdin to `/dev/null`", so
   `ssh -n nas2 'bash -s' <<EOF` hands the remote shell an empty script. It
   runs nothing, prints nothing, and exits 0. Against a `find` that listed the
   published files, that reads as **the files are not on the server** --
   alarming, and false. Measured 2026-09-10, having made the mistake while
   verifying a transfer that was in fact perfect.

   The rule: `-n` when the command is an *argument* (`ssh -n host "sha256sum
   ..."`), which is the loop case. No `-n` when the script arrives on *stdin*
   (`ssh host 'bash -s' <<EOF`), because that is where the script is. A
   heredoc block is not in a loop, so it never needed `-n` anyway.

5. **A process pattern matches the process doing the matching.** `pgrep -f`
   and `pkill -f` search the whole command line, and your own shell's command
   line contains the pattern you just typed. So this waits forever:

   ```bash
   # WRONG -- the until-loop's own cmdline contains "ready_to_add5"
   until ! pgrep -f "ready_to_add5" >/dev/null; do sleep 20; done
   ```

   Measured 2026-09-10: the gated job sat idle for fifteen minutes after the
   job it was waiting for had finished, because it was waiting on itself. The
   same trap kills the caller outright with `pkill -f`, which took out two of
   this session's own shells (exit 144) before it reached its target.

   **Do not wait on a name. Wait on a PID**, which cannot be ambiguous:

   ```bash
   until ! kill -0 "$PID" 2>/dev/null; do sleep 20; done
   ```

   If a pattern is unavoidable, exclude self and parent -- `pgrep -f PAT |
   grep -qv -e "^$$\$" -e "^$PPID\$"` -- and for `pkill`, get the pid list
   first, drop `$$` and `$PPID`, then kill by number. Better still, run the
   two jobs in one script, one after the other: no gate, nothing to match.

This re-reads every byte on both machines. A 20 GB title takes minutes, most
of it on the NAS. That is the price of deleting the only other copy, and it is
worth paying.

**An empty hash is not a mismatch.** Tell them apart in the message. A real
mismatch means the bytes differ and the disc must not be reclaimed; an empty
result means the command never ran, which is a bug in the command. Reporting
the second as the first sends the operator hunting a corruption that is not
there.

**Reclaim only a collection that fully verified.** Every published file
hashed and matched, nothing skipped, no disc left in a state other than `done`,
and `expected_disc_count` satisfied. One mismatch anywhere and the whole
collection stays.

Then, in this order:

1. Remove the staged links under `ready_to_add`. On their own these free
   nothing -- they are hardlinks, and the bytes belong to the archive -- so
   this is tidying, not reclamation.
2. Remove the media: the contents of each disc's `data/` directory.
3. **Keep `collection.json` and `logs/`.** They are about a megabyte and they
   are the entire record that this disc was ripped, what titles it held, what
   was chosen and why, and what makemkvcon said while doing it. Reclaiming the
   space does not mean discarding the provenance.
4. Write a `.published` marker beside `collection.json` recording where each
   file went and the hash that was matched:

```json
{"published_at": "2026-09-07T20:49:00Z",
 "destination": "nas2:/srv/dev-disk-by-uuid-78AA-077A/Movies",
 "files": [{"staged_as": "Match Point (2005) [imdbid-tt0416320].mkv",
            "sha256": "…", "size_bytes": 6262208596}]}
```

The marker is what makes this safe to re-run and what tells a later reader
that an empty `data/` is a finished job rather than a lost one.

**Never reclaim on the operator's behalf without saying so.** Report the
freed space and the destination in the same breath, so "it worked" and "your
local copy is gone" arrive together rather than one being discovered later.

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
| `ANNIES_POINT`, UPC 883476030029 | `B1_t00.mkv` 1:27:09 | `Movies/Annie's Point (2005) [imdbid-tt0441733]/…` |

The third was published 2026-09-11 and is the first done against the database.
Both the collection title (`Annies Point`) and the disc label (`ANNIES_POINT`)
drop the apostrophe the release has; `title_basics` gave `Annie's Point`,
`tvMovie`, 2005, `tt0441733`, and `runtimeMinutes` 87 against a measured
1:27:09. Its filename contains an apostrophe, which is the character that
previously produced a false MISMATCH in step 9 -- `printf '%q'` carried it
through cleanly.

Note the second: the volume label is the generic `DVD_VIDEO` and the film is
`Fresh Horses`. Always take the name from `makemkv_disc_name`, never from the
label. Both discs' short titles carried no `output_file` -- selection never
saved them -- so there were no extras to stage, which is not the same as
extras having been missed.
