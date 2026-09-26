# MakeMKV integration

How this project drives MakeMKV to read and back up discs.

Everything here was verified against a real install and a real Blu-ray on
2026-08-23 — not inferred from vendor documentation. The vendor's own developer
page (`makemkv.com/developers`) was unreachable at the time of writing, so these
docs are derived from the MakeMKV source, the shipped binary, and observed
output. Where something is *unverified*, it says so explicitly.

## Documents

| File | What it covers |
|---|---|
| [`robot-mode.md`](robot-mode.md) | **Start here.** The `makemkvcon -r` command set and line protocol. This is the integration path the project uses. |
| [`attribute-ids.md`](attribute-ids.md) | The numeric attribute IDs in `CINFO`/`TINFO`/`SINFO` lines, plus drive states and stream flags. |
| [`engine-protocol.md`](engine-protocol.md) | The `guiserver` shared-memory protocol — a richer alternative if robot mode ever becomes insufficient. |
| [`message-codes.md`](message-codes.md) | The `MSG` code table: where it lives on disk, how to decode any code, and the codes this project acts on. |
| [`track-selection.md`](track-selection.md) | Which audio, subtitle and closed-caption tracks end up in the output, why `--profile` cannot change it, and the one setting that can. |
| [`playlist-obfuscation.md`](playlist-obfuscation.md) | When playlist metadata lies: decoy playlists that hide the feature among hundreds of permutations, and declared durations that fail good backups. How to recognise each, which signals are worthless, and how to rip an obfuscated disc by hand. |
| [`test-suite.md`](test-suite.md) | Comprehensive test plan with verified invocations, output examples, error cases, and a phased test sequence for the AI agent. |

## Decision: use robot mode

`makemkvcon -r` is the integration surface for this project.

**Why.** `backup` and `mkv` are single commands; progress and status arrive as
line-oriented text that Rust can read with `BufReader::lines()`. No FFI, no
`unsafe`, no struct marshalling. It is the interface MakeMKV documents for third
parties, so it is the least likely to break across versions.

**The alternative and why not.** MakeMKV's own GUI does *not* use robot mode. It
spawns the engine as a co-process and speaks a shared-memory protocol
(`guiserver`) that offers structured data, per-title selection and mid-job
cancellation. That is a genuinely bigger lift — hand-marshalling a 64 KB struct
across a semaphore protocol with no vendor documentation. See
[`engine-protocol.md`](engine-protocol.md). Reach for it only against a concrete
limitation, not speculatively.

## Architecture

`makemkvcon` is the engine and the only closed-source component. Everything else
that ships in the `-oss` tarball — the Qt GUI, `libmmbd` — is a *client* of it.

There is **no library to link against.** Every integration path drives a
process.

```
your Rust code
      │  spawn + parse stdout            ← this project
      ▼
  makemkvcon -r   (closed engine)
      │  links
      ▼
  libmakemkv.so.1, libdriveio.so.0       ← built from the -oss tarball
```

Consequence worth knowing: **the `-bin` tarball cannot run on its own.**
`makemkvcon` links `libmakemkv.so.1` and `libdriveio.so.0`, which only exist
after the `-oss` half is compiled. `oss` and `bin` must be the same version.

## Install state on the target system

Built from source and installed to `/usr/local` (not `/usr`, to stay clear of
distro-managed files). `/usr/local/bin` is in MakeMKV's own engine search path,
so the GUI locates `makemkvcon` correctly.

```
/usr/local/bin/    makemkvcon  makemkv (GUI)  sdftool→makemkvcon  mmccextr  mmgplsrv
/usr/local/lib/    libmakemkv.so.1  libdriveio.so.0  libmmbd.so.0
/usr/local/share/  MakeMKV/{appdata.tar,blues.jar,blues.policy}
~/.MakeMKV/        settings.conf + AACS key cache
```

| | |
|---|---|
| Version | 1.18.4. The vendor serves only the current release, so 1.18.3 can no longer be downloaded. The licence is permanent and version independent, and registers 1.18.4 unchanged. |
| Licence | Purchased permanent key (`app_Key` starts with `M-`, not the free rotating `T-` beta key). Does not expire. |
| Build notes | Compiles clean against **ffmpeg 8 / libavcodec 62 with no patch**. Only **Qt5** is supported — 1.18.x's `configure` knows nothing about Qt6. |
| Verified drives | Two Pioneer `BD-RW BDR-212D` (firmware 1.02) on SATA, both reporting **"Using direct disc access mode"**. That message alone does not establish LibreDrive — see below. Standard Blu-ray is verified; **UHD is not — a UHD disc was tried on a Pioneer and it too needs LibreDrive firmware.** **One of the two (`/dev/sr1`, `ata4`) failed at the ATA layer on 2026-09-08 and the kernel disabled it at 08:59** — every INQUIRY since returns `DID_BAD_TARGET`, so MakeMKV lists three drives, not four. A drive fault, not a MakeMKV one; see `docs/PLAN.md`. |
| BU40N | LG `BD-RE BU40N` over USB (Initio INIC-1618L bridge, `13fd:0840`). **Flashed from FR07 to 1.03 and now reads UHD** — see below. On FR07 it read standard Blu-ray and failed UHD with `MSG:3346`. |
| WH16NS60 | LG `BD-RE WH16NS60` (firmware 1.02) over USB (Prolific USB-SATA bridge, `067b:2773`). **Reads UHD** — see below. |

