# Media Backup — specification and design

**Status:** specification for a reimplementation. Derived from the Python
prototype in this repository as it stood on 2026-09-08.

**Who this is for:** someone building this system again, in another language,
without the prototype to read. It states contracts, invariants and measured
facts rather than code. Where a number appears it was measured against real
hardware and real discs, and the date it was measured is given — those are the
expensive parts, and a port that ignores them will rediscover them the same
way this one did.

**How to read it:** §1–§3 are the problem and the one rule everything serves.
§4–§12 are the system proper. §13–§14 are the contracts with the operator and
with whatever consumes the archive. §15 is how to prove a port correct. §16 and
§17 are the measured facts and the recorded mistakes; they are the reason this
document is worth more than the source.

---

## 1. The problem, and the one invariant

Optical discs rot, and the drives that read them are leaving the market. The
job is to get a shelf of discs onto a filesystem, faithfully, with enough
recorded about each one that a person opening the archive in ten years can
tell what they are looking at.

Everything in this design serves one rule:

> **Never silently lose content, and never claim a backup is good when it is
> not.**

Both halves matter, and the second is the harder one. A crash is loud. A
backup that completed, reported success, and quietly contains one film out of
two is discovered years later when the disc is gone. Every design decision
below that looks paranoid — hashing after transfer, judging on duration rather
than size, refusing to guess which title is the feature — is that rule applied.

The corollary, learned the expensive way (§17.1):

> **When in doubt, keep more, not less. Disk is recoverable. A dropped film is
> not.**

---

## 2. Scope

### In scope

- Group discs into **collections** (a box set, a season, a single film).
- Detect optical drives and the discs in them.
- Scan a disc, record its full title inventory, copy every title to `.mkv`.
- Judge whether the copy succeeded, on evidence rather than on exit status.
- Persist all of it crash-safely, with the provenance of every attempt.
- Present progress and failures to one operator at one machine.

### Out of scope

- Deciding what a title *means* — film, cut, episode, extra. See §9.
- Transcoding, track selection, subtitle handling. Copy what the disc has.
- Naming for a media server. That is a separate downstream step (§14).
- Multi-user, network, or unattended operation. One operator, one machine.
- Ripping the disc itself. That is MakeMKV's job, and it is proprietary.

### The essential difficulty

Not the copying — a subprocess does that. The difficulty is that **the tool
doing the copying reports success unreliably**, in a text protocol, with exit
codes that mean nothing (§3.1). Most of this system is the apparatus for
deciding whether to believe it.

---

## 3. External dependencies and their contracts

### 3.1 `makemkvcon` — robot mode

The only component that can read an encrypted disc. Proprietary, licensed,
built from source, invoked as a subprocess. **The full record of its wire
protocol is in `docs/makemkv/robot-mode.md`; a port must read that document.**
The contract summarised:

**Invocation.** `makemkvcon -r [switches] <command> [args]`. `-r` gives
line-oriented records instead of prose.

| Command | Signature | Use here |
|---|---|---|
| `info` | `info <source>` | enumerate drives; scan a disc |
| `mkv` | `mkv <source> <title id> <dest>` | save one title |
| `backup` | `backup <source> <dest>` | *not used* — see §17.4 |

**Source forms:** `disc:<n>`, `dev:<path>`, `iso:<file>`, `file:<dir>`.
`mkv` and `backup` accept **only** `disc:<n>`, so a device path must be
resolved to an index immediately before each run. Indices are assigned per
scan and are not stable across hotplug.

**Records.** One per line, `PREFIX:field,field,...`, strings double-quoted.

| Prefix | Shape | Meaning |
|---|---|---|
| `MSG` | `code,flags,count,"text","format",params...` | log/error. **Switch on `code`; text is localised.** |
| `DRV` | `index,state,flags,_,"drive","disc","device"` | one per drive slot; 16 slots always emitted, empty ones `state=256` |
| `TCOUNT` | `n` | title count after a scan |
| `CINFO` | `id,code,"value"` | disc-level attribute |
| `TINFO` | `title,id,code,"value"` | title-level attribute |
| `SINFO` | `title,stream,id,code,"value"` | stream-level attribute |
| `PRGT`/`PRGC` | `code,id,"name"` | progress titles (total / current) |
| `PRGV` | `current,total,max` | both progress bars, numerically |

**Parsing rules that are not optional:**

- Split the prefix on the **first** `:` only. Device paths and titles contain colons.
- `PRGV` is the only all-numeric record. Everything else ends in quoted
  strings, so a naive comma split corrupts any value containing a comma.
  Use a quote-aware splitter.
