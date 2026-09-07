# Backup pipeline: state and next steps

Recorded 2026-09-06, mid-plan, so the work survives a reboot. The original
plan lived in a session that was lost to an OOM (see "How this got
interrupted" at the bottom).

## Done and green

`/usr/bin/python3 -m unittest discover -s tests -t .` -> **413 tests, OK**.

Committed already:

- `makemkv/records.py`, `command.py`, `enumeration.py`, `inspect.py`,
  `messages.py`, `outcome.py` -- robot-mode parsing, argv construction,
  outcome policy.
- `model.py`, `store.py` -- collection/disc model and crash-safe on-disk store.

Uncommitted in the working tree:

- `events.py` (new) -- `JobEvent`, the worker -> GUI message.
- `makemkv/runner.py` (new) -- `BackupRunner`: resolve index, verify the disc,
  check space, spawn, read, judge, eject. One job, one thread, injectable
  `spawn` and `clock`.
- `jobs.py` (new) -- `JobManager`, the coordinator. See below.
- `gui_app.py` (rewritten) -- the window, wired to all of it. See below.
- `eject.py` (new) -- `Drive.Eject` over the udisks2 system bus.
- `drives.py` (modified) -- carries `object_path` / `drive_object_path` on
  `DriveState` so eject has something to call.
- `model.py` (modified) -- one addition, `ERR_STORE`, for a job that never
  started because the store could not clear the way for it.
- `tests/test_backup_runner.py`, `tests/test_eject.py`, `tests/test_jobs.py`
  (new); `tests/test_gui_app.py` (extended).

## The `jobs` coordinator, now written

`JobManager` owns the queue and is the only writer of job state. Everything in
it runs on the GUI thread; runner threads only call `_on_event`, which hands
the event straight back through `dispatch` -- `root.after(0, ...)` under
tkinter, a direct call without a GUI. No locks, one writer.

Decisions taken while writing it:

- **One slot per drive**, always. `cfg.max_concurrent_jobs` is a global cap on
  top of that when non-zero. A queued job for a *free* drive is not blocked by
  a busy one ahead of it in the queue -- head-of-line blocking would idle a
  drive that has a disc sitting in it.
- **Retries are manual.** This was the open question. A failed disc stays in
  `FAILED` -- retryable, never retried on its own. Automatic retry would
  re-read the same unreadable sectors of the same dirty disc for hours, and
  each attempt costs a `prepare_attempt` move of tens of gigabytes. Retrying
  is just calling `enqueue` again, which is what the operator does after
  cleaning the disc; `attempt` is incremented by `store.prepare_attempt` at
  start time, not at enqueue time.
- **Cancel** signals the runner and does *not* release the slot; the slot is
  the runner's until it reports FINISHED, because makemkvcon takes seconds to
  die. Cancelling a job that is still *queued* restores the disc to whatever
  state it had before, rather than inventing a failed attempt that never ran.
- **`ABANDONED` is a separate operator action** (`abandon()`), not what a
  cancel produces. A cancel is retryable; giving up is not.
- **Progress is never persisted.** It arrives four times a second for hours.
  The durable record of a job is its `model.Attempt`; `JobStatus` is the live
  view the progress bar reads.
- **A save failure never takes the pipeline down.** The copy may still
  succeed, and a stale JSON is recoverable at the next startup.

One thing deliberately left empty: `Attempt.makemkv_index`, `source_spec` and
`argv`. The runner resolves the index on its own thread and does not report it
back, and widening `JobEvent` to carry it was not worth it -- the argv it
actually ran is the first line of the attempt log.

## `gui_app.py`, now wired

Every piece has a caller. `main()` loads and validates the config and shows a
`ProblemWindow` instead of the app when anything is fatal -- `media_path` on a
volume that is not mounted yet is the normal failure, and it has to produce
something the operator can act on. Non-fatal problems become a banner inside
the window, along with anything `recover_interrupted` had to settle.

Two event sources drive the window and nothing else: `DriveMonitor` for what
is in the drives, `JobManager.on_change` for what the jobs are doing. Both
deliver on the tkinter main thread. `attach_to_tkinter(root)` is called on the
manager before any enqueue can happen, and `_on_close` stops the jobs *before*
the D-Bus thread, so a runner still has a loop to report FINISHED into.

Decisions taken while writing it:

- **A drive card shows the drive; the collection panel shows the job.** The
  card offers one button ("Back up this disc") and, while a job is running in
  that drive, a one-line summary instead of the button -- the backup already
  going is the only thing that may happen to that disc. Progress belongs to
  the disc, not to the tray it happens to be sitting in.
- **The disc list is a `Treeview`, not a column of cards.** It scrolls itself,
  which leaves the one piece of hand-rolled scroll machinery in the file (for
  the drive cards, with its own regression tests) unduplicated. Actions apply
  to the selected row.
- **"Back up this disc" is `add_disc` + `enqueue` in one press.** `PENDING`
  stays reachable and meaningful: it is what a disc goes back to when its
  queued job is cancelled before it started.
- **The window rules on nothing.** Whether a disc may start, what a failure
  means, whether the collection is safe to finish: it asks `jobs`, `model` and
  `store` and renders the answer. A window with its own policy would be a
  second policy nobody tests.
- **`JobManager.on_change` is public**, so a window that did not build the
  manager can still wire itself to it. That is what the tests do.
- Retrying picks the drive the disc was last in, or the only loaded free drive.
  A guess is safe: the runner resolves the device to a `disc:N` and refuses
  outright if the label is not the one that was chosen.

Verified against live udisks2, not only in tests: the app starts with a real
config, finds both physical drives, and offers the button on each. Nothing was
actually ripped -- that starts a multi-hour makemkvcon run on a real disc and
is the operator's call.

## The first real disc, and what it found (2026-09-06)

Two discs were put through the app for real. The Blu-ray copied fine. The DVD
failed instantly, and the reason turned out to be a rule nobody had written
down anywhere:

**``makemkvcon backup`` produces a different shape per media, and the
destination has to match it.**

* **Blu-ray** -- a **directory** (``BDMV/``, ``CERTIFICATE/``, ``MAKEMKV/``).
  It is content to find an empty directory already there.
* **DVD** -- a single decrypted **ISO image file**, and it insists on creating
  that file itself. Handed a path already taken, it answers MSG:5068,
  *"already contains a backup, please choose another folder"*.

``prepare_attempt`` created ``data/`` as a directory for every disc. For a DVD
that is a directory sitting where makemkvcon wanted to create a file, so every
DVD failed on its first message, and the message described a situation that was
not happening: the directory was empty, and provably so -- its mtime matched
the millisecond ``prepare_attempt`` made it.

Three controlled runs settled it. Same disc, same argv, one variable:

| destination | result |
| --- | --- |
| existing empty directory | MSG:5068, immediate failure |
| absent | proceeds, and writes an ISO (``file``: "ISO 9660 ... 'DVD_VIDEO'") |
| ``dev:/dev/sr0`` instead of ``disc:1`` | exit 10, silent, no output at all |

That last row is a separate quirk worth remembering: ``backup`` does not accept
a ``dev:`` source, only ``disc:N``. ``enumeration.py`` resolving a device to an
index is therefore load-bearing, not a nicety.

### What changed as a result

* ``CollectionStore.wants_image(disc)`` decides the shape from the udisks2
  media string: ``optical_bd*`` gets ``data/`` created for it, everything else
  gets ``data.iso`` and a path that must not exist. Unknown media counts as an
  image -- that guess fails loudly, where the other one is what produced the
  misleading 5068 in the first place.
* ``inspect`` gained ``ISO`` as a layout and ``looks_like_iso`` (the ISO 9660
  descriptor at 0x8001, checked rather than trusting an extension we chose
  ourselves); ``tree_size`` handles a single file; ``judge`` accepts ``iso`` as
  a recognised layout, so a good DVD image passes on its own merits.
* ``BackupRequest.dest_dir`` is now ``dest``. It is not always a directory, and
  a name that says otherwise is the exact class of mistake this whole incident
  was made of.
* The 5068 hint no longer asserts that the directory needs clearing. It said
  that with total confidence and it was wrong, and it cost about four
  experiments' worth of looking in the wrong place.
* A DVD's rejected partial is parked *inside* ``rejected/attempt-N/`` rather
  than replacing that directory, so pruning and ``rejected_bytes`` keep working
  unchanged for both shapes.

Verified against the real hardware, not only in tests: a genuine ISO classifies
as ``iso`` with the right size, ``/dev/sr0`` (``optical_dvd``) routes to an
absent ``data.iso``, and ``/dev/sr1`` (``optical_bd``) still gets the existing
empty ``data/`` that its in-flight backup is using.

**Still unproven:** no DVD has yet been backed up all the way through. The
image was killed at 604 MB once it had shown what shape it takes. The next DVD
run is the one that confirms the ISO is judged good rather than merely
produced.

### A second thing the first real run found: the Cancel button flashed

``_render_disc_actions`` unpacked and repacked all four action buttons on
every job event, and a running job emits progress every
``PROGRESS_INTERVAL_S`` -- so the Cancel button was torn out of the layout and
put back four times a second for the whole length of a copy.

The fix is the discipline ``DriveFrame.update_drive`` already had and this
path did not: re-render only what changed. ``_show_disc_buttons`` now touches
the layout only when the offered set differs, and ``grid_shown`` /
``grid_text`` return early when the widget is already in the state asked for
-- which incidentally settles the drive card too, where only the one label
carrying the percentage now gets reconfigured per tick.

Worth remembering as a shape rather than a one-off: **anything reachable from
``on_change`` runs at progress frequency.** A re-render there has to be free
when nothing moved.

### Disc metadata: MakeMKV knows more than the volume label does

A DVD's volume label is very often a generic stamp -- the one here reads
``DVD_VIDEO`` -- while MakeMKV identifies the disc perfectly well. The scan
the job already runs was fetching that and dropping it: ``_scan_titles``
skipped every record that was not a ``TINFO``, so the whole ``CINFO`` block
went in the bin, and ``Disc.makemkv_disc_name`` -- a field that has existed
since the model was written -- was never set by anything.

The proof was already on disk. That failed DVD's own ``collection.json`` had
four titles recorded, every one of them named **"Fresh Horses"**, sitting
behind a UI that said ``DVD_VIDEO``.

What is available, and now used:

* ``CINFO:2`` -- the disc name. Falls back to the largest title's name, since
  it is not established that a DVD emits ``CINFO:2`` at all (see the fixture
  note in ``tests/makemkv_fixtures.py``).
* ``CINFO:1`` -- the disc type, "DVD disc" / "Blu-ray disc".
* ``TINFO`` per title -- name, duration, size, and source file. Already
  captured; now surfaced as "21 titles, main feature 2:20:05".

Still on the floor, if it is ever wanted: ``CINFO:28`` metadata language,
``CINFO:32`` the raw volume name, ``TINFO:8`` chapter count, ``TINFO:19/20/21``
video size, aspect and frame rate, and the whole ``SINFO`` block -- per-stream
codec, language and channel layout. ``records.py`` parses all three record
types already; only the attribute ids need picking out.

The name is carried on a new ``IDENTIFIED`` event emitted the moment the scan
finishes, rather than waiting for FINISHED: an hour into a copy is too late to
stop calling the disc ``DVD_VIDEO``, and a job that dies later should still
have recorded what the disc was. ``display_name`` now prefers MakeMKV's name
over the label -- it is the better-written one either way,
"Spider-Man: Across The Spider-Verse" against
``SPIDER_MAN_ACROSS_SPIDER_VERSE``.

## The pipeline has now worked end to end (2026-09-07)

The Spider-Man Blu-ray completed for real: **46.5 GB, layout ``bdmv``, exit 0,
no read or hash errors, progress reached PRGV's max, ejected cleanly**, in 40
minutes. ``judge()`` returned ``success`` on MSG:5081 plus a recognised
layout. Message codes seen on a good run: 1005, 5070, 5072, 5081, 5085 --
5085 is not in ``messages.py`` yet and should be looked up.

The DVD fix is confirmed live too: a DVD is backing up to ``data.iso`` as this
is written, where every DVD failed instantly before.

### Are the backups actually decrypted? (verified 2026-09-07)

Yes, both kinds -- checked against the real output, not inferred from the fact
that ``--decrypt`` is on the command line.

* **DVD.** CSS scrambles the video and audio PES payloads and records it in
  the PES header's 2-bit ``scrambling_control`` field. 3,974 packets sampled
  evenly across the image: every one zero. The ISO carries a ``VIDEO_TS``
  directory and is in the clear.
* **Blu-ray.** AACS encrypts the aligned units inside the M2TS streams; each
  192-byte BDAV packet's ``TP_extra_header`` opens with a 2-bit
  ``copy_permission_indicator`` that a decrypter zeroes. 4,500 packets across
  the three largest of 172 streams: every one zero. The backup holds ``BDMV``,
  ``CERTIFICATE`` and ``MAKEMKV`` -- no ``AACS`` directory at all.

**The gap this exposes:** ``judge()`` cannot tell. It weighs message codes,
exit code, progress, size ratio and layout, and an encrypted copy passes every
one of them -- full size, right layout, MSG:5081 and all. If ``--decrypt``
ever silently failed, or ``cfg.decrypt`` were turned off, the result would be
a full-size, "successful", permanently unplayable archive, and nobody would
find out for years. That is exactly the failure this project's design notes
are otherwise so careful about.

Both checks above are cheap -- a few thousand sampled packets, well under a
second -- and would slot into ``inspect`` as an observation for ``judge()`` to
weigh. Not implemented: it adds a new way for a backup to be rejected, which
is a policy decision rather than a bug fix.

### The window was built for a 96-dpi desktop, and this one is not (2026-09-07)

Reported as "the text of the discs is cut off, like the box is too short".
It was, and the cause was underneath the whole file rather than in one widget.

This machine is 8192x2880 with Tk scaling 1.33, so ``TkDefaultFont`` has a
**37px linespace** where a normal-DPI desktop gives it about 20. Every length
in ``gui_app.py`` was a pixel constant written against that 20:

* **ttk's Treeview keeps its default row height whatever the font does**, so
  37px of text sat in a ~20px row and every row clipped itself top and bottom.
  This is the one that matches the report exactly, and no amount of widening
  columns would have touched it.
* Every column clipped its text: "Waiting for the drive" wants 271px and had
  180; a disc name wants 474 and had 300; even ``100%`` wanted 81 in an 80px
  column.
* The card title was hardcoded ``("Helvetica", 10, "bold")`` -- 28px, so the
  bold heading was *smaller* than the 37px body text under it.
* ``wraplength`` was fixed at 780/800, and ``minsize``/``geometry`` were flat
  pixels.

Everything now derives from the font. ``MainWindow._configure_metrics`` reads
the interface font once, sets ``Treeview`` rowheight from its linespace, and
exposes ``scale()``/``text_width()``; column widths are measured from the
widest string each column can be asked to hold; ``ui_font()`` derives the bold
and heading fonts from the desktop's own rather than naming a family and a
size. The regression tests assert *relative to the font*, so they hold on any
display rather than encoding this one.

Two layout faults turned up on the way and are fixed with it:

* **The action row was starved to one pixel.** It was packed last, and pack()
  hands out space in packing order -- fixed chrome packed after expanding
  widgets is the first thing squeezed. Its detail text was simply not on
  screen. It is now packed to the bottom before the two lists.
* **The drive list and the disc list were fighting for the window**, the drive
  canvas asking for a flat Tk default (7cm) whatever was in it. They are now
  in a ``ttk.PanedWindow``: the drive pane takes the height its cards need up
  to a cap counted in text lines, spare room goes to the disc list, and the
  operator can drag the sash. The sash is refitted only when the card list
  itself changes, so a drag survives everything else.

## Switched from `backup` to `mkv` (2026-09-07)

The pipeline now runs ``makemkvcon mkv`` and produces a directory of ``.mkv``
files. The same shape for both media, which deletes the entire DVD-versus-
Blu-ray destination problem: ``wants_image``, the ISO layout and the MSG:5068
trap are all gone, because ``mkv`` is content to find an empty directory
whatever the disc is.

It is also better instrumented. ``backup`` announced itself with a bare 5081
and left the rest to size heuristics; ``mkv`` ends with an explicit tally:

| code | meaning |
| --- | --- |
| `5036` | Copy complete. N titles saved. |
| `5037` | Copy complete. N titles saved, **M failed** |
| `5005` / `5004` | the same counts without the announcement |
| `5003` | Failed to save title N to file |
| `5016` | Directory is invalid -- the mkv analogue of 5068 |
| `5043` | Failed to decode AV data of title N |

Decoded from MakeMKV's own gettext catalogues by the recipe already written
down in ``docs/makemkv/message-codes.md``. The same lookup settled the code
left open from the first Blu-ray: **5085 is "Loaded content hash table, will
verify integrity of M2TS files"**, which is benign.

``judge`` is rebuilt on that. The disc's own size is no longer the yardstick
-- an MKV run leaves out menus, duplicate angles and unwanted tracks by
design -- so the run is measured against **the selection**: every chosen title
present as a file, at about the size the scan said it was, with MakeMKV's own
count agreeing. Fewer files than asked for is ``PARTIAL``, not failure: one
lost extra is not the same news as a lost feature, and the operator decides.

### Playlist obfuscation, and refusing to guess

Some Blu-rays carry dozens of decoy playlists all cut to roughly the feature's
length, precisely so a tool picking "the longest title" picks garbage.
``makemkv/selection.py`` refuses those discs rather than guessing: past
``max_feature_titles`` (5) titles of feature length, the disc goes back to the
operator to do by hand in the MakeMKV GUI.

Feature length is **relative** -- 90% of the longest title -- because that is
the shape of the protection, and because an absolute threshold misfires on
ordinary discs. The Blu-ray backed up on 2026-09-06 has one feature at 2:20:05
and extras running to 14:49, so "anything over ten minutes" would have called
four titles features. Relative calls one.

The same rule handles a TV disc: four episodes of similar length are four
features, all saved, and only past five does it read as protection.

**Known limitation.** One threshold does two jobs: it picks what to save *and*
detects decoys. Lowering ``feature_ratio`` to keep a disc's extras would also
make ordinary discs look protected. Decoupling them -- a separate floor for
what to keep -- is a small change if extras turn out to be wanted.

### Verified against real output

The transcript in ``tests/makemkv_fixtures.py`` is a real capture: four titles
saved from the finished "Fresh Horses" ISO, 275 PRGV records, the whole
completion sequence. That also closes the note about PRGV cadence being
uncharacterised. Running ``mkv`` against ``iso:`` and ``file:`` sources works,
so MKVs can still be made from the two collections already archived in the old
formats -- nothing reads them any more, but nothing is stranded either.

## Reading a real protected disc (2026-09-07)

The Hancock Blu-ray was examined directly. **BDMV navigation metadata is not
AACS-encrypted** -- only the streams are -- so ``index.bdmv``, ``MovieObject``
and all 175 ``.mpls`` playlists can be parsed off the disc without decrypting
anything. That is worth knowing on its own.

### What the theory says, and why it does not help here

The principled way to find the real feature is to do what a player does:
``index.bdmv`` names a First Playback title, that resolves to a Movie Object,
and its ``PlayPL`` command names a playlist. That works for **HDMV** discs,
where the navigation is a small command list in ``MovieObject.bdmv``.

Hancock is a **BD-J** disc -- it has ``BDJO/`` and ``JAR/`` -- so the playlist
is chosen by Java bytecode, and answering "which playlist would play?"
statically means running the disc's own application. That is why the practical
tools are all heuristic, and why monitoring PowerDVD works when static
analysis does not.

### What the disc's data does say, plainly

Parsing every playlist gave a clean answer without any of that:

| playlist | duration | play items | distinct clips | chapters |
| --- | --- | --- | --- | --- |
| 00002 / 00004 | 1:42:14 | 19 | 19 | 16 |
| 00001 / 00003 | 1:32:13 | 19 | 19 | 16 |
| 00530 | 1:37:18 | 101 | **2** | 1 |
| 00529 | 1:36:20 | 100 | **1** | 100 |

The obfuscation is structural and obvious once looked at: a playlist of a
hundred play items that all point at the *same* clip. It has a feature's
duration and no other property of one. A real title's clips are distinct.

The four survivors are **two films** -- the theatrical cut and the extended
cut -- each authored twice with an identical clip list.

### What changed

MakeMKV already filters the two decoys: it reports ``TCOUNT:4`` even at
``--minlength=5000``, which would have included them on length. So the real
gap was duplicates, not decoys, and MakeMKV hands us what is needed to close
it -- ``TINFO:26``, the segments map, is the clip list itself.

* ``Title`` gained ``segments`` (attribute 26) and ``chapters`` (8).
* ``selection.distinct()`` drops titles whose segments map is already taken.
  Hancock goes from four titles to two, halving what gets written.
* ``selection.is_degenerate()`` is the backstop for a disc where MakeMKV does
  not filter: ten or more segments with under half of them distinct.
* Deduplicating happens **before** the decoy count. That was a latent bug --
  three cuts authored in pairs would have read as six features and been
  refused.

The obvious remaining gap is a disc whose decoys are *structurally plausible*
-- distinct clips, sane chapters, feature length. Nothing here would catch
that, and neither would MakeMKV. Watching which title a player picks stays the
only sure answer for one of those.

### Deciding whether we can decide

The disc-level question is not "which title is the feature?" but "can this be
answered from here at all?", and the answer is now explicit.

``Selection.needs_operator`` is true when the disc is fine and the *choice* is
beyond us. That is deliberately narrower than "not ok": a disc with nothing
worth saving does not need help, because there is nothing to help with.

**Decided** when there is one feature-length title, or a few that differ in
ways real content differs.

**Handed back** on any of:

* more than ``max_feature_titles`` candidates -- the classic decoy pile;
* two candidates playing *the same clips in a different order*. Genuine cuts
  differ in which clips they use -- Hancock's extended cut pulls in clips the
  theatrical one never touches -- so a pure reordering is the disc hiding
  which title is real;
* a feature-length candidate with a single chapter, which a real feature does
  not have.

When it hands back it says what it saw. The reason carries the candidate list
-- playlist name, duration, chapter count, how many of its clips are distinct
-- so the operator opens MakeMKV already knowing what they are choosing
between, and the same list goes into the attempt record.

The GUI stops calling these discs "Failed". They read **"Needs you"** in amber:
nothing is broken, retrying changes nothing, and it is waiting on a person.
An ordinary copy failure still reads as failed and still offers a retry.

Two spellings of the segments map turned up and both are handled: a Blu-ray
lists its clips (``123,141,125``), a DVD gives a cell range (``1-28``). The
range is expanded before anything counts distinct clips, so an ordinary
28-cell DVD title is not mistaken for a decoy.

### Telling the two cuts apart, and which file is which

Hancock's two surviving titles are a theatrical cut and an extended cut, and
the clip lists say so outright. Seamless branching stores the common footage
once and the differing segments separately:

```
shared          123 125 127 129 131 133 135 137 139 150   (10 clips)
theatrical only 124 126 128 130 132 134 136 138 140       ( 9 clips)
extended only   141 142 143 144 145 146 147 148 149       ( 9 clips)
```

Nine branch points, alternating shared segment and variant segment. That
structure is the answer: ``selection.relationship()`` reports ``cut_variants``
when the candidates all share a backbone (over half the shorter title's clips)
and ``separate_works`` when they do not -- four episodes of a series share
nothing but perhaps a title card and fall well under the line.

**What can be derived, and what cannot.** That these are two cuts of one film,
which is longer, and exactly which segments differ: all of it, from the disc.
The *names* -- "Unrated Extended Version" and so on -- cannot. Those live in
the BD-J menu's graphics and Java, not in any structured field. A later
process gets "the longer cut" and "the shorter cut" with the evidence, and
that is as far as the data goes.

**Which file is which.** MakeMKV's suggested output filename (attribute 27)
embeds the title's index *within the list it was showing when asked*, and the
save pass runs a different ``--minlength`` from the scan, so that number can
move. The archive would otherwise record four titles and leave whoever comes
back to it guessing which ``.mkv`` is the extended cut.
``selection.match_files()`` reconciles titles to the files actually on disk,
by the three things that do not move: the exact name where it happens to
match, MakeMKV's own designator (attribute 49, "A1"/"B2"), and failing both,
size. ``Title.output_file`` records the answer.

## Next steps

Nothing is blocked. In rough order of worth:

- **Restart the app to pick up the DVD fix and the flicker fix.** The
  instance that ran on 2026-09-06 has the old code loaded; its Blu-ray will
  finish fine, but every DVD it is asked for will keep failing until it is
  restarted.
- **Confirm the finished DVD is judged good.** The copy itself is proven --
  an ISO is being written right now -- but no DVD has yet reached ``judge()``,
  so the ``iso`` layout has not been exercised against a real image.
- **Confirm the file matching on a real multi-title save.** It is tested
  against the awkward cases but has only been exercised for real on a disc
  where the suggested filename happened to be right.
- **Watch a disc that genuinely defeats this.** The remaining hole is decoys
  that are structurally plausible -- distinct clips, sane chapters, feature
  length. Hancock is not one of those. When one turns up, the PowerDVD method
  is what settles it, and its title list is worth recording here as a fixture.
- **Run a disc through the mkv path for real.** Every layer is tested against
  a real transcript, but no disc has yet been read by this pipeline end to
  end. The HANCOCK Blu-ray in sr1 is the obvious candidate, and a Blu-ray is
  also the first chance to see whether the decoy rule fires on a real one.
- **Check the window on a normal-DPI screen.** Everything is derived from
  the font now and the tests are relative to it, but it has only ever been
  looked at on one display.
- **Decide whether ``judge()`` should verify decryption** -- see above. The
  check is written and proven against both formats; only the policy is open.
- **Capture real transcripts.** Two now exist and both should replace
  hand-built fixtures: the 40-minute Blu-ray backup log under
  `discs/<id>/logs/attempt-1.log`, and a real DVD `info` scan -- which would
  finally settle whether `CINFO:2` is emitted for a DVD and let the fallback
  in `_scan_titles` be dropped.
- **Commit.** The tree has grown four new modules and three new test files
  since the last commit.
- Small known wart: a pending `after(50, _update_scroll)` outlives a destroyed
  window and Tk complains to stderr ("invalid command name ..._update_scroll").
  It predates this work, it is noise rather than a fault, and the fix touches
  the scroll machinery that has its own hard-won regression tests -- so it was
  left alone deliberately rather than missed.
- `Attempt.makemkv_index` / `source_spec` / `argv` are still empty; see above.

## How this got interrupted

`tests/test_backup_runner.py` built its "finished disc" fixture as a literal
4.5 GB `bytes` object, written into a tmpfs `/tmp`. Six tests did it. That
drove a global OOM; the kernel killed a python3 that happened to sit in the
`org.gnome.Shell@ubuntu.service` cgroup, systemd marked the service
`oom-kill`, and the desktop session was logged out mid-plan.

The fixture is now sparse (`fh.truncate(n)` -- judging reads `st_size`, never
the bytes), in `test_backup_runner.py` and in `test_jobs.py` alike. Run
anything heavy under a cap so the cap dies instead of the desktop:

    systemd-run --user --scope -q -p MemoryMax=4G -p MemorySwapMax=0 \
        /usr/bin/python3 -m unittest discover -s tests -t .

Note `/usr/bin/python3` -- the default `python3` on this box is a venv without
PyGObject. `llama-test.service` was stopped and disabled to get the memory
back; re-enable with `systemctl --user enable --now llama-test.service`.
