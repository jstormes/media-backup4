# `makemkvcon -r` — robot mode reference

The machine-readable interface. Add `-r` (or `--robot`) to any command and
output becomes line-oriented records instead of prose.

Verified against MakeMKV 1.18.3 on Linux, 2026-08-23.

---

## Commands

```
makemkvcon [switches] <command> [parameters]
```

| Command | Signature | Purpose |
|---|---|---|
| `info` | `info <source>` | Enumerate drives, or scan a disc and emit its full title/stream tree. |
| `backup` | `backup <source> <dest folder>` | Copy a whole disc to disk. **This project's primary operation.** |
| `mkv` | `mkv <source> <title id> <dest folder>` | Remux one title to `.mkv`. |
| `reg` | `reg <key or filename>` | Store a registration key. |
| `f` | `f <args>` | Drive firmware tool. Not used here. |

`sdftool` (a symlink to `makemkvcon` created at install) exposes an unrelated
drive-firmware mode. Ignore it.

## Source specification

| Form | Meaning |
|---|---|
| `disc:<n>` | Drive by MakeMKV index — `disc:0` is the first drive. |
| `dev:<path>` | Drive by OS device node, e.g. `dev:/dev/sr0`. |
| `iso:<file>` | An ISO image. |
| `file:<folder>` | An unpacked disc directory (`BDMV`/`VIDEO_TS` parent). |

**Prefer `dev:` over `disc:` for anything targeting a specific drive.** MakeMKV
indices are assigned per scan and are not stable across drive hotplug; the
device node is. `disc:` is fine for the enumeration idiom below because it
targets no real drive.

## Switches

Only `-r` appears in `makemkvcon --help`. **The help output is not the switch
list** — the following all work and were confirmed accepted by the shipped
binary. Do not conclude a switch is missing because help omits it.

| Switch | Status | Notes |
|---|---|---|
| `-r`, `--robot` | Documented | Machine-readable output. |
| `--progress=<dest>` | **Verified working** | Routes `PRG*` records. Use `-same` to interleave with messages on stdout. |
| `--messages=<dest>` | Verified present | Routes `MSG` records. |
| `--debug[=file]` | Verified present | Diagnostic log. |
| `--cache=<MB>` | **Verified working** | Read-cache size. |
| `--minlength=<sec>` | Present, not directly exercised | Title-length floor. Default is **120 s** — observed in messages: *"has length of 12 seconds which is less than minimum title length of 120 seconds"*. |
| `--decrypt` | Present, not directly exercised | Decrypt video during `backup`. Corresponds to `AP_BackupFlagDecryptVideo=1`. |
| `--directio`, `--noscan` | Present in binary | Not exercised. |

`<dest>` for the routing switches is one of `-same`, `-stdout`, `-stderr`,
`-null`, or a filename.

### Recommended invocations

```bash
# Enumerate drives (see idiom below)
makemkvcon -r --cache=1 info disc:9999

# Scan a disc
makemkvcon -r --progress=-same --cache=1 info dev:/dev/sr0

# Back up a whole disc, decrypted
makemkvcon -r --progress=-same --decrypt backup dev:/dev/sr0 /path/to/out
```

---

## ⚠ Exit codes are not a success signal

**Verified behaviour:**

| Situation | Exit code |
|---|---|
| Successful disc scan | `0` |
| Nonexistent drive index (`disc:9999`) | `0` |
| Nonexistent ISO path | `0` |
| Unrecognised switch | `1` |

`makemkvcon` returns **0 for operational failures.** A run that emitted
`Failed to open disc` and produced no titles still exits 0. Exit `1` appears to
be reserved for usage errors.

**Therefore: determine success by parsing output, never by exit status.** For a
scan, require `TCOUNT` > 0 and absence of a fatal `MSG`. For a backup, wait for
the completion message and verify the output directory.

---

## Output records

One record per line. Format is `PREFIX:field,field,...` where string fields are
double-quoted.

### `MSG` — log and error messages

```
MSG:code,flags,count,"message","format",("param",...)
```

`message` is the rendered text; `format` and the trailing params are the
un-substituted form, useful for matching regardless of interface language.
**Switch on `code`, not on text** — text is localised.

```
MSG:1005,0,1,"MakeMKV v1.18.3 linux(x64-release) started","%1 started","MakeMKV v1.18.3 linux(x64-release)"
MSG:5010,0,0,"Failed to open disc","Failed to open disc"
```

Codes observed in practice:

| Code | Meaning |
|---|---|
| `1005` | Engine started (always first). |
| `5010` | **Failed to open disc** — the key failure signal. |
| `5042` | No usable optical drives found. |
| `5074` | Update-check-enabled notice. Benign. |
| `5018` | Scanning CD-ROM devices (also appears as a `PRG*` name). |

This list is what was observed, not exhaustive.

### `DRV` — one per drive slot

```
DRV:index,state,flags,unused,"drive name","disc name","device path"
```

Emitted for **16 slots (0–15)** regardless of how many drives exist. Empty
slots carry `state=256`. Filter on state, not on position.

```
DRV:0,2,999,12,"BD-RE HL-DT-ST BD-RE BU40N FR07 902HS017569","SPIDER_MAN_ACROSS_SPIDER_VERSE","/dev/sr0"
DRV:1,256,999,0,"","",""
```

