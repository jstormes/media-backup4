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
| Verified drives | Two Pioneer `BD-RW BDR-212D` (firmware 1.02) on SATA, both reporting **"Using direct disc access mode"** — LibreDrive active, bypassing the drive's AACS. Previously verified on an LG `BD-RE BU40N` (FR07) over USB. **One of the two (`/dev/sr1`, `ata4`) failed at the ATA layer on 2026-09-08 and the kernel disabled it at 08:59** — every INQUIRY since returns `DID_BAD_TARGET`, so MakeMKV lists three drives, not four. A drive fault, not a MakeMKV one; see `docs/PLAN.md`. |

Rebuilding it: `install-makemkv.sh` in the repo root does the whole job --
build dependencies, both tarballs with `PREFIX=/usr/local`, the EULA prompt,
and a link check on the installed binary. Both halves default to `/usr`, so
the prefix has to be passed explicitly to each.

Do not rely on the tarballs living in a Claude Code scratchpad; those are
session-scoped and disappear. The script looks in `~/Downloads`, its own
directory and `--src-dir` before falling back to downloading from the vendor,
and verifies SHA-256 for the version it knows.

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