- Ignore unknown prefixes and unknown attribute ids. They vary across versions.
- Quote escaping inside values is **unverified** — no disc encountered has
  exercised it. Treat it as a case to test, not a solved problem.

**Three facts that will bite a port:**

1. **Exit codes are not a success signal.** `makemkvcon` exits `0` for
   operational failures. A run that emitted `Failed to open disc` and produced
   nothing exits `0`. Exit `1` means a usage error — i.e. a bug in your argv.
   *Determine success by parsing output. Never by exit status.*
2. **Progress is a fraction of 65536**, not a percent. Read `max` from the
   record; expect 65536. Treating it as a percentage pins the bar near zero
   for the whole job.
3. **Every run probes every optical drive on the machine**, at engine startup,
   before it reads the source argument. No source form, and not `--noscan`,
   changes this. See §3.3 and §16.1 — this is the reason the isolation layer
   exists.

**Attribute ids used** (full table in `docs/makemkv/attribute-ids.md`):

| id | Level | Meaning |
|---|---|---|
| 1 | C | disc type (`Blu-ray disc`, `DVD disc`) |
| 2 | C, T | name |
| 8 | T | chapter count |
| 9 | T | duration, as `H:MM:SS` |
| 10 | T | size, human-readable — **do not compute from this** |
| 11 | T | size in bytes — use this |
| 16 | T | source file (`00001.mpls`). **DVDs do not emit this** |
| 25 | T | segment count |
| 26 | T | segments map — the clip list. **See §16.3; this one is a trap.** |
| 27 | T | suggested output filename |
| 32 | C | raw volume label (`DVD_VIDEO`) |
| 49 | T | comment — MakeMKV's designator (`A1`, `B2`) |

**Message codes reacted to** (full table in `docs/makemkv/message-codes.md`;
decoded from the shipped gettext catalogues):

| Code | Meaning | Used for |
|---|---|---|
| 5036 / 5037 | copy complete / complete with failures | mkv terminal outcome |
| 5005 / 5004 | titles saved / saved with failures | mkv tally |
| 5003 | failed to save title | partial |
| 5043 | failed to decode AV data | partial |
| 5010 | failed to open disc | failure — **but expected during enumeration** |
| 5042 | no usable optical drives | failure |
| 2016 | no drive access | environment: user not in `cdrom`, or missing `CAP_SYS_RAWIO` |
| 2003 | read error at offset | dirty-disc counter |
| 5068 | destination already contains a backup | failure |
| 1005 | engine started | always first |

Beware the **progress-title forms**: codes 5017, 5024 (mkv) and 5069, 5070,
5079 (backup) carry the same text as the outcome messages, without the
trailing full stop, and arrive as `PRGT`/`PRGC`. Never treat them as outcomes.

**An alternative exists and was rejected.** MakeMKV's own GUI speaks a
shared-memory engine protocol (`makemkvcon guiserver`) offering per-title
selection in one job, mid-job cancellation, and structured data. It costs
hand-marshalling a 64 KB struct against an undocumented ABI. Documented in
`docs/makemkv/engine-protocol.md` so the decision is reversible with evidence.
**Do not start there.** Robot mode is sufficient.

### 3.2 Drive and disc detection — udisks2

One `ObjectManager.GetManagedObjects` D-Bus round trip yields model, vendor,
serial, disc presence, label, filesystem and mount points. No subprocesses.

Two properties matter more than they look:

- **`Drive.MediaCompatibility`** identifies a drive as optical, and stays
  populated when the tray is empty. `Drive.Optical` is `False` on a drive with
  no disc and **cannot** be used for this.
- **`Drive.MediaAvailable`** is the authoritative disc-presence flag. True for
  audio CDs and blank discs, which carry no filesystem and report an empty
  `Block.IdType`.

A port on a platform without udisks2 needs an equivalent that answers: which
drives exist, which have media, what is the media's label and size, and what
device node is each. See `docs/drives/disc-detection.md`.

**Automount must be suppressed** for these drives, or the desktop mounts a
disc mid-rip. Handled here with a udev rule.

### 3.3 Drive isolation — `bwrap`

Because every `makemkvcon` run probes every drive (§3.1, §16.1), and that
probe reaches into a drive that is mid-rip and makes it thrash.