`state` values are in [`attribute-ids.md`](attribute-ids.md#drive-states);
`2` = disc inserted, `256` = no drive.

### Drive enumeration idiom

There is no `list` command. To enumerate drives, ask for a drive index that
cannot exist and read the `DRV` lines before the failure:

```bash
makemkvcon -r --cache=1 info disc:9999
```

This emits all 16 `DRV` records, then `MSG:5010 Failed to open disc` and exits
**0**. The `5010` here is expected and must not be treated as an error.

### `TCOUNT` — title count

```
TCOUNT:21
```

Emitted once after a successful scan. `TCOUNT:0` means nothing usable was found.

### `CINFO` / `TINFO` / `SINFO` — the metadata tree

```
CINFO:id,code,"value"                     disc-level
TINFO:title,id,code,"value"               title-level
SINFO:title,stream,id,code,"value"         stream-level
```

`id` indexes the attribute enum — see [`attribute-ids.md`](attribute-ids.md).
`code` is a message-table reference for enumerated values (`0` when the value is
free text).

Real output:

```
CINFO:1,6209,"Blu-ray disc"
CINFO:2,0,"Spider-Man: Across The Spider-Verse"
CINFO:28,0,"eng"

TINFO:1,2,0,"Spider-Man: Across The Spider-Verse"
TINFO:1,8,0,"16"                     ← chapter count
TINFO:1,9,0,"2:20:05"                ← duration
TINFO:1,10,0,"29.3 GB"               ← human-readable size
TINFO:1,11,0,"31506235392"           ← size in bytes  ← use this one
TINFO:1,16,0,"00001.mpls"            ← source playlist

SINFO:1,0,1,6201,"Video"
SINFO:1,0,5,0,"V_MPEG4/ISO/AVC"
SINFO:1,0,19,0,"1920x1080"
SINFO:1,0,21,0,"23.976 (120000/5005)"
```

Note the pairing at ids **10 and 11**: `10` is a formatted string for display,
`11` is exact bytes. Always compute from `11`.

### `PRGT` / `PRGC` / `PRGV` — progress

```
PRGT:code,id,"name"        total operation — the whole job
PRGC:code,id,"name"        current operation — the sub-step in flight
PRGV:current,total,max     both bars, numerically
```

```
PRGT:5018,0,"Scanning CD-ROM devices"
PRGC:5018,0,"Scanning CD-ROM devices"
PRGV:0,0,65536
```

### ⚠ Progress is a fraction of 65536, not a percent

`max` is **65536** (`AP_Progress_MaxValue` in `apdefs.h`, confirmed live). It is
not 100 and not 1000.

```rust
let pct = value as f64 * 100.0 / max as f64;   // max == 65536
```

Treating `PRGV` as a percentage yields a progress bar pinned near zero for the
entire job. Read `max` from the record rather than hardcoding, but expect 65536.

`PRGV` carries **both** bars in one record: `current` is the sub-step, `total`
is the whole job.

---

## Parsing rules

- Split the prefix on the **first** `:` only. Device paths and titles contain
  colons.
- `PRGV` is the only record that is purely numeric. Every other record ends in
  one or more quoted strings, so a naive `split(',')` will corrupt any value
  containing a comma. Use a quote-aware field splitter.
- **Escaping is unverified.** Vendor convention is `\"` for an embedded quote,
  but no disc encountered so far has exercised it. Treat a quote inside a value
  as a case to test before trusting.
- Unknown prefixes and unknown attribute ids appear across versions. Ignore
  rather than error.

### Reader skeleton

```rust
use std::io::{BufRead, BufReader};
use std::process::{Command, Stdio};

let mut child = Command::new("makemkvcon")
    .args(["-r", "--progress=-same", "--cache=1", "info", "dev:/dev/sr0"])
    .stdout(Stdio::piped())
    .spawn()?;

let stdout = child.stdout.take().expect("piped");
for line in BufReader::new(stdout).lines().flatten() {
    let Some((tag, rest)) = line.split_once(':') else { continue };
    match tag {
        "PRGV" => {
            let v: Vec<u64> = rest.split(',').filter_map(|s| s.parse().ok()).collect();
            if let [cur, tot, max] = v[..] {
                let pct = |x: u64| x as f64 * 100.0 / max as f64;   // max == 65536
                report(pct(cur), pct(tot));
            }
        }
        "PRGT" | "PRGC" => { /* code,id,"name" */ }
        "TCOUNT" => { /* title count — success gate */ }
        "TINFO"  => { /* title,id,code,"value" */ }
        "SINFO"  => { /* title,stream,id,code,"value" */ }
        "DRV"    => { /* index,state,flags,_,"drive","disc","device" */ }
        "MSG"    => { /* code,flags,count,"text",... — switch on code */ }
        _ => {}
    }
}
let _ = child.wait()?;   // exit status is NOT a success signal — see above
```

---

## Unverified — test before relying on

These could not be settled without a longer-running job or an unusual disc:

1. **`PRGV` emission cadence** during a multi-hour backup. Unknown whether
   updates are smooth or bursty.
2. **stdout buffering.** MakeMKV's stdout is a pipe here, not a TTY. If progress
   arrives in bursts, that is libc block buffering; `stdbuf -oL` is the usual
   remedy. Not observed either way yet.
3. **Quote escaping** inside string fields (above).
4. **Behaviour on mid-job disc eject or I/O error** — what is emitted, and
   whether the process exits at all.
5. **Concurrency.** Whether two `makemkvcon` processes may target two drives
   simultaneously. `PLAN.md` assumes multiple drives, so this matters and is
   worth an early experiment.

## Worked example

Scanning *Spider-Man: Across The Spider-Verse* on the LG BU40N produced 2092
lines and `TCOUNT:21`. Title 1 was the feature — `2:20:05`, `31506235392` bytes,
16 chapters, from `00001.mpls`. The remaining 20 titles were extras of 2–15
minutes. MakeMKV auto-skipped sub-120-second clips and duplicate playlists
(`00883.mpls is equal to title 00881.mpls`).

Startup reported `Using direct disc access mode` (LibreDrive) and loaded a
content hash table to verify M2TS integrity.
