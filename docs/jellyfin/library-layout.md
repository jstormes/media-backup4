# What Jellyfin needs the files to be called

The collection is managed in [Jellyfin](https://jellyfin.org), so the archive
eventually has to become a Jellyfin library. This is what that layout is, and
where what this project produces falls short of it.

Rules taken from the Jellyfin documentation on 2026-09-07:
[Movies](https://jellyfin.org/docs/general/server/media/movies/) and
[Shows](https://jellyfin.org/docs/general/server/media/shows/). Where a rule is
quoted it is quoted exactly, because most of them are unforgiving.

Jellyfin accepts several shapes for most of these. The one that matters is the
one **the existing library already uses**, because a publisher writing a
different legal shape produces a library that is inconsistent rather than
wrong. "In the library as it stands" below records what that is, measured on
2026-09-07 against 235 films and 1,665 episodes.

## Films

One folder per film, and **the file name must begin with the folder name**:

```
Movies/
└── Hancock (2008)/
    └── Hancock (2008).mkv
```

The year is optional and worth having: it is what makes the match reliable. A
provider id pins it outright -- see "Linking to online metadata" below.

### Two cuts of the same film

This is the case the discs in hand actually present, and Jellyfin handles it
directly. Put both in the one folder and give each a version label:

```
Movies/
└── Hancock (2008)/
    ├── Hancock (2008) - Theatrical Cut.mkv
    └── Hancock (2008) - Unrated Extended Cut.mkv
```

Jellyfin then shows **one** film with a version selector rather than two
entries. The rule is strict:

> Each file **must** begin exactly with the parent folder name - including any
> year and/or metadata provider IDs - before adding a version label.

And the separator is not free-form: a space, a hyphen, a space, then the label.
Periods and commas are not supported.

### Extras

Subfolders inside the film's folder, named exactly. Files inside inherit the
film's metadata:

```
Spider-Man - Across The Spider-Verse (2023)/
├── Spider-Man - Across The Spider-Verse (2023).mkv
├── behind the scenes/
│   └── Spider-Verse - The Making Of.mkv
└── deleted scenes/
    └── Alternate Opening.mkv
```

The accepted names are `behind the scenes`, `deleted scenes`, `interviews`,
`scenes`, `samples`, `shorts`, `featurettes`, `clips`, `other`, `extras`,
`trailers`, `theme-music`, `backdrops`.

**The library uses the flat `extras/` for all of them** -- 176 of 235 films
have one, and none use the semantic folders. Which is the honest choice for
this pipeline anyway: nothing on a disc says whether a 4-minute title is a
deleted scene or a featurette, so sorting them into semantic folders would
mean guessing.

### Films split across files

Supported part keywords are `cd`, `dvd`, `part`, `pt`, `disc`, `disk`, with a
space, period, dash or underscore before them: `Movie Name-cd1.mkv`,
`Movie Name-cd2.mkv`. Not something this project should ever need to produce --
a title is saved whole.

## Series

```
Shows/
└── Series Name (2019)/
    ├── Season 01/
    │   ├── Series Name S01E01.mkv
    │   └── Series Name S01E02-E03.mkv
    └── Season 00/
        └── Series Name S00E01.mkv
```

* `Season 01`, zero-padded. The documentation is explicit: *"Do not abbreviate
  the Season name to S01 or SE01"*.
* Specials live in `Season 00`.
* One file holding two episodes is `S01E02-E03`.
* An episode split across files uses the same part keywords as films:
  `Series Name (2025) S01E01-part-1.mkv`.

**In the library as it stands the episode file is bare `S01E01.mkv`**, with no
series name -- 1,665 of them. Both forms are legal and Jellyfin matches either;
the bare form is the one to write here.

## Linking to online metadata

Name and year get a title matched most of the time. A **provider id** makes it
exact, and is worth reaching for when a title is ambiguous, a remake exists, or
Jellyfin keeps grabbing the wrong entry.

### The tags

From Jellyfin's
[Metadata Provider Identifiers](https://jellyfin.org/docs/general/server/metadata/identifiers/):

| Tag | Provider | Applies to |
|---|---|---|
| `[tmdbid-680]` | TMDB | films and series |
| `[tvdbid-79168]` | TVDB | series only |
| `[imdbid-tt0448157]` | IMDb, via OMDB | films and series, English only |

They go in the folder name or the file name:

```
Movies/
└── Hancock (2008) [imdbid-tt0448157]/
    └── Hancock (2008) [imdbid-tt0448157].mkv
```

More than one may be given -- Jellyfin's own example is
`Best_Movie_Ever (1994) [tmdbid-680] [imdbid-1234]`. Note that their example
drops the `tt` from the IMDb id where the rest of their documentation keeps it;
use the full `tt…` form as it appears in the URL.

**Do not copy Plex's syntax.** Plex writes `{imdb-tt0448157}` -- curly braces,
and no `id` on the provider name. Jellyfin wants `[imdbid-tt0448157]`. A great
deal of advice online mixes the two, and the wrong one is simply ignored, which
looks like "Jellyfin did not match it" rather than like a syntax error.

### The tag interacts badly with version labels

The rule that a file must begin **exactly** with its folder name applies to the
provider tag too, so two cuts in a tagged folder repeat the whole thing:

```
Hancock (2008) [imdbid-tt0448157]/
├── Hancock (2008) [imdbid-tt0448157] - Theatrical Cut.mkv
└── Hancock (2008) [imdbid-tt0448157] - Unrated Extended Cut.mkv
```

Which is why the next section is usually the better answer.

### NFO files, and why this project should not write them

A sidecar XML file carries the same identifiers without putting them in every
filename:

| File | For |
|---|---|
| `movie.nfo` (or `<video filename>.nfo`) | a film |
| `tvshow.nfo` | a series |
| `season.nfo` | a season |
| `<episode filename>.nfo` | an episode |

The format is Kodi's. Identifiers are `uniqueid` elements with a `type`:

```xml
<?xml version="1.0" encoding="utf-8"?>
<movie>
  <title>Hancock</title>
  <year>2008</year>
  <uniqueid type="imdb" default="true">tt0448157</uniqueid>
</movie>
```

Two properties look like a natural fit for a publishing step:

> It's currently not possible to disable .nfo metadata. Local metadata will
> always be fetched and has priority over remote metadata providers like TMDb.

So an NFO is authoritative -- whatever it says wins, and there is no library
setting to turn on or forget. And it keeps the filenames plain, which matters
because version labels and provider tags fight for the same filename.

**The live library contains no NFO files at all.** Zero, across 235 films. It
pins metadata with `[imdbid-tt…]` in the folder and file names, and 228 of the
235 carry one. An earlier draft of this document recommended NFOs on the
reasoning above; that recommendation was made without looking at the library
it was aimed at, and is withdrawn.

Write the bracket tag, and match what is there. The reasoning above is not
wrong about NFOs -- they really are authoritative and really do keep filenames
plain -- but introducing a second metadata mechanism into a library that
consistently uses one buys a cleaner filename at the cost of two ways for a
title to be described, which is worse. Being authoritative also means an NFO
is authoritatively wrong when the id is wrong, and this pipeline cannot derive
the id itself.

### Finding the ids

They are in the URL, and they are looked up, never remembered:

* IMDb -- `imdb.com/title/`**`tt0448157`**`/`
* TMDB -- `themoviedb.org/movie/`**`680`**
* TVDB -- the series page on `thetvdb.com`

The disc's own barcode is *not* one of these. `Collection.identifier` holds the
UPC or SKU off the case, which identifies **the physical release** -- a
particular edition, region and packaging -- not the work. It is worth keeping
for exactly that reason, and it can seed a lookup that yields a provider id,
but it is not something Jellyfin understands.

## Formats

`.mkv` is exactly what Jellyfin wants, which is the main reason the switch away
from disc images matters here. Of the alternatives:

> `.iso` files and other disc image formats should work, but are not supported.

and a `VIDEO_TS` or `BDMV` folder "do not support multiple versions, multiple
parts or external subtitle/audio tracks" -- so the two Hancock cuts could not
have been expressed at all in the format this project produced before
2026-09-07.

Subtitles are muxed into the `.mkv` (PGS from a Blu-ray, VobSub and converted
closed captions from a DVD -- see `../makemkv/track-selection.md`), so no
sidecar `.srt` or `.sup` files are needed and none are written.

## In the library as it stands

Measured over SSH on 2026-09-07, on **nas2** at
`/srv/dev-disk-by-uuid-78AA-077A` -- 7.3T, 4.3T used. That path is both the
media root and the container's home: `docker-compose.yml` beside it mounts
`./:/media`, with `Backups/jellyfin_config` and `Backups/jellyfin_cache` as
`/config` and `/cache`.

```
Movies/A Knight's Tale (2001) [imdbid-tt0183790]/
├── A Knight's Tale (2001) [imdbid-tt0183790].mkv    <- repeats the folder name
├── extras/
│   └── A Knight's Tale-F1 T07-8.mkv
├── folder.jpg  logo.png  landscape.jpg  backdrop.jpg
└── *.trickplay/

Shows/Almost Human (2013) [imdbid-tt2654580]/
└── Season 01/
    └── S01E01.mkv
```

| Convention | Holds for |
|---|---|
| `[imdbid-tt…]` in the folder name | 228 of 235 films, every series |
| Main file repeats the folder name exactly | 211 of 235 |
| An `extras/` folder | 176 of 235 |
| Two or more cuts in one folder | 20 of 235 |
| `.nfo` sidecars | **0** |
| Episodes named `S01E01.mkv` | 1,665 files |

The artwork (`folder.jpg`, `logo.png`, `landscape.jpg`, `backdrop.jpg`) and
the `.trickplay` directories are written by Jellyfin, not by whatever put the
media there. A publisher should not create them.

Version labels are where the library is least consistent. The `<folder name>
- <label>` shape is honoured, but the labels are free-form: `- Directors Cut`,
`- Final Cut`, `- [DVD]`, and `- 2.mkv`. That last is a fallback for "the
second cut, name unknown", which is exactly the case a disc presents -- the
edition name lives in the BD-J menu graphics and in no field.

### Permissions

The tree is `root:root` mode `777`, and the container runs as PUID/PGID 1001.
Anything written needs to be world-readable; ownership does not matter.

### Faults worth repairing

A malformed provider tag is **silently ignored** -- the title then matches on
name alone, which looks like Jellyfin being bad at matching rather than like a
typo. Present in the library:

* `[indbid-…]` and `[imbdid-…]` -- one film each, letters transposed.
* `[tt0103359episodes]` on Batman Animated Series -- no `imdbid-` prefix.
* 5 films with no tag at all, and 24 whose main file does not repeat the
  folder name.

## What this project produces, and the distance to the above

Today an attempt writes:

```
collections/<collection-uuid>/
└── discs/<disc-uuid>/
    ├── data/
    │   ├── Fresh Horses-A1_t00.mkv
    │   └── Fresh Horses-B1_t01.mkv
    └── logs/attempt-1.log
```

UUID directories and MakeMKV's own filenames. That is deliberate -- the archive
is organised for recovery and evidence, not for a media server -- so becoming a
Jellyfin library is a **later, separate process** reading `collection.json`.

### What that process already has

| It needs | Where it comes from |
|---|---|
| The film's name | `Disc.makemkv_disc_name`, MakeMKV's `CINFO:2` -- "Fresh Horses", not the `DVD_VIDEO` volume label |
| Which file is which title | `Title.output_file`, reconciled against the files on disk |
| Which titles are cuts of one film | `selection.relationship()` -- shared clip backbone |
| Which cut is the longer one | `Title.duration` / `Title.seconds` |
| Which titles are extras | duration, and that they were not the chosen features |
| Chapter counts, sizes, clip lists | `Title.chapters`, `size_bytes`, `segments` |

### What it does not have, and cannot get from the disc

* **The release year.** Jellyfin wants `(2008)`. No MakeMKV attribute carries
  it. It has to come from the operator or from an online lookup keyed on the
  name. (Occasionally a disc carries authoring metadata that includes it --
  Hancock ships a `FilmIndex.xml` with a `FirstReleaseDate` -- but that is a
  studio artefact, not something to rely on.)
* **Film or series.** Nothing distinguishes a film disc from a TV disc except
  the shape of the title list, and the guess is only a guess.
* **Episode and season numbers.** A TV disc's titles are in playlist order,
  which is usually but not always broadcast order.
* **What an edition is called.** "Unrated Extended Cut" is in the BD-J menu's
  graphics, not in any field. The data supports "the longer cut" and no more.
* **A provider id.** By definition external -- see "Linking to online
  metadata" above. The disc's barcode, which *is* recorded, identifies the
  physical release rather than the work, so it can seed a lookup but cannot
  stand in for one.

So the layout above cannot be produced from the disc alone. The realistic shape
is: the pipeline records everything the disc knows, and the publishing step
asks for the year and the title -- once per collection, not once per file --
and derives the rest.

### Names need sanitising

The disc's own name is not always a legal or sensible filename. "Spider-Man:
Across The Spider-Verse" carries a colon, which is illegal on NTFS and exFAT
and awkward everywhere. Jellyfin's own examples replace it with " - ".

## Publishing is a transfer, not a link

This was open while the library's location was unknown. It is not open any
more: the library is on **nas2** and the archive is on the ripping machine's
local NVMe, so a hardlink or a symlink cannot span them. Publishing copies
bytes across a network.

Which settles how, too. The archive stays local -- `config.validate` requires
`finished_path` and `cancelled_path` to share a filesystem with `media_path`,
because finishing a collection is an atomic rename rather than a
multi-gigabyte copy, and that cannot hold if `media_path` is a network mount.
Nor should it: a mounted share that goes away mid-rip blocks in
uninterruptible sleep, where neither `config.validate` nor the job watchdogs
can see it.

So: rip local, then push. `rsync` over SSH rather than a mount, because a
failed transfer is then an exit code to retry rather than a wedged job, and
`--partial --append-verify` resumes a 40 GB title instead of restarting it.
The step is idempotent, so re-running after any failure is safe.