**The mechanism:** MakeMKV finds drives through their **SCSI generic node**
(`/dev/sgN`, *not* `/dev/srN`). A node it cannot open is dropped silently, with
no SCSI command issued. So each run is wrapped so that every `/dev/sg*` except
its own is masked:

```
bwrap --dev-bind / / --bind /dev/null /dev/sg0 ... -- makemkvcon ...
```

Verified: 24 SG_IO commands to the target's node, zero to the other three, one
`DRV` row in the output. Every MakeMKV Docker image relies on the same
property — one container per drive.

**Map `srN` to its `sg` node via `/sys/class/block/srN/device/scsi_generic/`.**
Do not assume `sr0` is `sg0`: `sg` numbers every SCSI device, so a disk
enumerating first shifts one numbering and not the other.

**Consequence:** inside the sandbox the chosen drive is the only drive, so it
is always `disc:0`.

**Isolation is best-effort by design.** If the sandbox tool is missing or the
device cannot be mapped, run unwrapped rather than failing. Probing every drive
is the old behaviour and merely slow; a job that will not start is a disc that
does not get backed up. Surface the degradation to the operator at startup.

A port on another platform needs *some* answer to "make this process see one
drive". If there is none, the fallback is to serialise all jobs.

### 3.4 The filesystem

- `finished/` and `cancelled/` **must** be on the same filesystem as
  `collections/`, so completing a collection is an atomic `rename` rather than
  a copy of 200 GB. Validate this at startup.
- Free-space check before every job: `disc_size * 1.05 + margin`. The 5% is
  headroom for a remux that comes out over the disc's own figure; the margin
  (10 GiB by default) keeps the filesystem off empty.

---

## 4. Domain model

Four entities. All timestamps ISO-8601 UTC with milliseconds. All ids UUIDs.

**Every path stored is relative to the collection directory.** The whole
directory is renamed when the collection is finished, so an absolute path rots
silently at exactly the moment the data becomes an archive nobody looks at.

### Title

One title from a disc scan.

| Field | Type | Notes |
|---|---|---|
| `index` | int | position in the scan's list |
| `name` | string | attribute 2; frequently empty |
| `duration` | string | `H:MM:SS` as MakeMKV writes it |
| `size_bytes` | int | attribute 11 — size **on the disc**, an over-estimate of the remux |
| `source` | string | `00001.mpls` or VTS reference |
| `segments` | string | the clip list. **Read §16.3 before comparing this across titles.** |
| `chapters` | int | |
| `suggested_file` | string | MakeMKV's suggestion. Only a suggestion — see below |
| `designator` | string | `A1`, `B2`. Stable where the index is not |
| `output_file` | string | what it actually became; filled in after the run |
| `streams` | int | count; distinguishes richer from poorer copies of the same content |

`duration` is stored as text because that is what goes in the archive and what
a person reads; everything that compares durations converts to seconds.

### Attempt

One try at copying a disc. **Kept even when it failed — especially then.**

Records: attempt number, start/end, device, drive model and serial, resolved
index, argv, exit code, final and max progress, bytes written, layout,
outcome, failure reason, error kind, read-error and hash-error counts, a
**bounded histogram of message codes seen**, log path, rejected-data path,
eject result.

The message-code histogram is small and is exactly what is needed to tune the
outcome policy once a hundred real discs have gone through.

### Disc

One physical disc: id, ordinal within the collection, volume label,
MakeMKV's disc name, media type, disc size, state, state detail, timestamps,
`titles[]`, `attempts[]`.

**Display name precedence: MakeMKV's disc name, then the volume label, then
`Disc <ordinal>`.** The label is very often the generic `DVD_VIDEO` where
MakeMKV knows the disc as `Fresh Horses`; and even where both are meaningful,
MakeMKV's is the better written one — proper case and punctuation against
`SPIDER_MAN_ACROSS_SPIDER_VERSE`.

### Collection

A boxed set, a season, or a single film — whatever the operator groups.
Id, free-text identifier (UPC/SKU — *not unique, not validated*), title,
notes, `expected_disc_count`, state, timestamps, schema version, app version,
`discs[]`.

**`expected_disc_count` is the only guard against filing an incomplete box
set**, an error otherwise invisible for years. When set, a collection is
complete only when that many discs are good.

---

## 5. State machines

### Disc

```
pending ──▶ queued ──▶ resolving ──▶ scanning ──▶ copying ──▶ verifying ──▶ ejecting ──▶ done
                            │            │           │            │
                            └────────────┴───────────┴────────────┴──────▶ failed
                                                                              │
   (operator gives up) ─────────────────────────────────────────────────▶ abandoned
```

