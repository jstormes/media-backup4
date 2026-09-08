# MakeMKV Interface Test Suite

A comprehensive test plan for verifying the `makemkvcon` interface between
the Rust backend and MakeMKV.

**Verified against:** MakeMKV 1.18.3, LG BD-RE BU40N (`/dev/sr0`), Linux x64.
**Status:** Most tests verified against real hardware. Mark ⚠ where verification
is pending (needs different disc type or longer-running job).

---

## 0. System prerequisites

| Requirement | Status |
|---|---|
| `makemkvcon` installed and in `$PATH` | ✅ `/usr/local/bin/makemkvcon` |
| User can read `/dev/sr0` (cdrom group + udev ACL) | ✅ |
| JRE present for BD-J discs | ✅ OpenJDK 25 |
| Free permanent licence key (not the rotating beta key) | ✅ |
| Writeable destination directory | ✅ |

---

## 1. Drive enumeration

### 1.1 Enumerate all drives

```bash
makemkvcon -r --cache=1 info disc:9999
```

There is no `list` or `info driver` subcommand. This idiom asks for a
nonexistent disc index and reads the `DRV` lines before the failure.

This opens every drive on the machine — as does every other command, whatever
source it is given. Under the sandbox this project runs it in, it lists exactly
one drive: see
[robot-mode.md](robot-mode.md#every-run-probes-every-drive).

**Emitted output (spider-man-disc inserted):**

```
MSG:1005,0,1,"MakeMKV v1.18.3 linux(x64-release) started","%1 started","MakeMKV v1.18.3 linux(x64-release)"
DRV:0,2,999,12,"BD-RE HL-DT-ST BD-RE BU40N FR07 902HS017569","SPIDER_MAN_ACROSS_SPIDER_VERSE","/dev/sr0"
DRV:1,256,999,0,"","",""
...
DRV:15,256,999,0,"","",""
MSG:5010,0,0,"Failed to open disc","Failed to open disc"
```

### 1.2 Parse rules

- 16 `DRV` records emitted (slots 0–15) regardless of drive count.
- **Filter on `state` (field 2), not position.**
  - `2` = disc inserted
  - `3` = loading (transient after tray close)
  - `256` = no drive
- Empty slots carry empty strings for name/title/device.
- Exit code is **always 0** (even for the failure message).

### 1.3 Drive states

| State | Meaning |
|---|---|
| `0` | Empty, tray closed |
| `1` | Empty, tray open |
| `2` | Disc inserted (ready) |
| `3` | Loading |
| `256` | No drive in this slot |
| `257` | Unmounting |

---

## 2. Disc scan — `info <source>`

### 2.1 Scan a disc by device path

```bash
makemkvcon -r --progress=-same --cache=1 info dev:/dev/sr0
```

Returns the full `CINFO` / `TINFO` / `SINFO` metadata tree plus `TCOUNT`.

### 2.2 Scan by MakeMKV drive index

```bash
makemkvcon -r --cache=1 info disc:0
```

Same result as 2.1. Prefer `dev:` in production code (drive indices can shift
on hotplug); `disc:` is acceptable for quick scans in a stable environment.
Neither form stops MakeMKV opening every other drive first — see
[robot-mode.md](robot-mode.md#every-run-probes-every-drive).

### 2.3 Output structure

```
MSG:1005,...          ← engine started (always first)
MSG:3007,...          ← direct disc access mode (LibreDrive)
MSG:5085,...          ← content hash table loaded
MSG:3025,...          ← sub-120s clips skipped (info only)
MSG:3307,...          ← titles discovered
MSG:3309,...          ← duplicate playlists skipped
CINFO:1,6209,"Blu-ray disc"        ← disc type
CINFO:2,0,"Spider-Man: Across The Spider-Verse"  ← disc name
CINFO:28,0,"eng"                     ← disc language
TCOUNT:21                              ← total titles
TINFO:0,2,0,"Spider-Man: Across The Spider-Verse"  ← title 0, name
TINFO:0,9,0,"0:04:47"                  ← title 0, duration
TINFO:0,10,0,"308.1 MB"               ← title 0, display size (do not parse)
TINFO:0,11,0,"323100672"              ← title 0, exact bytes (use for arithmetic)
TINFO:0,16,0,"00736.m2ts"              ← title 0, source file
SINFO:0,0,1,6201,"Video"              ← title 0, stream 0, video
SINFO:0,0,5,0,"V_MPEG4/ISO/AVC"       ← codec
SINFO:0,0,19,0,"1920x1080"            ← resolution
SINFO:0,0,28,0,"eng"                  ← language code
SINFO:0,1,1,6202,"Audio"              ← audio stream
TINFO:1,2,0,"Next title..."           ← title 1
...
```

### 2.4 TCOUNT

Emitted once after scan completion.

| Value | Meaning |
|---|---|
| `> 0` | Valid titles found |
| `0` | Nothing usable (empty disc, sub-120s clips only, etc.) |

**Success gate:** require `TCOUNT > 0` and no `MSG:5010` on the target source.

### 2.5 Attribute ID quick-reference

| ID | Name | Example use |
|---|---|---|
| `2` | `ap_iaName` | Disc/title name |
| `8` | `ap_iaChapterCount` | Chapter count |
| `9` | `ap_iaDuration` | `H:MM:SS` |
| `10` | `ap_iaDiskSize` | Display string (do not parse) |
| `11` | `ap_iaDiskSizeBytes` | Exact bytes (compute from this) |
| `16` | `ap_iaSourceFileName` | `00001.mpls` |
| `19` | `ap_iaVideoSize` | `1920x1080` |
| `21` | `ap_iaVideoFrameRate` | `23.976` |
| `27` | `ap_iaOutputFileName` | Suggested `.mkv` name |

Full attribute enum (0–50) is in [`attribute-ids.md`](attribute-ids.md).

---

## 3. Copy a disc — `backup <source> <dest>`

### 3.1 Full disc backup

```bash
makemkvcon -r --progress=-same --cache=1 backup disc:0 /path/to/out
```

**Requires `disc:` source, not `dev:`.** The `dev:` form fails with:

```
Backup source must start with "disc:"
```

### 3.2 Decrypted backup (for Blu-ray)

```bash
makemkvcon -r --progress=-same --decrypt --cache=1 backup disc:0 /path/to/out
```

### 3.3 Progress output

With `--progress=-same`, the process emits `PRG*` records on stdout:

```
PRGT:5018,0,"Scanning CD-ROM devices"   ← total job name
PRGC:5018,0,"Scanning CD-ROM devices"   ← current sub-step name
PRGV:0,0,65536                           ← current, total, max
```

**`PRGV.max` is 65536, not 100.** Convert to percentage:

```
pct = (value * 100.0) / 65536.0
```

`PRGV` carries **both** bars: field 0 is the sub-step progress, field 1 is
the whole-job progress.

### 3.4 Verification

- The process exits 0 on success.
- The destination directory contains `BDMV/`, `AACS/`, `CERTIFICATE/`, and
  a `discatt.dat` file.
- **Never rely on exit code alone** — operational failures also return 0.

### 3.5 ⚠ Unverified

- Full end-to-end timing on a large disc (4× Blu-ray ≈ 128 GB) not yet tested.
- `PRGV` emission cadence during a multi-hour copy not characterized.
- Mid-job disc eject / I/O error behavior untested.

---

## 4. Copy one title — `mkv <source> <title id> <dest>`

### 4.1 Single title remux

```bash
makemkvcon -r --cache=1 mkv disc:0 0 /path/to/out
```

Copies title #0 to a `.mkv` file in the destination folder.

### 4.2 ⚠ Unverified

- Output filename convention (not yet captured).
- Behavior with a nonexistent `title id` (tested locally; returns 0 and emits
  `MSG:5010`, but need to confirm across versions).

---

## 5. DVD vs. Blu-ray parity

### 5.1 Same flags for both

✅ Verified that `info`, `backup`, and `mkv` commands accept the same flags
and produce the same output format (DRV/MSG/TINFO/SINFO/PRG) for Blu-ray.

The same flag set (`-r --cache=1`, `--progress=-same`, `--decrypt`) applies
to both media types.

### 5.2 ⚠ Unverified

- **No DVD drive available for testing.** A DVD disc should produce the same
  `CINFO`/`TINFO`/`SINFO` records, but with:
  - `CINFO:1` value likely `"DVD-ROM disc"` or similar
  - `CINFO` id `6201` = DVD type vs `6209` = Blu-ray
  - Potentially `VIDEO_TS` directories instead of `BDMV`

**Action needed:** When a DVD is available, run `makemkvcon -r --cache=1 info dev:/dev/dvd0`
and compare the output structure. If the format matches, this requirement is
satisfied.

---

## 6. Error cases

### 6.1 No disc / drive not responding

```bash
makemkvcon -r --cache=1 info disc:99        # empty slot
```

**Output:**
```
DRV:0,...,"SPIDER_MAN_ACROSS_SPIDER_VERSE","/dev/sr0"     ← other drives ok
DRV:1,256,999,0,"","",""                                  ← empty slot
MSG:5010,0,0,"Failed to open disc"
TCOUNT:0
```

**Exit code: 0.** Parse `MSG:5010` and `TCOUNT:0` for the failure signal.

### 6.2 Nonexistent disc index

```bash
makemkvcon -r --cache=1 info disc:9999
```

**Output:**
```
DRV:0,...                                                   ← enumeration (all 16 slots)
MSG:5010,0,0,"Failed to open disc"                         ← expected failure
```

**Exit code: 0.** The `5010` here is intentional — this is the enumeration idiom.
Do **not** treat `5010` from `disc:9999` as an error; it is the expected signal.

### 6.3 Drive not responding (unplugged during scan)

⚠ **Untested.** Requires hot-unplugging a USB drive mid-scan. Expected output:
`MSG:5010` or some I/O error message, followed by `TCOUNT:0`.

### 6.4 Usage error

```bash
makemkvcon -r badcommand
```

**Output:** Usage help. **Exit code: 1.** This is the only exit code that
indicates a real error.

### 6.5 Error summary

| Situation | Exit code | Success signal |
|---|---|---|
| Successful scan | `0` | `TCOUNT > 0`, no `MSG:5010` on target |
| Empty drive | `0` | `TCOUNT:0` |
| Bad disc index (enum idiom) | `0` | Intentional — parse DRV lines |
| Nonexistent drive index | `0` | `MSG:5010` |
| Usage error | `1` | N/A — this is the only real error code |

**Rule: Parse output, never rely on exit status.**

---

## 7. Progress routing — advanced

### 7.1 Separate files for progress and messages

```bash
makemkvcon -r --progress=/tmp/makemkv-prog.log --messages=/tmp/makemkv-msg.log \
    backup disc:0 /path/to/out
```

Routes `PRG*` records to one file and `MSG` records to another. Useful for
decoupling the UI (progress bar) from the log (status messages).

### 7.2 `--progress=-stdout`

Writes progress to stdout. Equivalent to not using `--progress` at all on
non-TTY stdout.

---

## 8. Test plan for the AI agent

When implementing the spec, run these tests against the target system to
verify the integration before writing any code.

### Phase 1: Smoke tests (must pass before coding)

| # | Command | Check |
|---|---------|-------|
| 1 | `makemkvcon -r --cache=1 info disc:9999` | ≥ 16 `DRV` lines; at least one with `state=2` |
| 2 | `makemkvcon -r --cache=1 info dev:/dev/sr0` | `TCOUNT > 0`; at least one `TINFO` + `SINFO` per title |
| 3 | `makemkvcon -r --cache=1 mkv disc:0 0 /tmp/test-out` | Output `.mkv` file created |
| 4 | `timeout 10 makemkvcon -r --progress=-same backup disc:0 /tmp/test-out` | Emits `PRG*` records; exits 0 |

### Phase 2: Error handling tests

| # | Command | Expected |
|---|---------|----------|
| 5 | `makemkvcon -r --cache=1 info disc:99` | `MSG:5010` + `TCOUNT:0`; exit 0 |
| 6 | `makemkvcon -r backup dev:/dev/sr0 /tmp/test` | Fails with `"Backup source must start with disc:"` |
| 7 | `makemkvcon -r badcmd` | Exit 1 (usage error) |

### Phase 3: DVD parity test (when DVD available)

| # | Command | Check |
|---|---------|-------|
| 8 | `makemkvcon -r --cache=1 info dev:/dev/dvd0` | Same record format as Blu-ray; `TCOUNT > 0` |
| 9 | `makemkvcon -r --cache=1 info disc:0` (DVD) | Same `CINFO`/`TINFO`/`SINFO` structure |

### Phase 4: Concurrency test (important for multi-drive setup)

| # | Setup | Check |
|---|---------|-------|
| 10 | Two `makemkvcon` processes targeting different drives | Both run simultaneously without interference |
| 11 | `strace -f -e trace=ioctl makemkvcon -r --cache=1 info dev:/dev/srX` | SG_IO goes to **every** drive's `sg` node, not just `srX`'s — the behaviour isolation exists to stop |
| 12 | The same command under `bwrap` with the other `/dev/sg*` masked | SG_IO to one node only; one `DRV` row, at index 0; masked nodes fail `openat` with EACCES |

Test 12 is what `src/media_backup/makemkv/isolation.py` builds. It needs no
disc: with empty drives the probe is still visible, which is the point.

---

## 9. Known issues and edge cases

### 9.1 Exit code 0 on failure

`makemkvcon` returns 0 for operational failures (disc not found, no titles).
**Always parse output, never check exit status alone.**

### 9.2 Quote escaping unverified

String fields are double-quoted. A quote inside a value (e.g., in a title
name like `"Best of "Friends"`) would need escaping (`\"`). No disc encountered
so far has exercised this. Test before trusting quote parsing.

### 9.3 `--cache` recommendation

Always pass `--cache=<MB>` (≥ 1) to avoid full-device reads on every scan.
`--cache=1` (1 MB) is the minimum; 32–256 MB is typical for good performance.

### 9.4 Progress fraction is 1/65536

`PRGV.max` is `AP_Progress_MaxValue = 65536`, **not** 100 or 1000.
Converting `PRGV` as a percentage yields progress pinned near zero.

### 9.5 Stdout buffering

When MakeMKV's stdout is a pipe (not a TTY), progress updates may arrive in
bursts rather than line-by-line. If this becomes an issue, use `stdbuf -oL`
to force line buffering.

---

## 10. Summary

| Requirement from ticket #3 | Status |
|---|---|
| `makemkvcon info driver N` (enumerate drives) | ✅ Covered by `DRV:` lines from `info disc:9999` |
| `makemkvcon info disc N` (disc info, track list) | ✅ Covered by `info dev:/dev/sr0` / `info disc:0` |
| `makemkvcon avconf` with `-r` (progress) | ❌ Not available in v1.18.3; `backup --progress=-same` is the equivalent |
| DVD and Blu-ray same flags | ✅ Confirmed same command set; DVD untested physically |
| Error cases (no disc, unreadable, unresponsive) | ✅ No disc / empty slot covered; unresponsive not tested |
| Exact flags and output format | ✅ Documented throughout |
| Test plan for AI agent | ✅ Sections 8, phases 1–4 |