Rebuilding it: `install-makemkv.sh` in the repo root does the whole job --
build dependencies, both tarballs with `PREFIX=/usr/local`, the EULA prompt,
and a link check on the installed binary. Both halves default to `/usr`, so
the prefix has to be passed explicitly to each.

Do not rely on the tarballs living in a Claude Code scratchpad; those are
session-scoped and disappear. The script looks in `~/Downloads`, its own
directory and `--src-dir` before falling back to downloading from the vendor,
and verifies SHA-256 for the version it knows.

## "Direct disc access" is not LibreDrive

These are two different things, and an earlier version of this document treated
them as one. The distinction decides whether a disc can be ripped at all.

| Message | Means |
|---|---|
| `MSG:3007` `Using direct disc access mode` | MakeMKV is talking to the drive itself rather than going through the OS filesystem layer. Says **nothing** about decryption. |
| `MSG:3346` `LibreDrive compatible drive is required to open this disc` | The drive lacks LibreDrive, so AACS 2.0 (UHD) content cannot be decrypted. |
| `MSG:1011` `Using LibreDrive mode (v06.3 id=…)` | **The positive signal.** LibreDrive is active, and UHD decrypts. Emitted before `3007`, on every run, whatever the disc. |

Look for `1011`, not for the absence of `3346`: `3346` only appears once a UHD
disc is actually opened, so a drive with no disc in it, or a standard Blu-ray,
says nothing either way.

The BU40N on FR07 emits `3007` and `3346`. It handles standard Blu-ray, and fails on UHD:

```
MSG:3007,0,0,"Using direct disc access mode"
MSG:5085,0,0,"Loaded content hash table, will verify integrity of M2TS files."
MSG:3346,...,"LibreDrive compatible drive is required to open this disc - video can't be decrypted."
MSG:5010,0,0,"Failed to open disc"
```

Reproduced on *Project Hail Mary* (UHD, BD-100, 91.5 GiB). MakeMKV identifies
the disc correctly and downloads its AACS 2.0 key block to
`~/.MakeMKV/MKB20_v82_Project_Hail_Mary_01D3.tgz` — identification is not the
failure; decryption is.

The earlier FR07 verification was done against `SPIDER_MAN_ACROSS_SPIDER_VERSE`,
which did not exercise AACS 2.0, so the limit never surfaced.

### Telling UHD from standard Blu-ray before you try

The BDMV version in the first 8 bytes of `BDMV/index.bdmv`:

```
$ head -c 8 /path/to/mount/BDMV/index.bdmv
INDX0300     ← UHD (AACS 2.0) — needs LibreDrive
INDX0200     ← standard BD / BD-3D — FR07 handles this
```

A sibling signal: UHD discs carry `AACS/ContentHash*.tbl` and a ~5 MB
`AACS/MKB_RO.inf`. Do **not** infer the main title from `.m2ts` file sizes on a
UHD disc — the sparse/overlapping allocation reports impossible values (on this
disc `00589.m2ts` claims 93 GB, larger than the disc). Use the playlists.

### UHD verified end to end (2026-09-25)

Two drives on p14 now read UHD, and both were proved with a full rip, not just
a scan. MakeMKV 1.18.3, each run through the app's own `mkv_argv` under the
`bwrap` drive sandbox, both drives copying at the same time:

| | WH16NS60 1.02 (`/dev/sr0`) | BU40N 1.03 (`/dev/sr1`) |
|---|---|---|
| Disc | *Project Hail Mary*, title 0 (`01199.mpls`) | *The Accountant 2*, title 1 (`00800.mpls`) |
| LibreDrive | `MSG:1011` v06.3 | `MSG:1011` v06.3 |
| Verdict | `MSG:5036` "1 titles saved", exit 0, no errors | same |
| Duration vs scan | 2:36:31.8 vs 2:36:31 | 2:12:24.7 vs 2:12:24 |
| Chapters vs scan | 24 vs 24 | 15 vs 15 |
| MKV vs scan size | 89.4 GB vs 93.3 GB (96%) | 87.8 GB vs 93.2 GB (94%) |
| Video | HEVC 3840x2160, HDR10 (PQ) | HEVC 3840x2160, HDR10 + Dolby Vision |
| Throughput | 13–26 MB/s; 20:32–21:33, ~61 min | 20–26 MB/s; 20:26–21:29, ~63 min |