- **Active states:** `queued`, `resolving`, `scanning`, `copying`,
  `verifying`, `ejecting`. Anything in one of these at startup was interrupted.
- **Terminal states:** `done`, `failed`, `abandoned`. `failed` is retryable;
  `abandoned` is the operator's decision to stop.

### Collection

`open` → `finished` | `cancelled`. Both transitions are a directory rename.

`is_complete` requires: at least one disc, every disc terminal, at least one
good, and — if `expected_disc_count` is set — exactly that many good.

The operator may finish anyway. The system's job is to **enumerate the
warnings** (still copying / not backed up / never started / count mismatch)
and make them impossible to miss, not to forbid the action.

### Crash recovery

On startup, any disc in an active state is moved to `failed` with kind
`interrupted` and a reason that says so plainly.

**`makemkvcon` cannot resume**, and it refuses to write into a directory that
already holds a partial backup. An interrupted copy is dead. Say so and offer
a retry rather than pretending it might continue.

---

## 6. Storage layout and durability

```
<media_path>/
  collections/<collection-uuid>/
      collection.json          + collection.json.bak, collection.json.tmp
      discs/<disc-uuid>/
          data/                the saved titles, as .mkv
          logs/attempt-N.log
          rejected/attempt-N/  previous partial output
  finished/<collection-uuid>/  + .complete marker
  cancelled/<collection-uuid>/
```

**One JSON per collection, discs nested inside** — not a file per disc. One
atomic write per state change, and no window in which two files disagree.
Safe because there is exactly one writer (§11).

**The write protocol**, in order, every time:

1. Serialise to `collection.json.tmp`.
2. `flush`, then `fsync` the file descriptor.
3. If the target exists, `rename` it to `collection.json.bak`.
4. `rename` tmp over the target.
5. **`fsync` the directory** — this is what makes the rename itself durable,
   not merely the file contents.

On load, a corrupt `collection.json` falls back to the `.bak`. This has fired
in practice.

**Retry hygiene.** Previous partial output is **moved aside, never deleted** —
a half-copied disc is evidence about why it failed. Move it lazily, at retry
time rather than at failure time, so an operator who never retries still finds
the partial where they would look for it. Keep only the most recent N failed
attempts' *data* (three failed Blu-ray attempts is 100+ GB); **never prune
logs** — they are small and they are what diagnosing a bad disc needs.

---

## 7. The pipeline: one disc, end to end

1. **Resolve.** Enumerate drives; find this device's current `disc:N`.
   Verify the disc in the drive is still the one that was queued.
2. **Check room.** `disc_size * 1.05 + margin` against free space. Fail early.
3. **Check the destination is empty.** `mkv` will not write into a directory
   holding a previous backup.
4. **Scan.** `info` on the drive. Parse `CINFO`/`TINFO`/`TCOUNT` into the
   title inventory. Record the disc's real name and media type. **Not
   optional** — the run saves titles, so something must know what they are.
5. **Select.** §9. Currently: everything.
6. **Copy.** One `mkv` invocation **per title** (§17.4). Stream and parse
   records; emit progress; log every line; watch the watchdogs.
7. **Judge.** §10. Gather observations, apply the decision ladder.
8. **Reconcile.** Match files on disk back to titles (§8).
9. **Eject** — only on success. A failed disc stays in the drive so the
   operator can take it out, clean it and retry.

### Watchdogs

| Watchdog | Default | Why |
|---|---|---|
| stall | 1800 s | no progress *and* no messages. A disc grinding through read retries still emits `MSG:2003`, so it is not silent — a truly silent run is wedged. |
| probe | 300 s | enumerate and scan finish in seconds (14 s for four loaded drives, measured 2026-09-07) and produce **no output at all** when a drive wedges, so the stall watchdog cannot see them. |
| job | 21600 s | absolute ceiling. |

Cancellation is process termination — robot mode offers nothing better.
Escalate: signal, grace period, kill; report `unkillable` if it survives.

## 8. Matching files back to titles

MakeMKV's suggested filename embeds the title's index **within the list it was
showing when asked**, and that number can move between the scan and the save.
The files on disk are ground truth. Reconcile by the three things that do not
move, in order:

1. exact filename match against `suggested_file`, where it happens to match;
2. MakeMKV's **designator** (attribute 49) appearing in the name;
3. failing both, **closest size**.

A file is never claimed twice. The result is written to `Title.output_file`
and is the archive's only link from metadata to bytes. Without it, a reader
finds four `.mkv` files and no way to tell which is which.

---

## 9. Title selection

> **Save every title the scan reports. That is the whole policy.**

The system does **not** decide which title is the feature, and a port must not
add that back. The reasoning is worth stating in full because the temptation
is strong and the failure is silent.

An earlier design saved "the feature": the longest title and anything within
90% of it, after removing duplicates and refusing discs that looked protected.
It failed in both directions, silently, and reported the discs as complete:

- **A DVD double feature lost its second film.** `FIREHEAD_AND_LAST_LIVES`
  offers 1:35:43 and 1:23:43 — the shorter is 87% of the longer, under the 90%
  line, so it was never a candidate.
- **A second one lost its second film to the duplicate filter**, for the
  reason in §16.3.
- **A kids' disc of twenty-minute shorts, and a TV disc of episodes, have no
  feature to find at all.** Episodes are feature-length by every structural
  test available from a scan and a box set by every other.

There is no ratio, floor or count that separates a second feature from a
feature-length documentary, or an episode from a decoy playlist, **using the
data a scan provides**. The question "which title is the work?" is not
answerable here. It is answerable at publish time, where there is a person,
the disc sleeve, and the option of asking.

**MakeMKV's own default minimum title length (120 s) is the only filter**, and
it is applied by MakeMKV. Neither the scan nor the save passes `--minlength`,
so what the scan lists is exactly what the save writes. Keep it that way: the
two must see the same list or the indices diverge.

**The one refusal left:** a disc whose scan reports no title with a duration.
There is nothing to copy.

**The cost, measured** over every disc scan in the archive (2026-09-08):
255 GB → 414 GB, a factor of 1.6. Worst case Hancock, 149 GB from a 45 GB
disc, because it authors each of its two cuts twice and offers seventeen of
the feature's clips as titles in their own right. **That is the trade: disk,
which is recoverable, against silently dropping a film, which is not.**

### What is still computed, because it describes rather than decides

- `segments(title)` — expand the clip list. Two spellings: a Blu-ray lists
  global clip ids (`123,141,125`), a DVD gives a cell range (`1-28`). Expand
  ranges before counting.
- `shared_ratio(a, b)` — overlap as a fraction of the shorter clip list.
- `relationship(titles)` → `cut_variants` | `separate_works` | `single`.
  **Both conditions are required for `cut_variants`:** the titles share a
  backbone (over half the shorter list) **and each carries clips the other
  lacks.** Seamless branching stores common footage once and the differing
  segments separately — Hancock's two cuts share 10 clips and hold 9 exclusive
  each. A subset relationship is *not* branching; requiring exclusives on both
  sides is what stops a DVD's `{1..12}` inside `{1..15}` scoring 1.0 and
  reporting two different films as two cuts of one (§17.2).

---

## 10. Judging a run

Gather everything observable into one struct; judge it with **one pure
function**. That split is deliberate: the failure taxonomy of several hundred
real discs cannot be enumerated up front, so every real-world failure becomes
a fixture and revising the policy is a test-first afternoon rather than an
archaeology dig.

**Verdicts:** `success`, `success_unverified`, `partial`, `failure`,
`cancelled`. `success_unverified` means *keep it, but do not claim it was
checked* — the distinction that stops the system lying.

**The observation** carries: exit code, message-code histogram, max total
progress and its max, whether any progress was seen, output layout, bytes
written, files written, titles expected, expected bytes, and per-title
verification results (short / unverified counts).

**The decision ladder**, in order — order is part of the specification:

1. Cancelled → `cancelled`.
2. Exit code 1 → `failure`. The only exit code that means anything: a usage
   error, i.e. a bug in the argv.
3. Any **fatal** message code → `failure`, reporting **the cause code, not the
   generic announcement**. MakeMKV prints both; the announcement says only
   that the run died. Reporting it sends the operator to clean a disc over
   what is really a permissions problem.