Decryption was checked in the output, not inferred from the verdict: frames
sampled deep into each file (1:40:00 and 0:50:00) decode, with their HDR
mastering metadata intact. The MKV coming in 4–6% under the scan's size is the
M2TS packet overhead the remux drops, not missing content.

The BU40N is the drive that refused *Project Hail Mary* with `MSG:3346` on
FR07 earlier the same day; on 1.03 it emits `1011` and ripped a different UHD
disc clean, so the flash worked. It has not been re-run against *Hail Mary*
itself. Whether the WH16NS60's 1.02 is stock or patched is not established,
only that MakeMKV runs it in LibreDrive mode.

**One failure on the way, and it was the USB bridge.** The first *Hail Mary*
attempt stalled in the startup scan and never wrote a byte. The kernel log
shows the WH16NS60's Prolific bridge resetting twice a minute in
(`usb 3-1: reset high-speed USB device`, 20:27:22 and 20:27:59). After a
drive power-cycle the same disc ripped clean with no resets. A UHD scan that
sits silent on this drive: check `journalctl -k` for bridge resets before
blaming the disc.

**Every UHD disc tried so far authors its feature twice.** Both discs list the
film as a playlist *and* as its bare `.m2ts`, with the same segments map
(`589` and `14`). This project copies every title, so a whole-disc run of
either writes ~190 GB where the film is ~88 GB. Not decoys —
`selection.obfuscation()` finds nothing on either — just the Hancock pattern
at UHD sizes.

### Firmware history

The Pioneer BDR-212D on 1.02 was tried against a UHD disc and needs flashing;
1.02 looked like a plausible LibreDrive revision and was not one. `MSG:3007`
was never evidence either way.

The BU40N was flashed first, because it was the cheaper drive to lose: on
Windows with Marty's GUI SDF tool, which bundles the payload and firmware,
target **BU40N 1.03MK**, which per the flashing guide goes on from any
existing firmware, FR07 included. The `Enc` setting is the one that has to be
right. The drive now reports `1.03` in `DRV` and `lsblk`.

Flashing from Linux was considered and rejected. It needs three things this
machine does not have: `sdf.bin` (mandatory, absent — there is no
`~/.libdriveio/`), the 1.03MK image, and **MakeMKV 1.17.7 or older** — the
guide says newer versions do not flash correctly on Linux, and 1.18.3 is what
is installed. `rawflash` is not a command in any installed binary; it comes
from `sdf.bin`, which is why `sdftool -d /dev/sr0 help` hangs silently with
nothing to enumerate. That hang says nothing about the USB bridge; the flashed
BU40N rips UHD through that same Initio bridge.

Bricking a BU40N is often unrecoverable and the drives are out of production.

Source for the procedure is the forum's [Ultimate UHD Drives Flashing
Guide](https://forum.makemkv.com/forum/viewtopic.php?t=19634), not vendor
documentation.

## Environment requirements

- The user must be able to read the optical device — **both nodes**. MakeMKV
  finds and drives a disc through the SCSI generic node (`/dev/sgN`), not
  `/dev/srN`, and skips any drive whose `sg` node it cannot open. On the target
  both are satisfied by the `cdrom` group plus a udev ACL.
- `bwrap` (bubblewrap) is wanted but not required. Each run is wrapped so it
  can open only its own drive's `sg` node; without it every run probes every
  drive on the machine, including one that is mid-rip. See
  [`robot-mode.md`](robot-mode.md#every-run-probes-every-drive) and
  `src/media_backup/makemkv/isolation.py`.
- A JRE must be present for BD-J discs. MakeMKV logs which one it picked:
  `Using Java runtime from /usr/lib/jvm/java-25-openjdk-amd64/bin/java`.
- `MAKEMKVCON` env var overrides the engine path if you ever need to pin a build.

## Licensing constraints on this project

| Component | Licence | Consequence |
|---|---|---|
| `aproxy.h`, `apdefs.h` | **Public domain** (explicit in the headers) | The attribute IDs and protocol constants in these docs can be freely reproduced and bound against. |
| `libmmbd` | LGPL 2.1+ | Fine to link; obligations if distributed. |
| `makemkvcon` | Proprietary, EULA, requires a key | **Do not bundle or redistribute it.** Treat it as a pre-installed system dependency — which is what `PLAN.md` already assumes. |

Writing and publishing a Rust wrapper is fine. Shipping the engine with it is
not.