4. Nothing on disk → `failure`.
5. Progress below the floor (0.99 of `PRGV`'s own max) → `failure`.
6. Fewer files than titles asked for → `partial`. **Not a failure:** one lost
   extra is not the same news as a lost feature, and the operator decides
   which this was.
7. Any saved title **shorter than the disc says it is** → `failure`.
   *This is the completeness check that matters* — see §16.2.
8. Bytes above the ceiling (1.5×) → `success_unverified`, "something was saved
   more than once". A floor alone waves a doubled run through.
9. A file that would not report its duration → `success_unverified`, falling
   back to a size floor (0.70) only here.
10. All titles present, all full length, and MakeMKV said so → `success`.
11. All measurably complete but MakeMKV never said so → `success_unverified`.

---

## 11. Concurrency

- **One job per drive, always.** A second process on the same device fights
  the first for the tray.
- An optional global cap, for a machine whose disk cannot keep up with four
  drives at once.
- **Exactly one writer of application state.** Worker threads emit immutable
  events; a single coordinator thread applies them to the model and persists.
  There is no lock anywhere in the coordinator because there is one writer.
  A port using a different concurrency model must preserve the property, not
  the mechanism.
- **Retries are manual, deliberately.** An automatic retry re-reads the same
  unreadable sectors of the same dirty disc for hours, and each attempt costs
  moving tens of gigabytes aside. The operator cleans the disc and asks again.

**Events:** `state`, `progress` (coalesced; droppable), `message`,
`identified` (the scan says what the disc really is), `finished` (terminal,
carries the verdict and observation; releases the drive slot).

---

## 12. Configuration

All tunable, defaults chosen to be **safe, not fast**. The values below were
measured; §16 says how.

| Setting | Default | Rationale |
|---|---|---|
| `media_path` | — | base for everything |
| `cache_mb` | 1024 | MakeMKV read cache |
| `decrypt` | true | |
| `isolate_drives` | true | §3.3 |
| `max_concurrent_jobs` | 0 | 0 = one per drive, no global cap |
| `min_free_margin_bytes` | 10 GiB | headroom beyond `disc_size * 1.05` |
| `size_ratio_floor` | 0.70 | the yardstick over-estimates; §16.2 |
| `size_ratio_ceiling` | 1.5 | catches saving the same footage twice |
| `duration_tolerance_s` | 10 | a correct Blu-ray title came back 0.6 s under |
| `stall_timeout_s` | 1800 | |
| `probe_timeout_s` | 300 | |
| `max_job_duration_s` | 21600 | |
| `eject_on_success` | true | |
| `keep_rejected_attempts` | 1 | |

Validate at startup and report problems as **error** (fatal) or **warning**
(degraded but usable) — a missing sandbox tool is a warning, a
`finished_path` on another filesystem is an error.

---

## 13. Operator interface contract

The UI is not specified. What it must make possible is:

- See every drive, what is in it, and whether it is busy.
- Create a collection, add the disc in a drive, start it.
- See per-job state, progress and the current step.
- See **why** a disc failed, in words that name the next action.
- Retry a failed disc; abandon one; cancel a running one.
- Finish a collection, **with the premature-finish warnings shown** (§5).
- Cancel a collection — which **moves it aside, never deletes it**. A mis-click
  must not destroy hours of ripping.

Two presentation rules carry meaning:

- A disc that needs a **person** is visually distinct from one that **failed**.
  Retrying the first changes nothing; it is waiting on a human. *(With the
  selection policy of §9 nothing currently produces this state, but the
  distinction is part of the model and cheap to keep.)*
- Never call a title "the main feature". The system does not know that.
  Call it "longest".

---

## 14. Downstream contract: publishing

Publishing to a media server is a **separate step** with a person in it, and
it inherits the job §9 refuses. Its full procedure is in
`.claude/skills/publish-to-jellyfin/SKILL.md`. The parts that are contract
rather than convenience:

- **The archive is the source and stays untouched.** Stage by **hardlink**, so
  there is no second copy and deleting the staging area leaves the archive
  whole. Hardlinks cannot cross filesystems — check before staging.
- **Reclaim only after a checksum match on both ends.** rsync's exit code and
  a matching size are not enough on a filesystem that stores no permissions or
  fine-grained times. Hash both ends and compare.
- **Keep `collection.json` and the logs forever.** They are about a megabyte
  and they are the entire record that the disc was ripped, what it held, and
  what the tool said while doing it. Reclaiming space is not discarding
  provenance.
- **Write a marker** recording where each file went and the hash that matched,
  so an empty `data/` reads as a finished job rather than a lost one.
- **Report every title left behind, with the reason.** A reader must be able
  to tell from the report alone that nothing was lost.

---

## 15. Proving a port correct

The prototype has 443 tests and no mocking framework. The strategy transfers.

**Test doubles at the process boundary.** Everything that matters is a pure
function over a struct, or a class taking an injected spawner, clock and
ejector. Nothing touches D-Bus in tests.

**Verbatim captures as fixtures.** The scan output of a real Blu-ray and a
real DVD, and a complete `mkv` run's 275 progress records, are stored as text
and replayed. New real-world failures become new fixtures. *This is the single
highest-value practice here — a port should copy the fixtures themselves,
which are in `tests/makemkv_fixtures.py` and are language-neutral text.*

**Real containers, sparse.** Duration verification reads a real Matroska
header; the test files carry genuine headers and are sparse past them, because
judging reads the declared duration and `st_size`, never the frames. *An early
version built a literal 4.5 GB byte buffer in tmpfs and triggered a global
OOM that logged the desktop out.* Do not do that.

**The cases a port must get right**, each of which is a real bug this system
had:

| Case | Expected |
|---|---|
| DVD double feature, 87% ratio | both films saved |
| Two DVD titles both reporting `1-12` | both saved; not deduplicated |
| Season disc, 8 similar-length titles | all 8 saved |
| Blu-ray with the feature authored twice | both saved (dedup is publish's job) |
| Cell range `{1..12}` inside `{1..15}` | `separate_works`, **not** `cut_variants` |
| Blu-ray title correct but 84% of reported size | `success`, not failure |
| A copy that stopped at 20% of duration | `failure` |
| A file that will not report duration | `success_unverified`, never `success` |
| `makemkvcon` exits 0 having saved nothing | `failure` |
| Scan index moved between scan and save | files still matched to titles |
| App killed mid-copy | disc reads `failed`/`interrupted` on restart |
| `collection.json` truncated | recovered from `.bak` |

---

## 16. Appendix: measured facts

Each was established against real hardware and cost real time. A port inherits
them for free.

### 16.1 MakeMKV probes every drive, always (2026-09-08, v1.18.4)

With `strace -f -e trace=openat,ioctl`, four drives, none loaded:

| Command | Drives sent SCSI commands | SG_IO total |
|---|---|---|
| `info disc:9999` | sg0–sg3 | 69 |
| `info dev:/dev/sr0` | sg0–sg3 | 70 |
| `--noscan info dev:/dev/sr0` | sg0–sg3 | 60 |
| `info disc:1` | sg0–sg3 | 69 |

Neither the source form nor `--noscan` nor the hidden `io_SingleDrive` setting
(tried as `"1"`, `"0"` and `"/dev/sr0"`) changes it. Reported to the vendor in
2011, 2013, 2015 and 2024; never fixed. On seven drives it costs 1m55s of
startup before the first record.

Masking the `sg` nodes works: 24 SG_IO to the target, zero elsewhere.

### 16.2 Size is a bad yardstick; duration is a good one (2026-09-07)

`TINFO:11` is the title's size **on the disc**, not a prediction of the remux.
Measured: a Blu-ray title came out at **0.84** of it, a DVD at **0.98**. A
0.90 size floor **failed a byte-perfect Blu-ray backup twice**, reporting
"only 84% of the expected size was written". The number was right; the
comparison was wrong.

Duration is the honest measure. A copy that stopped early is *short*, the disc
said how long each title runs, and neither figure is affected by container
overhead or dropped tracks. A correct Blu-ray title came back **0.6 s** under
what the scan reported — hence a 10 s tolerance.

Read duration out of the Matroska container directly (element ids in RFC 9559)
rather than shelling out to a media framework. A backup tool should not
acquire that dependency to answer a question the file already carries.

### 16.3 A DVD's segments map is meaningless across titles (2026-09-08)

**The single most expensive fact in this document.**

A Blu-ray's segments map (attribute 26) names **global clip files** — `123`,
`800`, `1130` — so two titles listing the same clips really do share footage.
A DVD's is a **cell range local to its own title**, and every DVD title's
range starts at 1.

Measured across all 21 disc scans in the archive: **every DVD title expands to
exactly `{1..N}`.** On one disc six unrelated extras all report `1,2`. A
double feature reports `1-12` for *both* films.

Any cross-title conclusion from a DVD segments map is nonsense. It cost a
film (§17.1).

To tell the spellings apart: if every title on the disc expands to exactly
`{1..N}`, the ids are title-local; one title that does not is enough to say
they are global. Verified against all 21 scans — every DVD reads local, every
Blu-ray global.

### 16.4 Playlist obfuscation is structural and visible (2026-09-07)

Parsing the Hancock Blu-ray's 175 playlists directly:

| Playlist | Duration | Play items | Distinct clips | Chapters |
|---|---|---|---|---|
| 00002 / 00004 | 1:42:14 | 19 | 19 | 16 |
| 00001 / 00003 | 1:32:13 | 19 | 19 | 16 |
| 00530 | 1:37:18 | 101 | **2** | 1 |
| 00529 | 1:36:20 | 100 | **1** | 100 |

A decoy is a playlist of a hundred play items pointing at the *same* clip: a
feature's duration and no other property of one. MakeMKV already filters
these — it reported `TCOUNT:4`.

Also useful: **BDMV navigation metadata is not AACS-encrypted.** Only the
streams are, so `index.bdmv`, `MovieObject` and every `.mpls` can be parsed off
a disc without decrypting anything.

### 16.5 Track selection is not controllable per run (2026-09-07)

`makemkvcon --profile=<name or file>` is **accepted and ignored** — verified
including with MakeMKV's own shipped FLAC profile, which left the audio as
AC3. An unknown profile name is not an error; it succeeds silently, so a typo
is never noticed.

The only lever is `app_DefaultSelectionString` in the user's
`~/.MakeMKV/settings.conf`, which is **global to the user**, not per-run. The
defaults keep every track the disc carries, which is what is wanted here.

Note the consequence: what an archive contains depends on an ambient setting
outside the application. A run can be given a private `HOME` holding its own
settings file (proved to work, copying the licence key across) if that ever
needs pinning.

### 16.6 DVD closed captions are captured

MakeMKV finds EIA-608 captions embedded in the MPEG-2 video, converts them to
a text subtitle track and labels it `S_CC608/DVD`. No action needed; worth
knowing they are not lost.

---

## 17. Appendix: mistakes to inherit rather than repeat

### 17.1 Guessing which title is the feature

Cost: two films, silently, both discs reported "all titles saved". Root causes
in §9 and §16.3. **The fix was not a better threshold** — the first attempt
was a separate keep-floor plus a duration guard on every cross-title use of
the segments map. It worked, recovered both films, and was still wrong,
because a kids' disc and a season disc have no threshold at all. The question
had to stop being asked here.

### 17.2 Calling two films two cuts of one

`relationship()` originally required only a shared backbone. One DVD cell
range is routinely a subset of another — `{1..12}` inside `{1..15}` scores 1.0
— so a double feature read as `cut_variants`. Real branching means **each cut
carries clips the other lacks**. Requiring exclusives on both sides is the fix.

### 17.3 Judging completeness on size

§16.2. Failed a perfect backup twice. The lesson generalises: **when a check
fails on known-good input, suspect the yardstick before the input.**

### 17.4 `backup` instead of one `mkv` per title

`makemkvcon backup` writes a directory for a Blu-ray and a single ISO for a
DVD, and refuses any destination it did not create itself. `mkv` writes titles
into a directory whatever the media is — one shape for every disc.

And `mkv ... all --minlength` **cannot express "these two of the four"**: a
length filter cannot separate a title from another of the same runtime.
Hancock offers its feature twice, and that wrote 88 GB where the film is 44,
with the second copy claimed by no title. **One invocation per title, naming
the title id, is what stops that** — and it remains right under §9's
copy-everything policy, because it is what keeps every file traceable to the
title it came from.

### 17.5 Trusting the announcement over the cause

MakeMKV prints both a generic "backup failed" and a specific cause code.
Reporting the announcement sent the operator to clean a disc over what was
really a `cdrom`-group permissions problem. **Always report the cause.**

### 17.6 A 4.5 GB test fixture

Built as a literal byte buffer in tmpfs, six times over. Triggered a global
OOM; the kernel killed a process in the desktop's cgroup and logged the
session out mid-work. Fixtures are sparse now. Run heavy test suites under a
memory cap so the cap dies instead of the desktop.

---

## 18. What a port should build first

In order, each independently testable against recorded fixtures:

1. **The record parser** (§3.1) — quote-aware, first-colon split. Everything
   else depends on it and it is pure. Test it against the captured scans.
2. **The model and the store** (§4, §6) — round-tripping and the atomic write.
3. **The outcome judge** (§10) — a pure function; replay the captured runs.
4. **File-to-title matching** (§8) — pure; test the awkward cases without
   writing four gigabytes.
5. **The runner** — process spawning, streaming, watchdogs. First point that
   needs a real subprocess; inject the spawner.
6. **Drive detection** (§3.2) and **isolation** (§3.3) — platform-specific,
   and the most likely to differ in a port.
7. **The coordinator** (§11) and the UI (§13).

Selection (§9) is a dozen lines and belongs with the runner. That it is a
dozen lines, and was once three hundred, is the main thing this document has
to say.
